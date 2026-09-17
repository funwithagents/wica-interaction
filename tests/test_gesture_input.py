"""Functional tests for the fist gesture input (`gesture_input`).

Drives `GestureInput` end-to-end through the real `vision-modules` pipeline — `RobotCameraSource →
StreamProvider → HandStage → GestureClassifier → watcher` — with a fake `ReachyMiniApi` serving
synthetic frames on a real event loop, a scripted `HandDetector`, and a scripted `ImageClassifier`
whose scores a test flips mid-run. No camera, no model, no network; a high fps keeps each test well
under a second. See specs/gesture-input.md ("Testing").
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from reachy_mini_bridge.api import ReachyMiniApi
from vision_modules.hand import DetectedHand

from reachy_mini_interaction_wica.gesture_input import (
    FIST,
    GestureInput,
    GestureModels,
    RobotCameraSource,
    fist_raised,
    snapshot_path,
)

_FPS = 100.0  # every stage; tests wait on outcomes, so this only bounds their latency

# A frame "with a hand" is bright, one "without" is black: the scripted detector reads the corner.
_HAND_FRAME = np.full((48, 64, 3), 200, dtype=np.uint8)
_EMPTY_FRAME = np.zeros((48, 64, 3), dtype=np.uint8)


class FakeCameraApi:
    """The one verb `RobotCameraSource` calls: `get_camera_frame()`, served from a settable `frame`
    (`None` ⇒ "nothing buffered yet"); `fail_with` makes it raise instead. Counts the polls."""

    def __init__(self, frame: np.ndarray | None = _HAND_FRAME) -> None:
        self.frame: np.ndarray | None = frame
        self.fail_with: str | None = None
        self.polls = 0

    async def get_camera_frame(self) -> np.ndarray | None:
        self.polls += 1
        if self.fail_with is not None:
            raise RuntimeError(self.fail_with)
        return self.frame


class ScriptedDetector:
    """One centered hand in any non-black frame, none in a black one."""

    def detect(self, image_bgr: np.ndarray, ts: float) -> tuple[DetectedHand, ...]:
        if int(image_bgr[0, 0, 0]) == 0:
            return ()
        return (DetectedHand(box=(0.25, 0.25, 0.75, 0.75), score=0.95),)

    def close(self) -> None:
        pass


class ScriptedClassifier:
    """Returns the current `scores` for every crop; a test reassigns them to change the gesture."""

    labels = ("fist", "palm", "one")

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.raise_with: str | None = None

    def classify(self, image_bgr: np.ndarray) -> dict[str, float]:
        if self.raise_with is not None:
            raise RuntimeError(self.raise_with)
        return dict(self.scores)

    def close(self) -> None:
        pass


def _scores(label: str, score: float) -> dict[str, float]:
    """A score map where *label* tops at *score* and the rest share the remainder."""
    others = [name for name in ScriptedClassifier.labels if name != label]
    rest = (1.0 - score) / len(others)
    return {label: score, **{name: rest for name in others}}


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """A running loop on its own thread — the app loop the camera source hops onto."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)
    loop.close()


def _wait_until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _input(
    loop: asyncio.AbstractEventLoop,
    api: FakeCameraApi,
    classifier: ScriptedClassifier,
    deliveries: list[str | None],
    *,
    threshold: float = 0.55,
) -> GestureInput:
    return GestureInput(
        cast(ReachyMiniApi, api),
        loop,
        on_gesture=deliveries.append,
        threshold=threshold,
        fps=_FPS,
        models=GestureModels(detector=ScriptedDetector(), classifier=classifier),
    )


def test_fist_is_delivered_once_across_many_frames(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """A fist held over many frames delivers `"fist"` exactly once — changes only, never per frame."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [FIST])
        polls_then = api.polls
        assert _wait_until(
            lambda: api.polls >= polls_then + 10
        )  # many more frames went by
        assert deliveries == [FIST]
        assert gesture.status()["gesture"] == FIST
        assert gesture.status()["label"] == FIST
        assert gesture.status()["confidence"] == pytest.approx(0.9)
    finally:
        gesture.stop()


def test_lowered_and_raised_again_deliver_each_change(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """None → fist → None → fist ⇒ exactly three deliveries, in that order."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [FIST])
        api.frame = _EMPTY_FRAME  # the hand leaves the frame
        assert _wait_until(lambda: deliveries == [FIST, None])
        assert gesture.status()["label"] is None
        api.frame = _HAND_FRAME  # ...and comes back as a fist
        assert _wait_until(lambda: deliveries == [FIST, None, FIST])
    finally:
        gesture.stop()


def test_sub_threshold_fist_delivers_nothing_but_shows_its_score(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """A fist scored below the threshold is "no fist": nothing delivered, the raw score visible."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.5)), deliveries)
    gesture.start()
    try:
        # The classifier processed frames (the score shows up), yet nothing was delivered.
        assert _wait_until(lambda: gesture.status()["confidence"] == pytest.approx(0.5))
        status = gesture.status()
        assert status["label"] is None and status["gesture"] is None
        assert deliveries == []
    finally:
        gesture.stop()


def test_threshold_is_the_apps_setting(loop: asyncio.AbstractEventLoop) -> None:
    """The same 0.5 fist validates once the configured threshold allows it."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(
        loop, api, ScriptedClassifier(_scores("fist", 0.5)), deliveries, threshold=0.4
    )
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [FIST])
    finally:
        gesture.stop()


