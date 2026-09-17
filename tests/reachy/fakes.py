"""Doubles for the Reachy Mini embodiment's tests: the slice of `ReachyMiniApi` it touches."""

from __future__ import annotations

from typing import Self

import numpy as np


class FakeReachyMiniApi:
    """An async-context robot recording what the embodiment asked of it.

    `ReachyEmbodiment.enter` enters it (`__aenter__`) and fetches `list_emotions`; `say` speaks;
    `RobotMicSource` drains `audio_input()`; `RobotCameraSource` polls `get_camera_frame()`; the
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

    # The perception slice: the camera frame the gesture input polls (a bright synthetic frame a
    # scripted detector reads as "a hand"; set `frame` to None for "nothing buffered").
    frame: np.ndarray | None = np.full((48, 64, 3), 200, dtype=np.uint8)
    camera_polls = 0

    async def get_camera_frame(self) -> np.ndarray | None:
        self.camera_polls += 1
        return self.frame

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
