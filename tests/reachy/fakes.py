"""Doubles for the Reachy Mini embodiment's tests: the slice of `ReachyMiniApi` it touches."""

from __future__ import annotations

import threading
import time
from typing import Self

import numpy as np
from reachy_mini_bridge import CameraFrame


class FakeCameraFeed:
    """The slice of the bridge's `CameraFeed` the embodiment's `RobotCameraFeed` forwards to —
    `latest()` and `published_count` — plus the `start()` / `stop()` the session owns and the
    adapter must never call (counted). `publish()` publishes `frame` under the next `frame_id`, as
    the bridge's reader thread would; `frame` `None` publishes nothing ("no frame yet")."""

    def __init__(self, frame: np.ndarray | None) -> None:
        self.frame = frame
        self.starts = 0
        self.stops = 0
        self._lock = threading.Lock()
        self._latest: CameraFrame | None = None
        self._count = 0

    def publish(self) -> CameraFrame | None:
        if self.frame is None:
            return None
        with self._lock:
            self._count += 1
            self._latest = CameraFrame(self._count, time.monotonic(), self.frame)
            return self._latest

    def latest(self) -> CameraFrame | None:
        with self._lock:
            return self._latest

    @property
    def published_count(self) -> int:
        with self._lock:
            return self._count

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


class FakeReachyMiniApi:
    """An async-context robot recording what the embodiment asked of it.

    `ReachyEmbodiment.enter` enters it (`__aenter__`) and fetches `list_emotions`; `say` speaks;
    `RobotMicSource` drains `audio_input()`; `RobotCameraFeed` samples `camera` (a `FakeCameraFeed`); the
    action Commands call the motor/emotion verbs. Set `raise_on_enter` to simulate a robot that
    fails to connect (the "no embodiment" degrade) and `raise_on_list_emotions` for one whose
    emotions library fails to load. Pass `log` to record teardown steps in order.
    """

    # What the startup fetch returns — the same names as the bridge's `fake` library.
    emotions = ("happy", "sad", "curious")

    def __init__(
        self,
        *,
        raise_on_enter: str | None = None,
        raise_on_list_emotions: str | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.said: list[str] = []
        self.played: list[str] = []
        self.entered = False
        self.exited = False
        self.emotion_fetches = 0
        self.motors = "disabled"
        self.synthesizer_error: str | None = None
        # The perception slice: the camera feed the gesture input samples, exists from construction
        # like the bridge's (a bright synthetic frame a scripted detector reads as "a hand").
        self.camera = FakeCameraFeed(np.full((48, 64, 3), 200, dtype=np.uint8))
        self._raise_on_enter = raise_on_enter
        self._raise_on_list_emotions = raise_on_list_emotions
        self._log = log

    async def __aenter__(self) -> Self:
        if self._raise_on_enter is not None:
            raise RuntimeError(self._raise_on_enter)
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._log is not None:
            self._log.append("embodiment.exit")
        self.exited = True

    async def say(self, text: str) -> None:
        self.said.append(text)

    # The robot-control slice.
    async def get_motors_state(self) -> str:
        return self.motors

    async def set_motors_state(self, state: str) -> None:
        self.motors = state

    async def play_emotion(self, name: str) -> None:
        self.played.append(name)

    async def list_emotions(self) -> list[str]:
        self.emotion_fetches += 1
        if self._raise_on_list_emotions is not None:
            raise RuntimeError(self._raise_on_list_emotions)
        return list(self.emotions)
