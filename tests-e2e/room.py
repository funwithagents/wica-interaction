"""The room (specs/e2e.md, "The room"): everything the app's mic hears, played into the cable from a
process of its own — the person's voice and, given an echo device, the app's own voice coming back.

The person's voice is a phonon engine whose audio is mixed in rather than played: it speaks each
line read on stdin and answers `done` once the line's last sample has gone out. The echo is the app's
speaker recorded from the echo device and played back 50 ms later, 10 dB quieter, for the whole run.

A process of its own because, on macOS, a process that plays into the cable stalls its own capture
of the cable — the app's capture, when the room shares the app's process. The person is not inside
the app either.

    python room.py <cable device> <voice> [<echo device>]
"""

from __future__ import annotations

import asyncio
import sys
import threading

import numpy as np
import samplerate
import sounddevice as sd
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine

RATE = 48000
BLOCK = 480  # 10 ms
ECHO_DELAY_S = 0.050
ECHO_GAIN = 10 ** (-10 / 20)
# The echo's backlog past its delay, beyond which the two devices' clocks have drifted apart: it is
# cut back to the delay rather than let the echo fall further behind the app's voice.
ECHO_SLACK_S = 0.100


class Mixer:
    """The cable's signal: the person's queued voice plus the delayed echo, block by block."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voice = np.zeros(0, np.float32)
        self._echo = np.zeros(int(ECHO_DELAY_S * RATE), np.float32)
        self.played = threading.Event()
        self.played.set()

    def speak(self, samples: np.ndarray) -> None:
        with self._lock:
            self._voice = np.concatenate([self._voice, samples])
            self.played.clear()

    def heard(self, block: np.ndarray) -> None:
        """A block recorded from the echo device."""
        with self._lock:
            self._echo = np.concatenate([self._echo, block * ECHO_GAIN])
            if len(self._echo) > (ECHO_DELAY_S + ECHO_SLACK_S) * RATE:
                self._echo = self._echo[-int(ECHO_DELAY_S * RATE) :]

    def _take(self, buffer: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        out = np.zeros(n, np.float32)
        k = min(n, len(buffer))
        out[:k] = buffer[:k]
        return out, buffer[k:]

    def block(self, n: int) -> np.ndarray:
        with self._lock:
            voice, self._voice = self._take(self._voice, n)
            echo, self._echo = self._take(self._echo, n)
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

    def play(outdata: np.ndarray, frames: int, _time: object, _status: object) -> None:
        outdata[:, 0] = mixer.block(frames)

    def record(
        indata: np.ndarray, _frames: int, _time: object, _status: object
    ) -> None:
        mixer.heard(indata[:, 0].copy())

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


if __name__ == "__main__":
    asyncio.run(
        main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    )
