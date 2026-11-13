"""Doubles for the Reachy Mini embodiment's tests: the slice of `ReachyMiniBridge` it touches, and a
scripted face detector for the tests that run the real bridge on its `fake` backend."""

from __future__ import annotations

import threading
import time

import numpy as np
from reachy_mini_bridge import CameraFrame, PixelFace
from reachy_mini_bridge.config import FaceDetectionSettings, ReachyMiniConfig


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


class FakeReachyMiniBridge:
    """A start/stop robot session recording what the embodiment asked of it.

    `ReachyEmbodiment.enter` starts it (`start()`) and fetches `list_emotions`; `say` speaks;
    `RobotMicSource` drains `audio_input()`; `RobotCameraFeed` samples `camera` (a `FakeCameraFeed`); the
    action Commands call the motor/emotion verbs. Set `raise_on_start` to simulate a robot that
    fails to connect (the "no embodiment" degrade) and `raise_on_list_emotions` for one whose
    emotions library fails to load. Pass `log` to record teardown steps in order.
    """

    # What the startup fetch returns — the same names as the bridge's `fake` library.
    emotions = ("happy", "sad", "curious")

    def __init__(
        self,
        *,
        raise_on_start: str | None = None,
        raise_on_list_emotions: str | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.said: list[str] = []
        self.played: list[str] = []
        self.started = False
        self.stopped = False
        self._running = False
        self.emotion_fetches = 0
        self.motors = "disabled"
        self.synthesizer_error: str | None = None
        # The perception slice: the camera feed the gesture input samples, exists from construction
        # like the bridge's (a bright synthetic frame a scripted detector reads as "a hand").
        self.camera = FakeCameraFeed(np.full((48, 64, 3), 200, dtype=np.uint8))
        self._raise_on_start = raise_on_start
        self._raise_on_list_emotions = raise_on_list_emotions
        self._log = log

    async def start(self) -> None:
        if self._raise_on_start is not None:
            raise RuntimeError(self._raise_on_start)
        self.started = True
        self._running = True

    async def stop(self) -> None:
        if self._log is not None:
            self._log.append("embodiment.exit")
        self.stopped = True
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

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


class ScriptedFaces:
    """A `FaceDetector` for the bridge's `custom` source, scripted by the test: `show` the faces the
    next frames hold, `hide` them all. The bridge runs it once per frame of its `fake` backend (a
    small synthetic frame), so everything downstream — the confirmation of a user, the tracks, the
    departures, the head tracker's pin and personal space — is the real bridge's.

    A face is placed by `x` (the bridge's normalised image coordinate, -1 left … 1 right) and `size`
    (its box height as a fraction of the frame's): the fake's camera model turns 0.2 into about
    0.7 m away and 0.9 into well inside the personal space."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faces: list[tuple[float, float]] = []

    def show(self, *faces: tuple[float, float]) -> None:
        """The faces from the next frame on, each `(x, size)`."""
        with self._lock:
            self._faces = list(faces)

    def hide(self) -> None:
        with self._lock:
            self._faces = []

    def detect(self, frame_bgr: np.ndarray, ts: float) -> list[PixelFace]:
        height, width = frame_bgr.shape[:2]
        with self._lock:
            faces = list(self._faces)
        boxes = []
        for x, size in faces:
            side = size * height
            centre = (x + 1.0) / 2.0 * width
            boxes.append(
                PixelFace(bbox=(centre - side / 2, height / 2 - side / 2, side, side))
            )
        return boxes


def seeing_config(faces: ScriptedFaces, *, enabled: bool = True) -> ReachyMiniConfig:
    """A `fake`-backend bridge config whose face detector is *faces* (the `custom` source); detection
    on at session start unless *enabled* is False."""
    return ReachyMiniConfig(
        backend="fake",
        face_detection=FaceDetectionSettings(
            detector="custom", enabled=enabled, face_detector=lambda: faces
        ),
    )
