"""Offline echo-canceller bench (analysis/202611101625_echo-canceller.md, section 6).

Not shipped and not tested: a working tool for the analysis. It replays any ``FrameProcessor``
(``process(near, far) -> out``, 10 ms frames of s16 mono @16 kHz) over recorded or synthetic audio
and measures what it removed and what it let through.

    uv run python analysis/echo_bench.py synth <dir>              # build the synthetic sets
    uv run python analysis/echo_bench.py run <file.npz>... [-c aec3,none]
    uv run --with livekit python analysis/echo_bench.py run <dir>/*.npz -c aec3,livekit

Two kinds of input, told apart by their arrays:

- **A recording** (``near``, ``far``, ``out``): the end-to-end tier's ``audio.npz``. The echo is not
  known, so it is predicted from the reference, shifted by the lag measured on the energy envelopes
  and scaled by a measured gain; windows are classed against that prediction.
- **A synthetic set** (``near``, ``far``, ``echo``, ``person``): built here from the tier's phonon
  voices (the app ``Micah``, the person ``Harper``) on a scripted timeline, in a simulated room. The
  echo and the person's clean voice are known exactly, so near-end preservation is measured.

Metrics, per 100 ms window: echo removed (ERLE, ``near − out`` in dB) over echo-only windows; leak
time, the echo-only windows whose output stays above ``LEAK_DBFS``; convergence, from the first echo
to the start of ``SETTLED_S`` without a leak; near-end, ``out − person`` in dB where the person
talks (double-talk, and alone); CPU per frame.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

RATE = 16000
FRAME = 160  # 10 ms
WINDOW = 1600  # 100 ms
ACTIVE_DBFS = -55.0  # a window where the echo (or its prediction) is present
SILENT_DBFS = -75.0  # a window where the person is silent
LEAK_DBFS = (
    -45.0
)  # an echo-only window whose output is above this leaked (calibrate on CI's 1.4 s)
SETTLED_S = 2.0

# ---------------------------------------------------------------------------------------------
# Candidates: each a factory returning a FrameProcessor.


class Passthrough:
    def process(self, near: bytes, far: bytes) -> bytes:
        return near


def _aec3() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from wica_interaction.computer.audio_processing import WebRtcAudioProcessor

    return WebRtcAudioProcessor(echo_cancellation=True, stream_delay_ms=50)


class LiveKitApm:
    """Candidate B: LiveKit's build of WebRTC's module (``uv run --with livekit``)."""

    def __init__(self) -> None:
        from livekit import rtc

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=False,
            high_pass_filter=True,
            auto_gain_control=False,
        )

    def _frame(self, pcm: bytes) -> Any:
        return self._rtc.AudioFrame(pcm, RATE, 1, FRAME)

    def process(self, near: bytes, far: bytes) -> bytes:
        reverse = self._frame(far)
        self._apm.process_reverse_stream(reverse)
        self._apm.set_stream_delay_ms(50)
        frame = self._frame(near)
        self._apm.process_stream(frame)
        return bytes(frame.data.cast("B"))


CANDIDATES: dict[str, Callable[[], Any]] = {
    "none": Passthrough,
    "aec3": _aec3,
    "livekit": LiveKitApm,
}

# ---------------------------------------------------------------------------------------------
# Replay and metrics.


def replay(proc: Any, near: np.ndarray, far: np.ndarray) -> tuple[np.ndarray, float]:
    """The processor's output over whole frames, and its mean time per frame in ms."""
    n = len(near) // FRAME * FRAME
    out = np.zeros(n, np.int16)
    start = time.perf_counter()
    for i in range(0, n, FRAME):
        cleaned = proc.process(
            near[i : i + FRAME].tobytes(), far[i : i + FRAME].tobytes()
        )
        out[i : i + FRAME] = np.frombuffer(cleaned, np.int16)
    return out, (time.perf_counter() - start) * 1000 / (n // FRAME)


def window_dbfs(x: np.ndarray) -> np.ndarray:
    n = len(x) // WINDOW * WINDOW
    w = x[:n].astype(np.float64).reshape(-1, WINDOW) / 32768.0
    return 10 * np.log10((w**2).mean(axis=1) + 1e-12)


def envelope_lag(near: np.ndarray, far: np.ndarray, max_s: float = 3.0) -> int:
    """The echo's lag (reference → mic) in samples, from the 10 ms log-energy envelopes."""

    def env(x: np.ndarray) -> np.ndarray:
        n = len(x) // FRAME * FRAME
        e = np.log10(
            (x[:n].astype(np.float64).reshape(-1, FRAME) ** 2).mean(axis=1) + 1.0
        )
        return e - e.mean()

    a, b = env(near), env(far)
    m = min(len(a), len(b))
    a, b = a[:m], b[:m]
    span = int(max_s * RATE / FRAME)
    lags = range(-span, span + 1)
    scores = [
        float(np.dot(a[max(k, 0) : m + min(k, 0)], b[max(-k, 0) : m - max(k, 0)]))
        for k in lags
    ]
    return lags[int(np.argmax(scores))] * FRAME


def predicted_echo(near: np.ndarray, far: np.ndarray) -> tuple[np.ndarray, int, float]:
    """The reference shifted by the measured lag and scaled to the echo's level (recordings)."""
    lag = envelope_lag(near, far)
    shifted = np.zeros_like(far, dtype=np.float64)
    if lag >= 0:
        shifted[lag:] = far[: len(far) - lag]
    else:
        shifted[:lag] = far[-lag:]
    near_db, pred_db = window_dbfs(near), window_dbfs(shifted.astype(np.int16))
    active = pred_db > -40
    # The lower quartile of near − reference over active windows: the echo alone, not the person.
    gain_db = (
        float(np.percentile(near_db[active] - pred_db[active], 25))
        if active.any()
        else -10.0
    )
    return shifted * 10 ** (gain_db / 20), lag, gain_db


@dataclass
class Result:
    name: str
    erle_db: float
    leak_s: float
    convergence_s: float
    double_talk_db: float
    double_talk_cut: float
    person_alone_db: float
    ms_per_frame: float


def measure(
    name: str,
    near: np.ndarray,
    far: np.ndarray,
    out: np.ndarray,
    echo: np.ndarray,
    person: np.ndarray | None,
    ms_per_frame: float,
) -> Result:
    n = min(len(near), len(out), len(echo)) // WINDOW * WINDOW
    far_on = window_dbfs(far[:n]) > ACTIVE_DBFS
    near_db, out_db = window_dbfs(near[:n]), window_dbfs(out[:n])
    echo_db = window_dbfs(np.clip(echo[:n], -32768, 32767).astype(np.int16))
    echo_on = echo_db > ACTIVE_DBFS
    if person is not None:
        person_db = window_dbfs(person[:n])
        person_on = person_db > ACTIVE_DBFS
        echo_only = echo_on & (person_db < SILENT_DBFS)
    else:  # a recording: classed against the predicted echo
        person_db = None
        person_on = near_db > echo_db + 6
        echo_only = echo_on & (near_db < echo_db + 3)
    leaks = echo_only & (out_db > LEAK_DBFS)

    # The first echo-only window after which the next SETTLED_S of echo-only windows hold.
    convergence = float("nan")
    idx = np.flatnonzero(echo_only)
    settle = int(SETTLED_S * RATE / WINDOW)
    for j, i in enumerate(idx[: max(len(idx) - settle, 0)]):
        if not leaks[idx[j : j + settle]].any():
            convergence = (i - idx[0]) * WINDOW / RATE
            break

    def med(x: np.ndarray) -> float:
        return float(np.median(x)) if len(x) else float("nan")

    dt_db = cut = alone_db = float("nan")
    if person_db is not None:
        # Double-talk: the person over the app's voice, echo or not (a headset has none).
        dt = person_on & (echo_on | far_on)
        alone = person_on & ~echo_on & ~far_on
        dt_db = med(out_db[dt] - person_db[dt])
        cut = (
            float(((out_db[dt] - person_db[dt]) < -6).mean())
            if dt.any()
            else float("nan")
        )
        alone_db = med(out_db[alone] - person_db[alone])
    return Result(
        name=name,
        erle_db=med(near_db[echo_only] - out_db[echo_only]),
        leak_s=float(leaks.sum()) * WINDOW / RATE,
        convergence_s=convergence,
        double_talk_db=dt_db,
        double_talk_cut=cut,
        person_alone_db=alone_db,
        ms_per_frame=ms_per_frame,
    )


def bench(path: Path, candidates: list[str]) -> list[Result]:
    d = np.load(path)
    near, far = d["near"].astype(np.int16), d["far"].astype(np.int16)
    if "echo" in d:
        echo, person = d["echo"].astype(np.float64), d["person"].astype(np.int16)
    else:
        echo, lag, gain = predicted_echo(near, far)
        person = None
        print(f"  {path.name}: echo lag {lag / RATE * 1000:.0f} ms, gain {gain:.1f} dB")
    results = []
    if "out" in d:
        results.append(
            measure("recorded", near, far, d["out"].astype(np.int16), echo, person, 0.0)
        )
    for c in candidates:
        out, ms = replay(CANDIDATES[c](), near, far)
        results.append(measure(c, near, far, out, echo, person, ms))
    return results


def show(path: Path, results: list[Result]) -> None:
    print(f"\n### {path.name}\n")
    print(
        "| candidate | ERLE dB | leak s | converge s | double-talk dB | DT cut >6 dB | person alone dB | ms/frame |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r.name} | {r.erle_db:.1f} | {r.leak_s:.1f} | {r.convergence_s:.1f} | "
            f"{r.double_talk_db:.1f} | {r.double_talk_cut:.0%} | {r.person_alone_db:.1f} | {r.ms_per_frame:.3f} |"
        )


# ---------------------------------------------------------------------------------------------
# Synthetic sets.

APP_LINES = [
    (
        "Once upon a time, a lighthouse keeper lived alone on a rock far out at sea. Every night he "
        "climbed the hundred steps to light the lamp, and every morning he wrote one line in his logbook."
    ),
    "Stopped.",
    "It is a quarter past four in the afternoon.",
    "Sure. I have noted that you want to buy milk, bread and a dozen eggs on the way home tonight.",
    "You are welcome.",
]
PERSON_LINES = [
    "Tell me a story about a lighthouse.",
    "Stop.",
    "What time is it?",
    "Remember to buy milk, bread and eggs.",
    "I was saying that the eggs should be free range, if they have them.",
    "Thank you.",
]


def _timeline(early: bool) -> list[tuple[str, int, float]]:
    """(who, line, start s): the conversation every synthetic set plays out.

    The person asks, the app tells a story, the person stops it (1 s in when ``early``, else 4 s),
    short replies follow, then the person talks over a whole reply (continuous double-talk)."""
    return [
        ("person", 0, 0.5),
        ("app", 0, 3.0),
        ("person", 1, 4.0 if early else 7.0),
        ("app", 1, 9.0),  # short reply right after the barge-in
        ("person", 2, 10.5),
        ("app", 2, 12.5),
        ("person", 3, 16.0),
        ("app", 3, 19.0),
        ("person", 4, 20.0),  # talks through the reply
        ("app", 4, 26.0),
        ("person", 5, 25.0),
    ]


async def _speak(voice: str, lines: list[str]) -> list[np.ndarray]:
    import samplerate
    from tts_engine.config import TTSEngineConfig
    from tts_engine.engine import TTSEngine

    class Capture:
        def __init__(self) -> None:
            self.chunks: list[bytes] = []

        def feed(self, chunk: bytes) -> None:
            self.chunks.append(chunk)

        def drain(self) -> None:
            pass

    sink = Capture()
    config = TTSEngineConfig.from_dict({"module": {"type": "phonon", "voice": voice}})
    out = []
    async with TTSEngine(config, sink=sink) as engine:
        for line in lines:
            sink.chunks = []
            await engine.say(line)
            pcm = (
                np.frombuffer(b"".join(sink.chunks), np.int16).astype(np.float32)
                / 32768
            )
            y = samplerate.resample(pcm, RATE / engine.sample_rate, "sinc_best")
            out.append(np.clip(y * 32768, -32768, 32767).astype(np.int16))
    return out


def _room_ir(rt60_s: float, rng: np.random.Generator) -> np.ndarray:
    """A synthetic impulse response: direct path plus exponentially decaying noise (no dataset)."""
    n = int(rt60_s * RATE)
    t = np.arange(n) / RATE
    tail = rng.normal(0, 1, n) * np.exp(-6.9 * t / rt60_s) * 0.3
    tail[0] = 1.0
    return tail / np.sqrt((tail**2).sum())


@dataclass(frozen=True)
class Room:
    name: str
    gain_db: float | None  # None: no echo (a headset)
    delay_ms: float = 170.0
    jump_ms: float | None = None  # the delay after the midpoint
    drift_ppm: float = (
        0.0  # the delay growing with time: two clocks apart, as measured in CI
    )
    rt60_s: float | None = None
    noise_dbfs: float | None = None
    early: bool = False


ROOMS = [
    Room("ci", -10.0),
    Room("loud", -3.0),
    Room("quiet", -20.0),
    Room("reverb", -10.0, rt60_s=0.4),
    Room("jump", -10.0, jump_ms=260.0),
    Room("drift", -10.0, drift_ppm=250.0),
    Room("drift1k", -10.0, drift_ppm=1000.0),
    Room("early", -10.0, early=True),
    Room("noise", -10.0, noise_dbfs=-50.0),
    Room("headset", None),
]


def synth(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    app = asyncio.run(_speak("Micah", APP_LINES))
    person = asyncio.run(_speak("Harper", PERSON_LINES))
    rng = np.random.default_rng(0)
    for room in ROOMS:
        timeline = _timeline(room.early)
        end = (
            max(
                s + len((app if w == "app" else person)[i]) / RATE
                for w, i, s in timeline
            )
            + 2
        )
        n = int(end * RATE) // WINDOW * WINDOW
        far = np.zeros(n, np.float64)
        voice = np.zeros(n, np.float64)
        app_starts = sorted(s for w, _, s in timeline if w == "app")
        for who, i, start in timeline:
            src = app[i] if who == "app" else person[i]
            k = int(start * RATE)
            if (
                who == "app"
            ):  # a reply stops where the next one starts, as a stopped story does
                later = [s for s in app_starts if s > start]
                if later:
                    src = src[: int((later[0] - start - 0.3) * RATE)]
            (far if who == "app" else voice)[k : k + len(src)] += src[: n - k]
        echo = np.zeros(n, np.float64)
        if room.gain_db is not None:
            path = far
            if room.rt60_s is not None:
                path = np.convolve(far, _room_ir(room.rt60_s, rng))[:n]
            g = 10 ** (room.gain_db / 20)
            d = int(room.delay_ms * RATE / 1000)
            if room.drift_ppm:
                t = np.arange(n, dtype=np.float64)
                src = (
                    t * (1 - room.drift_ppm * 1e-6) - d
                )  # read further and further behind
                echo = np.interp(src, t, path, left=0.0) * g
            else:
                echo[d:] = path[: n - d] * g
            if room.jump_ms is not None:
                mid = n // 2
                d2 = int(room.jump_ms * RATE / 1000)
                echo[mid:] = 0
                echo[mid:] = path[mid - d2 : n - d2] * g
        near = echo + voice
        if room.noise_dbfs is not None:
            near += rng.normal(0, 32768 * 10 ** (room.noise_dbfs / 20), n)

        def s16(x: np.ndarray) -> np.ndarray:
            return np.clip(np.round(x), -32768, 32767).astype(np.int16)

        np.savez(
            out_dir / f"{room.name}.npz",
            near=s16(near),
            far=s16(far),
            echo=echo.astype(np.float32),
            person=s16(voice),
        )
        print(f"{room.name}: {n / RATE:.1f} s")


def track(path: Path, span_s: float = 2.0, max_ms: float = 600.0) -> None:
    """The echo's delay through a recording, window by window, and where its leaks sit.

    The delay is the peak of the waveform cross-correlation of ``near`` with ``far`` over
    0..``max_ms``, in windows of ``span_s`` where the reference plays; a move between windows is the
    echo path changing (the room's drift correction, a device's buffering)."""
    d = np.load(path)
    near, far = d["near"].astype(np.float64), d["far"].astype(np.float64)
    n, step, max_lag = len(near), int(span_s * RATE), int(max_ms * RATE / 1000)
    print(f"\n### {path.name}: echo delay per {span_s:.0f} s window\n")
    for start in range(0, n - step - max_lag, step):
        f = far[start : start + step]
        if window_dbfs(f.astype(np.int16)).max() < -40:
            continue
        m = near[start : start + step + max_lag]
        size = 1 << int(np.ceil(np.log2(len(m) + len(f))))
        xc = np.fft.irfft(np.fft.rfft(m, size) * np.conj(np.fft.rfft(f, size)), size)
        xc = xc[: max_lag + 1]
        k = int(np.argmax(np.abs(xc)))
        sharp = float(np.abs(xc[k]) / (np.median(np.abs(xc)) + 1e-9))
        print(
            f"  {start / RATE:7.1f} s  {k / RATE * 1000:6.1f} ms  (peak/median {sharp:5.1f})"
        )
    if "out" in d:
        echo, _, _ = predicted_echo(d["near"], d["far"])
        near_db, out_db = window_dbfs(d["near"]), window_dbfs(d["out"])
        m = min(len(near_db), len(out_db))
        echo_db = window_dbfs(np.clip(echo, -32768, 32767).astype(np.int16))[:m]
        leaks = (
            (echo_db > ACTIVE_DBFS)
            & (near_db[:m] < echo_db + 3)
            & (out_db[:m] > LEAK_DBFS)
        )
        times = np.flatnonzero(leaks) * WINDOW / RATE
        print(
            f"\n  recorded leaks ({len(times) * WINDOW / RATE:.1f} s): {np.round(times, 1).tolist()}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth", help="build the synthetic sets")
    s.add_argument("dir", type=Path)
    r = sub.add_parser(
        "run", help="replay candidates over recordings or synthetic sets"
    )
    r.add_argument("files", type=Path, nargs="+")
    r.add_argument("-c", "--candidates", default="none,aec3")
    t = sub.add_parser(
        "track", help="the echo's delay through recordings, and their leaks"
    )
    t.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    if args.cmd == "synth":
        synth(args.dir)
    elif args.cmd == "track":
        for path in args.files:
            track(path)
    else:
        for path in args.files:
            show(path, bench(path, args.candidates.split(",")))


if __name__ == "__main__":
    main()
