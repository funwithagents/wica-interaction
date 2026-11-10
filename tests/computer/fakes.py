"""Doubles for the computer embodiment's tests: the TTS engine, the coordinator, the webcam."""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from tts_engine.modules.base import TTSError


class FakeTTSEngine:
    """The slice of `TTSEngine` that `TTSTools` and the embodiment touch.

    Records each spoken text in `said` and the `sink` it was built with; `sample_rate` is what
    `bind_far_rate` is expected to receive. Set `raise_message` to make `say` raise `TTSError` and
    exercise the voice's non-raising contract.
    """

    sample_rate = 44100

    def __init__(self, *, sink: Any = None, raise_message: str | None = None) -> None:
        self.said: list[str] = []
        self.sink = sink
        self._raise_message = raise_message

    async def say(self, text: str) -> None:
        self.said.append(text)
        if self._raise_message is not None:
            raise TTSError(self._raise_message)


class FakeCoordinator:
    """The slice of `AudioProcessingCoordinator` the embodiment wires: the two endpoints it hands
    the engines, and the far-rate binding it records."""

    def __init__(self) -> None:
        self.sink = object()
        self.source = object()
        self.bound: list[int] = []

    def bind_far_rate(self, rate: int) -> None:
        self.bound.append(rate)


class FakeOpenCVSource:
    """The slice of `vision-modules`' `OpenCVSource` that `WebcamSource` drives.

    `frame` is what `read()` returns (settable mid-run); `raise_on_open` / `raise_on_read` make the
    corresponding call raise, the way a missing or unplugged device does. Counts opens and closes.
    """

    def __init__(self, frame: np.ndarray | None = None) -> None:
        self.frame: np.ndarray | None = (
            frame if frame is not None else np.zeros((4, 4, 3), dtype=np.uint8)
        )
        self.raise_on_open: str | None = None
        self.raise_on_read: str | None = None
        self.frames_per_open: int | None = None
        self.opens = 0
        self.closes = 0
        self.reads = 0
        self._since_open = 0

    def open(self) -> None:
        self.opens += 1
        if self.raise_on_open is not None:
            raise RuntimeError(self.raise_on_open)
        self._since_open = 0

    def read(self) -> np.ndarray | None:
        self.reads += 1
        if self.raise_on_read is not None:
            raise RuntimeError(self.raise_on_read)
        if self.frames_per_open is not None:
            if self._since_open >= self.frames_per_open:
                return None
            self._since_open += 1
        return self.frame

    def close(self) -> None:
        self.closes += 1

    def fps(self) -> float:
        return 30.0


def run[T](coro: Any) -> Any:
    """Run one coroutine on a throwaway loop, as the app loop would."""
    return asyncio.run(coro)
