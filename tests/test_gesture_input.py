"""Functional tests for the hand-sign gesture input (`gesture_input`).

Drives `GestureInput` end-to-end through the real `vision-modules` pipeline — `CameraSource →
StreamProvider → HandStage → GestureClassifier → watcher` — over a scripted camera source serving
synthetic frames, a scripted `HandDetector`, and a scripted `ImageClassifier` whose scores a test
flips mid-run. No camera, no model, no network; a high fps keeps each test well under a second. The
camera sources themselves are tested with their embodiments. See specs/gesture-input.md ("Testing").
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from vision_modules.hand import DetectedHand

from tests.fakes import ScriptedCameraSource
from wica_interaction.gesture_input import (
    SIGNS,
    GestureInput,
    GestureModels,
    HoldFilter,
    hand_signs_prompt_section,
    sign_shown,
    snapshot_path,
)

# One delivery: (hand number, that hand's sign or None).
Delivery = tuple[int, str | None]

_FPS = 100.0  # every stage; tests wait on outcomes, so this only bounds their latency

# A frame "with a hand" is bright, one "without" is black: the scripted detector reads the corner.
_HAND_FRAME = np.full((48, 64, 3), 200, dtype=np.uint8)
_EMPTY_FRAME = np.zeros((48, 64, 3), dtype=np.uint8)


def _camera(frame: np.ndarray | None = _HAND_FRAME) -> ScriptedCameraSource:
    """The embodiment's camera, scripted: one settable frame at this file's fps."""
    return ScriptedCameraSource(frame, fps=_FPS)


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

    labels = ("fist", "palm", "one", "peace", "rock", "three2", "stop_inverted", "call")

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


def _wait_until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _input(
    camera: ScriptedCameraSource,
    classifier: ScriptedClassifier,
    deliveries: list[Delivery],
    *,
    threshold: float = 0.55,
    max_hands: int = 2,
    hold_s: float = 0.0,
) -> GestureInput:
    """No hold unless a test asks for one: the reduction and change tests are about what each
    frame does; the hold has its own tests below."""
    return GestureInput(
        camera,
        on_gesture=lambda hand, sign: deliveries.append((hand, sign)),
        threshold=threshold,
        fps=_FPS,
        max_hands=max_hands,
        hold_s=hold_s,
        models=GestureModels(detector=ScriptedDetector(), classifier=classifier),
    )


def test_a_sign_is_delivered_once_across_many_frames() -> None:
    """A sign held over many frames delivers once, for hand 1 — changes only, never per frame."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores("like", 0.9)), deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(1, "like")])
        reads_then = camera.reads
        assert _wait_until(
            lambda: camera.reads >= reads_then + 10
        )  # many more frames went by
        assert deliveries == [(1, "like")]
        assert gesture.status()["signs"] == ["like", None]  # one per max_hands
        assert gesture.status()["hands"] == [
            {"label": "like", "confidence": pytest.approx(0.9)}
        ]
    finally:
        gesture.stop()


def test_lowered_and_shown_again_deliver_each_change() -> None:
    """None → like → None → like ⇒ exactly three deliveries, in that order."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores("like", 0.9)), deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(1, "like")])
        camera.frame = _EMPTY_FRAME  # the hand leaves the frame
        assert _wait_until(lambda: deliveries == [(1, "like"), (1, None)])
        assert gesture.status()["hands"] == []
        camera.frame = _HAND_FRAME  # ...and comes back showing the same sign
        assert _wait_until(lambda: deliveries == [(1, "like"), (1, None), (1, "like")])
    finally:
        gesture.stop()


@pytest.mark.parametrize(
    ("label", "sign"),
    [
        ("fist", "stop"),
        ("stop_inverted", "stop"),
        ("one", "one"),
        ("rock", "two"),
        ("three2", "three"),
        ("palm", "five"),
    ],
)
def test_each_label_reduces_to_its_sign(label: str, sign: str) -> None:
    """The `SIGNS` table: several HaGRID classes collapse onto one app sign."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores(label, 0.9)), deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(1, sign)])
    finally:
        gesture.stop()


