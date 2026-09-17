"""Live tier: the hand-sign input over the REAL models (MediaPipe hand landmarker + the HaGRID
ViT), fed `vision-modules`' bundled fixture photos through a scripted camera source.

No camera and no body: the source serves one fixed photo the way a real embodiment's camera would
serve one frame. The models download on first run (~340 MB); a checkout without the sibling fixtures
skips, and a model that cannot be loaded (no network on first run, a missing extra) is reported by
the pipeline as a stage error and turned into a skip here rather than a failure.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from wica_interaction.gesture_input import GestureInput

# One delivery: (hand number, that hand's sign or None).
Delivery = tuple[int, str | None]

_FIXTURES = (
    Path(__file__).resolve().parents[2] / "vision-modules" / "tests-e2e" / "fixtures"
)
_MODEL_TIMEOUT_S = 180.0  # first run: model downloads


def _fixture(name: str) -> np.ndarray:
    path = _FIXTURES / name
    if not path.exists():
        pytest.skip(f"vision-modules fixture {path} not found")
    image = cv2.imread(str(path))
    if image is None:
        pytest.skip(f"could not decode {path}")
    return image


class ScriptedCameraSource:
    """The embodiment's camera, scripted: one fixed photo at the configured rate. Duplicated from
    the fast tier's own fake — the two tiers do not import each other (specs/testing.md)."""

    capture_error: str | None = None

    def __init__(self, frame: np.ndarray, *, fps: float = 5.0) -> None:
        self.frame = frame
        self._interval = 1.0 / fps
        self._closed = threading.Event()
        self._closed.set()

    def open(self) -> None:
        self._closed.clear()

    def read(self) -> np.ndarray | None:
        while not self._closed.is_set():
            self._closed.wait(self._interval)
            if self._closed.is_set():
                break
            return self.frame
        return None

    def close(self) -> None:
        self._closed.set()

    def fps(self) -> float:
        return 1.0 / self._interval


def _wait_until(predicate: Any, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _run(frame: np.ndarray) -> tuple[list[Delivery], dict]:
    """Run the real pipeline over *frame* until the classifier has produced a result (or a stage
    error), then return the deliveries and the final status."""
    deliveries: list[Delivery] = []
    gesture = GestureInput(
        ScriptedCameraSource(frame),
        on_gesture=lambda hand, sign: deliveries.append((hand, sign)),
        threshold=0.55,
        fps=5.0,
    )
    gesture.start()
    try:

        def classified_or_failed() -> bool:
            status = gesture.status()
            return status["error"] is not None or any(
                hand["confidence"] > 0.0 for hand in status["hands"]
            )

        assert _wait_until(classified_or_failed, _MODEL_TIMEOUT_S), (
            "the pipeline produced neither a classification nor an error"
        )
        status = gesture.status()
        if status["error"] is not None:
            pytest.skip(f"vision models unavailable: {status['error']}")
        # Give the watcher one more beat to deliver what the classifier just published.
        time.sleep(0.5)
        return deliveries, gesture.status()
    finally:
        gesture.stop()


@pytest.mark.parametrize(
    ("photo", "sign"),
    [
        ("hand_fist.jpg", "stop"),
        ("hand_stop.jpg", "stop"),
        ("hand_palm.jpg", "five"),
        ("hand_one.jpg", "one"),
        ("hand_three2.jpg", "three"),
    ],
)
def test_real_models_deliver_each_photos_sign(photo: str, sign: str) -> None:
    """The real classifier's label, through the app's `SIGNS` table, is the value hand 1 carries."""
    deliveries, status = _run(_fixture(photo))
    assert deliveries == [(1, sign)], status


def test_real_models_deliver_nothing_with_no_hand() -> None:
    """No hand ⇒ the hand stage publishes present=False results; the classifier publishes empty
    ones (confidence stays 0.0), so wait on the hand stage's output instead."""
    deliveries: list[Delivery] = []
    gesture = GestureInput(
        ScriptedCameraSource(_fixture("no_hand.jpg")),
        on_gesture=lambda hand, sign: deliveries.append((hand, sign)),
        fps=5.0,
    )
    gesture.start()
    try:
        hands = gesture._hands  # pyright: ignore[reportPrivateUsage] - the stage's own counter

        def detected_or_failed() -> bool:
            return hands.published_count >= 3 or gesture.status()["error"] is not None

        assert _wait_until(detected_or_failed, _MODEL_TIMEOUT_S)
        status = gesture.status()
        if status["error"] is not None:
            pytest.skip(f"vision models unavailable: {status['error']}")
        assert status["hands"] == [] and deliveries == []
    finally:
        gesture.stop()
