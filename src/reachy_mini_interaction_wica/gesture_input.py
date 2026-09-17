"""Vision-in: the robot camera → hand/gesture pipeline → the ``hand_gesture`` fist input.

The vision half of the app's perception. ``RobotCameraSource`` polls the bridge's
``get_camera_frame()`` as a ``vision-modules`` ``FrameSource``; ``GestureInput`` runs the library's
hand stage + HaGRID gesture classifier over it (up to ``max_hands`` hands per frame) and reduces
each result to one value — ``"fist"`` when any hand's validated label is ``fist``, else ``None`` —
delivering it to the injected ``on_gesture`` callback **on change only**. The app wires that
callback to ``world.update("hand_gesture", value)`` on an entry whose trigger predicate,
``fist_raised``, wakes the agent only on ``None → fist``. The library perceives, this module decides; nothing here touches
WICA. See specs/gesture-input.md.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from vision_modules import (
    GestureClassifier,
    HandDetector,
    HandStage,
    ImageClassifier,
    Pipeline,
    Stage,
    StreamProvider,
    save_input,
)

if TYPE_CHECKING:
    from reachy_mini_bridge.api import ReachyMiniApi

logger = logging.getLogger(__name__)

OnGesture = Callable[[str | None], None]

# The one gesture recognised: HaGRID's class name, verbatim (never remapped by the library).
FIST = "fist"

# Bound on one camera poll hop to the loop. The bridge runs the SDK read off the loop, so a poll is
# normally a few ms; a session that stalls longer than this is reported as a capture error rather
# than wedging the capture thread forever.
_POLL_TIMEOUT_S = 5.0

# How often the watcher samples each stage's achieved rate (delta published_count / delta time).
# One second smooths a 5 fps target into a readable figure; the UI timer (0.2 s) reads the latest.
_FPS_WINDOW_S = 1.0


def snapshot_path(folder: str | PathLike[str], stage_name: str, now: datetime) -> Path:
    """``<folder>/snapshot_<stage name>_<YYYYMMDDHHMMSS>.jpg`` — the hand demo's naming (local
    wall-clock time); ``save_input`` inserts ``_<i>`` per crop for the classifier's input."""
    return Path(folder) / f"snapshot_{stage_name}_{now:%Y%m%d%H%M%S}.jpg"


def fist_raised(old: str | None, new: str | None) -> bool:
    """The ``hand_gesture`` trigger predicate: only a fist going *up* wakes the agent.

    ``None → fist`` triggers; ``fist → None`` (lowered) updates the entry silently; ``fist → fist``
    never fires (and never reaches the World anyway — ``GestureInput`` delivers changes only).
    """
    return new == FIST and old != FIST