def test_switching_sign_delivers_the_new_one_directly() -> None:
    """A hand going from one sign to another with no sign-less frame between delivers the new sign;
    two labels that mean the same sign deliver nothing new."""
    camera = _camera()
    deliveries: list[Delivery] = []
    classifier = ScriptedClassifier(_scores("one", 0.9))
    gesture = _input(camera, classifier, deliveries)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(1, "one")])
        classifier.scores = _scores("peace", 0.9)  # one → two, no None between
        assert _wait_until(lambda: deliveries == [(1, "one"), (1, "two")])
        classifier.scores = _scores("rock", 0.9)  # another label, the same sign
        assert _wait_until(
            lambda: [h["label"] for h in gesture.status()["hands"]] == ["rock"]
        )
        assert deliveries == [(1, "one"), (1, "two")]
    finally:
        gesture.stop()


def test_sub_threshold_sign_delivers_nothing_but_shows_its_score() -> None:
    """A sign scored below the threshold is "no sign": nothing delivered, the raw score visible."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores("like", 0.5)), deliveries)
    gesture.start()
    try:
        # The classifier processed frames (the score shows up), yet nothing was delivered.
        assert _wait_until(
            lambda: (
                gesture.status()["hands"]
                == [{"label": None, "confidence": pytest.approx(0.5)}]
            )
        )
        assert gesture.status()["signs"] == [None, None]
        assert deliveries == []
    finally:
        gesture.stop()


def test_threshold_is_the_apps_setting() -> None:
    """The same 0.5 hand validates once the configured threshold allows it."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(
        camera, ScriptedClassifier(_scores("fist", 0.5)), deliveries, threshold=0.4
    )
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(1, "stop")])
    finally:
        gesture.stop()


@pytest.mark.parametrize("label", ["call", "mute"])
def test_an_unmapped_class_is_no_sign(label: str) -> None:
    """`call` and `mute` are outside the vocabulary: confidently classified, they still deliver
    nothing — and a hand leaving a mapped sign for one of them delivers the `None`."""
    camera = _camera()
    deliveries: list[Delivery] = []
    classifier = ScriptedClassifier(_scores(label, 0.9))
    gesture = _input(camera, classifier, deliveries)
    gesture.start()
    try:
        assert _wait_until(
            lambda: [h["label"] for h in gesture.status()["hands"]] == [label]
        )
        assert deliveries == []
        classifier.scores = _scores("ok", 0.9)
        assert _wait_until(lambda: deliveries == [(1, "ok")])
        classifier.scores = _scores(label, 0.9)  # back outside the vocabulary
        assert _wait_until(lambda: deliveries == [(1, "ok"), (1, None)])
    finally:
        gesture.stop()


def test_the_vocabulary_is_the_specced_table() -> None:
    """Every sign the persona names is reachable; `call` and `mute` are the classes left out."""
    assert set(SIGNS.values()) == {
        "stop",
        "like",
        "dislike",
        "ok",
        "one",
        "two",
        "three",
        "four",
        "five",
    }
    assert "call" not in SIGNS
    assert "mute" not in SIGNS


# --- The hold filter --------------------------------------------------------------------------------


def test_hold_delivers_a_value_only_once_it_has_held_unbroken() -> None:
    """Seen once: not delivered. Seen again `hold_s` later with nothing else between: delivered,
    once; later sightings of the delivered value deliver nothing more."""
    hold = HoldFilter(0.5)
    assert hold.observe("two", 10.0) is False
    assert hold.value is None
    assert hold.observe("two", 10.3) is False
    assert hold.observe("two", 10.5) is True
    assert hold.value == "two"
    assert hold.observe("two", 11.0) is False
    assert hold.value == "two"


def test_hold_restarts_the_clock_on_a_dissenting_frame() -> None:
    """One frame of another value resets the hold: it counts from the dissent, not from the first
    sighting — strictly consecutive, no majority vote."""
    hold = HoldFilter(0.5)
    assert hold.observe("two", 10.0) is False
    assert hold.observe("three", 10.4) is False  # the dissent starts its own candidate
    assert (
        hold.observe("two", 10.6) is False
    )  # back to two, but the clock restarted here
    assert (
        hold.observe("two", 10.9) is False
    )  # 0.9 s after the first sighting, still nothing
    assert hold.observe("two", 11.1) is True  # 0.5 s after the restart
    assert hold.value == "two"


def test_hold_swallows_a_dip_to_none_between_two_sightings() -> None:
    """A one-frame flicker to `None` below a delivered sign never reaches the consumer: the
    delivered value seen again drops the candidate, and no `two → None → two` happens."""
    hold = HoldFilter(0.5)
    assert hold.observe("two", 10.0) is False
    assert hold.observe("two", 10.5) is True
    assert hold.observe(None, 10.7) is False  # the dip
    assert hold.observe("two", 10.9) is False  # back before the hold elapsed
    assert hold.value == "two"
    assert (
        hold.observe(None, 11.5) is False
    )  # a fresh candidate: the old dip does not count
    assert hold.observe(None, 12.0) is True  # a real drop, held for its own 0.5 s
    assert hold.value is None