def test_other_gestures_are_no_fist(loop: asyncio.AbstractEventLoop) -> None:
    """A confidently classified non-fist gesture delivers nothing; only `fist` is recognised."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    classifier = ScriptedClassifier(_scores("palm", 0.9))
    gesture = _input(loop, api, classifier, deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: gesture.status()["label"] == "palm")
        assert deliveries == []
        classifier.scores = _scores("fist", 0.9)  # the palm becomes a fist
        assert _wait_until(lambda: deliveries == [FIST])
        classifier.scores = _scores(
            "one", 0.9
        )  # ...then another gesture: the fist is down
        assert _wait_until(lambda: deliveries == [FIST, None])
    finally:
        gesture.stop()


def test_achieved_fps_is_measured_per_stage(loop: asyncio.AbstractEventLoop) -> None:
    """Both stages' achieved rates appear (from their published_count) once frames flow, after
    one sampling window; the target is the configured rate."""
    api = FakeCameraApi()
    gesture = GestureInput(
        cast(ReachyMiniApi, api),
        loop,
        on_gesture=lambda _value: None,
        fps=_FPS,
        models=GestureModels(
            detector=ScriptedDetector(),
            classifier=ScriptedClassifier(_scores("fist", 0.9)),
        ),
        fps_window_s=0.1,
    )
    gesture.start()
    try:
        assert gesture.status()["hand_fps"] is None  # no window sampled yet
        assert gesture.status()["target_fps"] == _FPS

        def both_measured() -> bool:
            status = gesture.status()
            return (
                status["stream_fps"] is not None
                and status["stream_fps"] > 0
                and status["hand_fps"] is not None
                and status["hand_fps"] > 0
                and status["classifier_fps"] is not None
                and status["classifier_fps"] > 0
            )

        assert _wait_until(both_measured)
        # Sanity: no node can outrun the poll/target rate (generous slack for timer jitter).
        assert gesture.status()["stream_fps"] <= _FPS * 1.5
        assert gesture.status()["hand_fps"] <= _FPS * 1.5
    finally:
        gesture.stop()


def test_stop_joins_and_delivers_nothing_afterwards(
    loop: asyncio.AbstractEventLoop,
) -> None:
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    gesture.start()
    assert gesture.running
    assert _wait_until(lambda: deliveries == [FIST])
    gesture.stop()
    assert not gesture.running
    polls_then = api.polls
    api.frame = _EMPTY_FRAME  # would deliver None if anything were still running
    time.sleep(0.1)
    assert api.polls == polls_then  # the camera is no longer polled
    assert deliveries == [FIST]
    gesture.stop()  # idempotent


def test_classifier_failure_is_reported_not_fatal(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """A raising classifier (the model-load failure case) lands in status()["error"] and the graph
    keeps running: once it works again, the fist is delivered."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    classifier = ScriptedClassifier(_scores("fist", 0.9))
    classifier.raise_with = "model download failed"
    gesture = _input(loop, api, classifier, deliveries)
    gesture.start()
    try:
        assert _wait_until(
            lambda: (gesture.status()["error"] or "").endswith("model download failed")
        )
        assert deliveries == []
        classifier.raise_with = None
        assert _wait_until(lambda: deliveries == [FIST])
    finally:
        gesture.stop()


def test_failing_consumer_does_not_kill_the_watcher(
    loop: asyncio.AbstractEventLoop,
) -> None:
    api = FakeCameraApi()
    seen: list[str | None] = []

    def on_gesture(value: str | None) -> None:
        seen.append(value)
        raise RuntimeError("consumer broke")

    gesture = GestureInput(
        cast(ReachyMiniApi, api),
        loop,
        on_gesture=on_gesture,
        fps=_FPS,
        models=GestureModels(
            detector=ScriptedDetector(),
            classifier=ScriptedClassifier(_scores("fist", 0.9)),
        ),
    )
    gesture.start()
    try:
        assert _wait_until(lambda: seen == [FIST])
        api.frame = _EMPTY_FRAME
        assert _wait_until(
            lambda: seen == [FIST, None]
        )  # still watching after the raise
    finally:
        gesture.stop()


# --- Snapshots (the hand demo's buttons) --------------------------------------------------------