class RobotCameraSource:
    """The robot camera as a ``vision-modules`` ``FrameSource``: a paced poll of the bridge's
    ``get_camera_frame()`` on the app loop.

    ``read()`` runs on the ``StreamProvider``'s capture thread and hops each poll to the loop the api
    was entered on. It paces itself to ``fps`` (the provider calls ``read()`` back-to-back), skips a
    ``None`` frame (nothing buffered yet — the provider would otherwise end its loop for good), and
    keeps polling through a failing verb, recording why in ``capture_error`` (cleared by the next
    success) so a dead camera shows in the UI instead of silently killing vision. Only ``close()``
    makes ``read()`` return ``None``.
    """

    def __init__(
        self, api: ReachyMiniApi, loop: asyncio.AbstractEventLoop, *, fps: float
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self._api = api
        self._loop = loop
        self._interval = 1.0 / fps
        self._next_poll = time.monotonic()
        self._closed = threading.Event()
        self._error: str | None = None

    @property
    def capture_error(self) -> str | None:
        """Why the last poll failed; ``None`` while healthy (or before the first poll)."""
        return self._error

    def open(self) -> None:
        """Called by the provider on every ``start()``: (re)arm the source so a provider restarted
        after a ``stop()`` polls again. Nothing to open on the robot side — the api's media session
        is the app's, entered long before."""
        self._closed.clear()
        self._next_poll = time.monotonic()

    def read(self) -> np.ndarray | None:
        """The next camera frame, or ``None`` once closed. Never ``None`` for "no frame yet"."""
        while not self._closed.is_set():
            # Pace: one poll per interval, measured from the previous poll — a frame is returned
            # the moment it is read (no added latency), and a `None` just waits for the next slot.
            remaining = self._next_poll - time.monotonic()
            if remaining > 0:
                self._closed.wait(remaining)
                continue
            self._next_poll = time.monotonic() + self._interval
            frame = self._poll()
            if frame is not None:
                return frame
        return None

    def _poll(self) -> np.ndarray | None:
        try:
            frame = asyncio.run_coroutine_threadsafe(
                self._api.get_camera_frame(), self._loop
            ).result(_POLL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - any failure is surfaced, and polling goes on
            message = f"camera read failed: {exc}"
            if self._error != message:  # log the transition, not every poll
                logger.warning(
                    "robot camera read failed; vision is receiving no frames: %s", exc
                )
            self._error = message
            return None
        if self._error is not None:
            logger.info("robot camera read recovered")
            self._error = None
        return frame

    def close(self) -> None:
        self._closed.set()

    def fps(self) -> float:
        """The nominal rate, per the ``FrameSource`` protocol: the poll rate this source paces
        itself to (the robot camera reports none of its own through the bridge). The rate actually
        achieved is the provider's ``published_count``, metered like every other node."""
        return 1.0 / self._interval


class _FpsMeter:
    """A stage's real achieved rate from its ``published_count`` alone — the hand demo's meter.

    ``published_count`` increments exactly once per publish, so (delta count / delta time) between
    two samples is the exact rate over that window. ``frame_id`` would not do: it is inherited from
    the upstream frame, so it reflects the camera's rate, not this stage's own.
    """

    def __init__(self) -> None:
        self._last_count: int | None = None
        self._last_t: float | None = None
        self.fps: float | None = None  # None until two samples exist

    def sample(self, published_count: int) -> None:
        now = time.monotonic()
        if self._last_count is not None and self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                self.fps = (published_count - self._last_count) / dt
        self._last_count, self._last_t = published_count, now


@dataclass(frozen=True)
class GestureModels:
    """Injectable perception backends (tests); ``None`` for either means the library's shipped
    model (MediaPipe hand landmarker / HaGRID ViT), loaded lazily on its stage's thread."""

    detector: HandDetector | None = None
    classifier: ImageClassifier | None = None


class GestureInput:
    """Runs the hand + gesture pipeline over the robot camera and delivers fist changes.

    Graph: ``RobotCameraSource → StreamProvider → HandStage → GestureClassifier``, every stage at
    ``fps``, plus a watcher thread (also at ``fps``) that reduces each new classifier result to
    ``"fist"`` (a fist on any published hand) / ``None`` and calls ``on_gesture`` only when that
    value changes from the last one
    delivered (starting at ``None``, matching the World entry's initial value). ``threshold`` is the
    library's own validation rule — a hand's label is its top class only at or above it — so this
    module adds no second one. ``max_hands`` is always passed to the hand stage (never the library's
    default), so what the robot looks at is the app's setting. ``start``/``stop`` are synchronous and idempotent (threads, like the
    library's ``Pipeline``); ``status()`` is the cheap read the UI timer polls.
    """

    def __init__(
        self,
        api: ReachyMiniApi,
        loop: asyncio.AbstractEventLoop,
        *,
        on_gesture: OnGesture,
        threshold: float = 0.55,
        fps: float = 5.0,
        max_hands: int = 2,
        device: str | None = None,
        models: GestureModels | None = None,
        fps_window_s: float = _FPS_WINDOW_S,
    ) -> None:
        models = models if models is not None else GestureModels()
        self._on_gesture = on_gesture
        self._target_fps = fps
        self._max_hands = max_hands
        self._interval = 1.0 / fps
        self._fps_window_s = fps_window_s
        self._stream_meter = _FpsMeter()
        self._hand_meter = _FpsMeter()
        self._classifier_meter = _FpsMeter()
        self._source = RobotCameraSource(api, loop, fps=fps)
        self._provider = StreamProvider(self._source, mirror=False)
        self._hands = HandStage(
            self._provider,
            target_fps=fps,
            max_hands=max_hands,
            detector=models.detector,
        )
        self._gestures = GestureClassifier(
            self._hands,
            target_fps=fps,
            threshold=threshold,
            classifier=models.classifier,
            device=device,
        )
        self._pipeline = Pipeline([self._provider, self._hands, self._gestures])
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._gesture: str | None = None  # last delivered value
        # Every hand of the latest result, in the library's slot order: (validated label, top score).
        self._seen: tuple[tuple[str | None, float], ...] = ()

    def start(self) -> None:
        """Start the pipeline (upstream-first) and the watcher. A second call is a no-op."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._pipeline.start()
        self._thread = threading.Thread(
            target=self._watch, name="gesture-input-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the watcher, then the pipeline (downstream-first); joins every thread. Idempotent."""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join()
        self._thread = None
        self._pipeline.stop()

    @property
    def running(self) -> bool:
        return self._thread is not None

    def status(self) -> dict:
        """What the Vision line renders: the last delivered value, each hand's latest label/score (slot
        order — entry *i* stays the same physical hand while it is in view), the hand cap, the
        target rate and each node's achieved rate — the camera stream, the hand stage, the
        classifier (``None`` until a full sampling window has passed) — the camera's capture health,
        and the last stage error (the classifier's first — a failed model load lands there),
        ``None`` when healthy."""
        with self._lock:
            gesture, seen = self._gesture, self._seen
            stream_fps = self._stream_meter.fps
            hand_fps, classifier_fps = self._hand_meter.fps, self._classifier_meter.fps
        error = self._gestures.last_error or self._hands.last_error
        return {
            "running": self.running,
            "gesture": gesture,
            "hands": [
                {"label": label, "confidence": confidence} for label, confidence in seen
            ],
            "max_hands": self._max_hands,
            "target_fps": self._target_fps,
            "stream_fps": stream_fps,
            "hand_fps": hand_fps,
            "classifier_fps": classifier_fps,
            "capture_error": self._source.capture_error,
            "error": str(error) if error is not None else None,
        }

    # --- Snapshots: what each stage last consumed, saved from the caller's thread -------------

    def save_detector_snapshot(
        self, folder: str | PathLike[str], now: datetime | None = None
    ) -> str:
        """Save the full frame the hand stage last detected on; returns a one-line status."""
        return self._save_snapshot(self._hands, folder, now)

    def save_classifier_snapshot(
        self, folder: str | PathLike[str], now: datetime | None = None
    ) -> str:
        """Save the hand crop(s) the classifier last classified; returns a one-line status."""
        return self._save_snapshot(self._gestures, folder, now)

    @staticmethod
    def _save_snapshot(
        stage: Stage[Any, Any], folder: str | PathLike[str], now: datetime | None
    ) -> str:
        """The hand demo's ``save_snapshot``: ``save_input(stage)`` into *folder* under the
        ``snapshot_<stage>_<timestamp>.jpg`` name, returning a status line the UI shows instead of
        raising, so a click can never error out. Runs on the caller's (Gradio) thread — the
        library's rule for the snapshot functions — never on a worker."""
        if now is None:
            now = datetime.now(
                tz=UTC
            ).astimezone()  # aware, in the machine's local zone
        target = snapshot_path(folder, stage.name, now)
        try:
            written = save_input(stage, target)
        except (ValueError, TypeError, OSError) as exc:
            return f"⚠️ {stage.name}: {exc}"
        paths = (
            [written]
            if isinstance(written, Path)
            else [p for p in written if p is not None]
        )
        if not paths:
            return f"⚠️ {stage.name}: no crop to save (no hand in its last input)"
        return "Saved " + ", ".join(f"`{p}`" for p in paths)

    def _watch(self) -> None:
        seen: int | None = None
        next_sample = time.monotonic()
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            if t0 >= next_sample:
                # The achieved-rate meters, sampled once per window (like the demo's 1 s timer).
                with self._lock:
                    self._stream_meter.sample(self._provider.published_count)
                    self._hand_meter.sample(self._hands.published_count)
                    self._classifier_meter.sample(self._gestures.published_count)
                next_sample = t0 + self._fps_window_s
            result = self._gestures.latest()
            if result is not None and result.frame_id != seen:
                seen = result.frame_id
                seen_hands = tuple(
                    (hand.label, hand.confidence) for hand in result.hands
                )
                # Any hand counts: slot 0 is merely the hand that appeared first, not the one that
                # matters, and two fists are still one stop sign.
                value = FIST if any(label == FIST for label, _ in seen_hands) else None
                with self._lock:
                    self._seen = seen_hands
                    changed = value != self._gesture
                    if changed:
                        self._gesture = value
                if changed:
                    self._deliver(value)
            self._stop_event.wait(max(self._interval - (time.monotonic() - t0), 0.0))

    def _deliver(self, value: str | None) -> None:
        try:
            self._on_gesture(value)
        except Exception:  # the watcher must survive a failing consumer
            logger.exception("on_gesture(%r) failed", value)
