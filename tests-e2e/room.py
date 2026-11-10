"""The room (specs/e2e.md, "The room"): everything the app's mic hears, played into the cable from a
process of its own — the person's voice and, given an echo device, the app's own voice coming back.

The person's voice is a phonon engine whose audio is mixed in rather than played: it speaks each
line read on stdin and answers `done` once the line's last sample has gone out. The echo is the app's
speaker recorded from the echo device and played back 50 ms later, 10 dB quieter, for the whole run,
the delay held against the drift between the two streams' clocks.

A process of its own because, on macOS, a process that plays into the cable stalls its own capture
of the cable — the app's capture, when the room shares the app's process. The person is not inside
the app either.

    python room.py <cable device> <voice> [<echo device>]

With the echo on and `WICA_E2E_ROOM_LOG` set to a path, the room logs its echo path block by block
and saves it there as `.npz` on exit (specs/e2e.md, "Evidence on failure"): `play` rows (time,
output latency, echo backlog, echo samples short so far) and `rec` rows (time, input latency, echo
backlog, the backlog's hard corrections so far), the times and latencies from PortAudio, in seconds, the backlogs in samples. A
change in the echo's delay is then told apart: the room's backlog, or a device's latency.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any

import numpy as np
import samplerate
import sounddevice as sd
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine

RATE = 48000
BLOCK = 480  # 10 ms
ECHO_DELAY_S = 0.050
ECHO_GAIN = 10 ** (-10 / 20)
# The echo's delay is held (specs/e2e.md, "The echo's delay holds"): the time from a sample's
# recording to its playback (PortAudio's ADC and DAC times), as it settles over the first second.
# Averaged over about a second, it is corrected one sample per block while more than HOLD_TOLERANCE
# off (the clocks' drift); averaged over a few tenths of a second, it is cut back at once when more
# than HOLD_JUMP over (a late block, the echo then bunched up). A shortfall is never padded at once:
# it is echo still on its way.
HOLD_SLOW = 0.01  # the averages' weights per block: about a second, and about 0.3 s
HOLD_FAST = 0.03
HOLD_SETTLE = 100  # blocks before the delay is locked: the streams' start-up settled
HOLD_TOLERANCE = int(0.0005 * RATE)
HOLD_JUMP = int(0.020 * RATE)


class Mixer:
    """The cable's signal: the person's queued voice plus the delayed echo, block by block."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voice = np.zeros(0, np.float32)
        self._echo = np.zeros(int(ECHO_DELAY_S * RATE), np.float32)
        self._newest: float | None = (
            None  # the ADC time just past the newest recorded sample
        )
        self._slow = self._fast = 0.0  # the echo's delay in samples, averaged
        self._blocks = 0
        self.target: float | None = None  # the delay held, locked once settled
        self.played = threading.Event()
        self.played.set()
        self.echo_short = (
            0  # echo samples the cable played as silence, the echo not yet recorded
        )
        self.jumps = 0  # the delay's hard corrections: cut back to the target

    def speak(self, samples: np.ndarray) -> None:
        with self._lock:
            self._voice = np.concatenate([self._voice, samples])
            self.played.clear()

    def heard(self, block: np.ndarray, adc_time: float) -> None:
        """A block recorded from the echo device, its first sample taken at *adc_time*."""
        with self._lock:
            self._echo = np.concatenate([self._echo, block * ECHO_GAIN])
            self._newest = adc_time + len(block) / RATE

    @property
    def echo_backlog(self) -> int:
        with self._lock:
            return len(self._echo)

    def _take(self, buffer: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        out = np.zeros(n, np.float32)
        k = min(n, len(buffer))
        out[:k] = buffer[:k]
        return out, buffer[k:]

    def _steps(self, n: int, dac_time: float) -> int:
        """How many echo samples the next *n* played take: *n*, one more or one fewer, as the
        delay is held; a cut is made here. Called under the lock."""
        if self._newest is None:
            return n
        delay = (dac_time - (self._newest - len(self._echo) / RATE)) * RATE
        if self._blocks == 0:
            self._slow = self._fast = delay
        self._slow += (delay - self._slow) * HOLD_SLOW
        self._fast += (delay - self._fast) * HOLD_FAST
        self._blocks += 1
        if self.target is None:
            if self._blocks >= HOLD_SETTLE:
                self.target = self._slow
            return n
        if self._fast - self.target > HOLD_JUMP:
            # The whole excess, as measured now: the average only detects it.
            k = max(int(delay - self.target), 0)
            self._echo = self._echo[k:]
            self._slow = self._fast = delay - k
            self.jumps += 1
            return n
        error = self._slow - self.target
        if error > HOLD_TOLERANCE:
            return n + 1  # one sample more of the echo per block: the delay shrinks
        if error < -HOLD_TOLERANCE:
            return n - 1
        return n

    def _held_echo(self, n: int, dac_time: float) -> np.ndarray:
        """The next *n* echo samples, its delay held. Called under the lock."""
        m = self._steps(n, dac_time)
        # The room's own step, so the averages never chase it.
        self._slow -= m - n
        self._fast -= m - n
        self.echo_short += max(m - len(self._echo), 0)
        echo, self._echo = self._take(self._echo, m)
        if m == n:
            return echo
        return np.interp(np.linspace(0, m - 1, n), np.arange(m), echo).astype(
            np.float32
        )

    def block(self, n: int, dac_time: float) -> np.ndarray:
        """The next *n* samples of the cable, the first of them played at *dac_time*."""
        with self._lock:
            voice, self._voice = self._take(self._voice, n)
            echo = self._held_echo(n, dac_time)
            if len(self._voice) == 0:
                self.played.set()
        return np.clip(voice + echo, -1.0, 1.0)


class VoiceSink:
    """The phonon engine's sink: each chunk resampled to the cable's rate and queued in the mixer."""

    def __init__(self, mixer: Mixer) -> None:
        self._mixer = mixer
        self._resampler = samplerate.Resampler("sinc_fastest", channels=1)
        self.rate = RATE

    def feed(self, chunk: bytes) -> None:
        samples = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0
        self._mixer.speak(self._resampler.process(samples, RATE / self.rate))

    def drain(self) -> None:
        pass


async def main(cable: str, voice: str, echo_device: str | None) -> None:
    mixer = Mixer()
    log_path = os.environ.get("WICA_E2E_ROOM_LOG") if echo_device else None
    play_log: list[tuple[float, float, int, int]] = []
    rec_log: list[tuple[float, float, int, int]] = []

    def play(outdata: np.ndarray, frames: int, time: Any, _status: object) -> None:
        if log_path:
            play_log.append(
                (
                    time.currentTime,
                    time.outputBufferDacTime - time.currentTime,
                    mixer.echo_backlog,
                    mixer.echo_short,
                )
            )
        outdata[:, 0] = mixer.block(frames, time.outputBufferDacTime)

    def record(indata: np.ndarray, _frames: int, time: Any, _status: object) -> None:
        mixer.heard(indata[:, 0].copy(), time.inputBufferAdcTime)
        if log_path:
            rec_log.append(
                (
                    time.currentTime,
                    time.currentTime - time.inputBufferAdcTime,
                    mixer.echo_backlog,
                    mixer.jumps,
                )
            )

    sink = VoiceSink(mixer)
    config = TTSEngineConfig.from_dict({"module": {"type": "phonon", "voice": voice}})
    async with TTSEngine(config, sink=sink) as engine:
        sink.rate = engine.sample_rate
        streams: list[sd.OutputStream | sd.InputStream] = [
            sd.OutputStream(
                device=cable,
                samplerate=RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCK,
                callback=play,
            )
        ]
        if echo_device is not None:
            streams.append(
                sd.InputStream(
                    device=echo_device,
                    samplerate=RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=BLOCK,
                    callback=record,
                )
            )
        for stream in streams:
            stream.start()
        try:
            print("ready", flush=True)
            for line in sys.stdin:
                await engine.say(line.strip())
                await asyncio.to_thread(mixer.played.wait)
                await asyncio.sleep(
                    BLOCK / RATE * 2
                )  # the last blocks leave the device
                print("done", flush=True)
        finally:
            for stream in streams:
                stream.stop()
                stream.close()
            if log_path:
                np.savez(log_path, play=np.array(play_log), rec=np.array(rec_log))


if __name__ == "__main__":
    asyncio.run(
        main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    )