def test_snapshots_save_what_each_stage_last_consumed(
    loop: asyncio.AbstractEventLoop, tmp_path: Path
) -> None:
    """Before any frame: a warning, nothing written. Once frames flow: the detector snapshot is the
    full frame, the classifier snapshot the hand crop, named `snapshot_<stage>_<ts>[_i].jpg`."""
    api = FakeCameraApi()
    deliveries: list[str | None] = []
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    when = datetime(2026, 9, 17, 14, 30, 5, tzinfo=UTC)

    early = gesture.save_detector_snapshot(tmp_path, when)
    assert early.startswith("⚠️ HandStage") and not list(tmp_path.iterdir())

    gesture.start()
    try:
        assert _wait_until(
            lambda: deliveries == [FIST]
        )  # both stages have consumed a frame
        detector = gesture.save_detector_snapshot(tmp_path, when)
        classifier = gesture.save_classifier_snapshot(tmp_path, when)
    finally:
        gesture.stop()

    frame_file = snapshot_path(tmp_path, "HandStage", when)
    crop_file = tmp_path / "snapshot_GestureClassifier_20260917143005_0.jpg"
    assert frame_file == tmp_path / "snapshot_HandStage_20260917143005.jpg"
    assert detector == f"Saved `{frame_file}`"
    assert classifier == f"Saved `{crop_file}`"
    assert frame_file.stat().st_size > 0 and crop_file.stat().st_size > 0
    # The detector's snapshot is the full 64×48 frame; the classifier's is the padded crop of it.
    import cv2

    frame = cv2.imread(str(frame_file))
    crop = cv2.imread(str(crop_file))
    assert frame is not None and frame.shape == _HAND_FRAME.shape
    assert crop is not None and crop.shape[0] < frame.shape[0]


def test_classifier_snapshot_without_a_hand_reports_no_crop(
    loop: asyncio.AbstractEventLoop, tmp_path: Path
) -> None:
    api = FakeCameraApi(frame=_EMPTY_FRAME)
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), [])
    gesture.start()
    try:
        assert _wait_until(lambda: gesture._gestures.published_count > 0)  # pyright: ignore[reportPrivateUsage]
        status = gesture.save_classifier_snapshot(tmp_path)
    finally:
        gesture.stop()
    assert "no crop to save" in status
    assert not list(tmp_path.iterdir())


def test_snapshot_into_an_unwritable_folder_reports_instead_of_raising(
    loop: asyncio.AbstractEventLoop, tmp_path: Path
) -> None:
    api = FakeCameraApi()
    gesture = _input(loop, api, ScriptedClassifier(_scores("fist", 0.9)), [])
    gesture.start()
    try:
        assert _wait_until(lambda: gesture._hands.published_count > 0)  # pyright: ignore[reportPrivateUsage]
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("a file where the folder should be")
        status = gesture.save_detector_snapshot(blocker)
    finally:
        gesture.stop()
    assert status.startswith("⚠️ HandStage:")


# --- RobotCameraSource ---------------------------------------------------------------------------


def test_camera_source_skips_none_frames_instead_of_ending(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """ "Nothing buffered yet" is not the end of the stream: read() waits for a real frame."""
    api = FakeCameraApi(frame=None)
    source = RobotCameraSource(cast(ReachyMiniApi, api), loop, fps=_FPS)
    threading.Timer(0.05, lambda: setattr(api, "frame", _HAND_FRAME)).start()
    frame = source.read()
    assert frame is not None and frame is _HAND_FRAME
    assert api.polls >= 2  # it polled through the None(s) rather than returning one


def test_camera_source_reports_and_recovers_from_a_failing_poll(
    loop: asyncio.AbstractEventLoop,
) -> None:
    api = FakeCameraApi()
    api.fail_with = "no camera"
    source = RobotCameraSource(cast(ReachyMiniApi, api), loop, fps=_FPS)
    threading.Timer(0.05, lambda: setattr(api, "fail_with", None)).start()
    frame = (
        source.read()
    )  # blocks through the failures, returns once the verb works again
    assert frame is _HAND_FRAME
    assert source.capture_error is None  # cleared by the success
    api.fail_with = "camera gone"
    threading.Timer(0.05, source.close).start()
    assert (
        source.read() is None
    )  # closed while failing ⇒ None, and the reason stays visible
    assert source.capture_error == "camera read failed: camera gone"


def test_camera_source_close_unblocks_read(loop: asyncio.AbstractEventLoop) -> None:
    source = RobotCameraSource(
        cast(ReachyMiniApi, FakeCameraApi(frame=None)), loop, fps=1.0
    )
    threading.Timer(0.05, source.close).start()
    t0 = time.monotonic()
    assert source.read() is None
    assert time.monotonic() - t0 < 0.5  # did not wait out the 1 s interval


def test_camera_source_paces_polls_to_fps(loop: asyncio.AbstractEventLoop) -> None:
    api = FakeCameraApi()
    source = RobotCameraSource(cast(ReachyMiniApi, api), loop, fps=50.0)
    assert source.fps() == 50.0  # the protocol's nominal rate is the poll rate
    t0 = time.monotonic()
    for _ in range(6):
        assert source.read() is not None
    elapsed = time.monotonic() - t0
    assert elapsed >= 5 * 0.02 * 0.9  # 6 reads span at least 5 intervals (with slack)
    assert api.polls == 6


# --- The trigger predicate -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, FIST, True),  # raised
        (FIST, None, False),  # lowered
        (FIST, FIST, False),  # still up
        (None, None, False),  # still down
    ],
)
def test_fist_raised_predicate(
    old: str | None, new: str | None, expected: bool
) -> None:
    assert fist_raised(old, new) is expected