def test_hold_applies_to_a_sign_going_away_too() -> None:
    """`x → None` waits the same hold as `None → x`."""
    hold = HoldFilter(0.5)
    hold.observe("stop", 0.0)
    assert hold.observe("stop", 0.5) is True
    assert hold.observe(None, 1.0) is False
    assert hold.observe(None, 1.4) is False
    assert hold.observe(None, 1.5) is True
    assert hold.value is None


def test_hold_of_zero_delivers_on_the_first_observation() -> None:
    hold = HoldFilter(0.0)
    assert hold.observe("ok", 5.0) is True
    assert hold.value == "ok"
    assert hold.observe(None, 5.01) is True
    assert hold.value is None


def test_hold_rejects_a_negative_duration() -> None:
    with pytest.raises(ValueError, match="hold_s"):
        HoldFilter(-0.1)
    with pytest.raises(ValueError, match="hold_s"):
        GestureInput(
            _camera(),
            on_gesture=lambda _hand, _sign: None,
            hold_s=-1.0,
            models=GestureModels(
                detector=ScriptedDetector(),
                classifier=ScriptedClassifier(_scores("ok", 0.9)),
            ),
        )


def test_a_sign_is_delivered_after_the_hold_and_not_before() -> None:
    """Through the pipeline with a real clock: a steady sign takes at least `hold_s` to arrive,
    arrives once, and the status echoes the hold."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(
        camera, ScriptedClassifier(_scores("like", 0.9)), deliveries, hold_s=0.2
    )
    t0 = time.monotonic()
    gesture.start()
    try:
        assert gesture.status()["hold_s"] == 0.2
        # The classifier is publishing well before the hold elapses, yet nothing is delivered.
        assert _wait_until(
            lambda: [h["label"] for h in gesture.status()["hands"]] == ["like"]
        )
        assert deliveries == []
        assert gesture.status()["signs"] == [None, None]
        assert _wait_until(lambda: deliveries == [(1, "like")])
        assert time.monotonic() - t0 >= 0.2
        assert gesture.status()["signs"] == ["like", None]
        time.sleep(0.1)  # many more frames at 100 fps
        assert deliveries == [(1, "like")]
    finally:
        gesture.stop()


def test_a_flickering_sign_never_arrives_until_it_settles() -> None:
    """A classifier flipping between two signs faster than the hold delivers neither; once it
    settles on one, that one arrives."""
    camera = _camera()
    deliveries: list[Delivery] = []
    classifier = ScriptedClassifier(_scores("stop", 0.9))
    gesture = _input(camera, classifier, deliveries, hold_s=0.2)
    gesture.start()
    try:
        deadline = time.monotonic() + 0.6
        flip = False
        while (
            time.monotonic() < deadline
        ):  # flip every ~50 ms, well under the 200 ms hold
            classifier.scores = _scores("palm" if flip else "stop", 0.9)
            flip = not flip
            time.sleep(0.05)
        assert deliveries == []
        classifier.scores = _scores("palm", 0.9)  # settles
        assert _wait_until(lambda: deliveries == [(1, "five")])
    finally:
        gesture.stop()


# --- Two hands ------------------------------------------------------------------------------------

# Two hands side by side: the left half of the frame is one hand, the right half the other. The
# left box is the bigger one, so it takes slot 0 when both first appear (the library's area rank).
_LEFT, _RIGHT = 100, 200
_TWO_HANDS_FRAME = np.zeros((48, 64, 3), dtype=np.uint8)
_TWO_HANDS_FRAME[:, :32] = _LEFT
_TWO_HANDS_FRAME[:, 32:] = _RIGHT


class TwoHandDetector:
    """Always the same two hands: a big one on the left, a smaller one on the right."""

    def detect(self, image_bgr: np.ndarray, ts: float) -> tuple[DetectedHand, ...]:
        return (
            DetectedHand(box=(0.60, 0.30, 0.90, 0.70), score=0.9),  # right, smaller
            DetectedHand(box=(0.05, 0.20, 0.45, 0.80), score=0.8),  # left, bigger
        )

    def close(self) -> None:
        pass


class PerHandClassifier(ScriptedClassifier):
    """Scores each crop by which hand it is (the crop's center pixel): `scores` for the left hand,
    `right_scores` for the right one."""

    def __init__(self, left: dict[str, float], right: dict[str, float]) -> None:
        super().__init__(left)
        self.right_scores = right

    def classify(self, image_bgr: np.ndarray) -> dict[str, float]:
        h, w = image_bgr.shape[:2]
        if int(image_bgr[h // 2, w // 2, 0]) == _RIGHT:
            return dict(self.right_scores)
        return dict(self.scores)


def _two_hand_input(
    classifier: PerHandClassifier,
    deliveries: list[Delivery],
    *,
    max_hands: int,
) -> GestureInput:
    return GestureInput(
        _camera(_TWO_HANDS_FRAME),
        on_gesture=lambda hand, sign: deliveries.append((hand, sign)),
        fps=_FPS,
        max_hands=max_hands,
        hold_s=0.0,
        models=GestureModels(detector=TwoHandDetector(), classifier=classifier),
    )


def test_each_hand_is_delivered_on_its_own_slot() -> None:
    """Two hands are two states: a sign on the second hand alone delivers `(2, sign)`; each hand's
    later changes are reported against that hand only."""
    deliveries: list[Delivery] = []
    classifier = PerHandClassifier(_scores("call", 0.9), _scores("fist", 0.8))
    gesture = _two_hand_input(classifier, deliveries, max_hands=2)
    gesture.start()
    try:
        assert _wait_until(lambda: deliveries == [(2, "stop")])
        status = gesture.status()
        assert status["max_hands"] == 2
        assert status["signs"] == [None, "stop"]
        # Slot order: the bigger left hand first, then the right one showing the fist.
        assert status["hands"] == [
            {"label": "call", "confidence": pytest.approx(0.9)},
            {"label": "fist", "confidence": pytest.approx(0.8)},
        ]

        classifier.scores = _scores("three", 0.9)  # the left hand starts signing too
        assert _wait_until(lambda: deliveries == [(2, "stop"), (1, "three")])
        assert gesture.status()["signs"] == ["three", "stop"]

        classifier.right_scores = _scores("call", 0.8)  # the right hand stops signing
        assert _wait_until(lambda: deliveries == [(2, "stop"), (1, "three"), (2, None)])
        assert gesture.status()["signs"] == ["three", None]
    finally:
        gesture.stop()


def test_max_hands_caps_what_is_classified() -> None:
    """With `max_hands=1` only the bigger (left, sign-less) hand is published: the right hand's sign
    is never seen, and only hand 1 is ever reported."""
    deliveries: list[Delivery] = []
    classifier = PerHandClassifier(_scores("call", 0.9), _scores("fist", 0.8))
    gesture = _two_hand_input(classifier, deliveries, max_hands=1)
    gesture.start()
    try:
        assert _wait_until(
            lambda: [h["label"] for h in gesture.status()["hands"]] == ["call"]
        )
        time.sleep(0.1)  # many more frames at 100 fps
        assert gesture.status()["max_hands"] == 1
        assert gesture.status()["signs"] == [None]
        assert len(gesture.status()["hands"]) == 1
        assert deliveries == []

        classifier.scores = _scores("ok", 0.9)  # the one published hand signs
        assert _wait_until(lambda: deliveries == [(1, "ok")])
    finally:
        gesture.stop()


def test_achieved_fps_is_measured_per_stage() -> None:
    """Both stages' achieved rates appear (from their published_count) once frames flow, after
    one sampling window; the target is the configured rate."""
    camera = _camera()
    gesture = GestureInput(
        camera,
        on_gesture=lambda _hand, _sign: None,
        fps=_FPS,
        hold_s=0.0,
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


def test_stop_joins_and_delivers_nothing_afterwards() -> None:
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    gesture.start()
    assert gesture.running
    assert _wait_until(lambda: deliveries == [(1, "stop")])
    gesture.stop()
    assert not gesture.running
    reads_then = camera.reads
    camera.frame = _EMPTY_FRAME  # would deliver None if anything were still running
    time.sleep(0.1)
    assert camera.reads == reads_then  # the camera is no longer polled
    assert deliveries == [(1, "stop")]
    gesture.stop()  # idempotent


def test_classifier_failure_is_reported_not_fatal() -> None:
    """A raising classifier (the model-load failure case) lands in status()["error"] and the graph
    keeps running: once it works again, the sign is delivered."""
    camera = _camera()
    deliveries: list[Delivery] = []
    classifier = ScriptedClassifier(_scores("fist", 0.9))
    classifier.raise_with = "model download failed"
    gesture = _input(camera, classifier, deliveries)
    gesture.start()
    try:
        assert _wait_until(
            lambda: (gesture.status()["error"] or "").endswith("model download failed")
        )
        assert deliveries == []
        classifier.raise_with = None
        assert _wait_until(lambda: deliveries == [(1, "stop")])
    finally:
        gesture.stop()


def test_failing_consumer_does_not_kill_the_watcher() -> None:
    camera = _camera()
    seen: list[Delivery] = []

    def on_gesture(hand: int, value: str | None) -> None:
        seen.append((hand, value))
        raise RuntimeError("consumer broke")

    gesture = GestureInput(
        camera,
        on_gesture=on_gesture,
        fps=_FPS,
        models=GestureModels(
            detector=ScriptedDetector(),
            classifier=ScriptedClassifier(_scores("fist", 0.9)),
        ),
    )
    gesture.start()
    try:
        assert _wait_until(lambda: seen == [(1, "stop")])
        camera.frame = _EMPTY_FRAME
        assert _wait_until(
            lambda: seen == [(1, "stop"), (1, None)]
        )  # still watching after the raise
    finally:
        gesture.stop()


# --- Snapshots (the hand demo's buttons) --------------------------------------------------------


def test_snapshots_save_what_each_stage_last_consumed(tmp_path: Path) -> None:
    """Before any frame: a warning, nothing written. Once frames flow: the detector snapshot is the
    full frame, the classifier snapshot the hand crop, named `snapshot_<stage>_<ts>[_i].jpg`."""
    camera = _camera()
    deliveries: list[Delivery] = []
    gesture = _input(camera, ScriptedClassifier(_scores("fist", 0.9)), deliveries)
    when = datetime(2026, 9, 17, 14, 30, 5, tzinfo=UTC)

    early = gesture.save_detector_snapshot(tmp_path, when)
    assert early.startswith("⚠️ HandStage") and not list(tmp_path.iterdir())

    gesture.start()
    try:
        assert _wait_until(
            lambda: deliveries == [(1, "stop")]
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


def test_classifier_snapshot_without_a_hand_reports_no_crop(tmp_path: Path) -> None:
    camera = _camera(_EMPTY_FRAME)
    gesture = _input(camera, ScriptedClassifier(_scores("fist", 0.9)), [])
    gesture.start()
    try:
        assert _wait_until(lambda: gesture._gestures.published_count > 0)  # pyright: ignore[reportPrivateUsage]
        status = gesture.save_classifier_snapshot(tmp_path)
    finally:
        gesture.stop()
    assert "no crop to save" in status
    assert not list(tmp_path.iterdir())


def test_snapshot_into_an_unwritable_folder_reports_instead_of_raising(
    tmp_path: Path,
) -> None:
    camera = _camera()
    gesture = _input(camera, ScriptedClassifier(_scores("fist", 0.9)), [])
    gesture.start()
    try:
        assert _wait_until(lambda: gesture._hands.published_count > 0)  # pyright: ignore[reportPrivateUsage]
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("a file where the folder should be")
        status = gesture.save_detector_snapshot(blocker)
    finally:
        gesture.stop()
    assert status.startswith("⚠️ HandStage:")


# --- The trigger predicate -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, "stop", True),  # shown
        ("two", "three", True),  # replaced by another sign, no gap
        ("stop", None, False),  # gone
        ("stop", "stop", False),  # still shown
        (None, None, False),  # still nothing
    ],
)
def test_sign_shown_predicate(old: str | None, new: str | None, expected: bool) -> None:
    assert sign_shown(old, new) is expected


# --- The persona section this module owns --------------------------------------------------------


def test_hand_signs_prompt_section_names_the_whole_vocabulary_and_the_stop_rule() -> (
    None
):
    """The section the app appends when vision is on must match the SIGNS table it describes —
    every sign the World can carry is named — and carry the one sign that comes with an instruction."""
    section = hand_signs_prompt_section()

    assert section.startswith("\n\n## Hand signs\n\n")
    for sign in set(SIGNS.values()):
        assert f"`{sign}`" in section
    assert (
        "mute" not in section
    )  # dropped from the vocabulary, and from the persona with it
    assert "`stop`" in section
    assert "cancel_command" in section  # the stop rule, spelled out
    assert "hand_gesture_<n>" in section
    # A sign state is one-shot (the app's TTL): the section says it names what the person just
    # did, not a hand's standing state, and never promises a "no sign" state.
    assert "not what they are still holding" in section
    assert "showing none" not in section
