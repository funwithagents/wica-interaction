"""Live tier: the fist gesture input over the REAL models (MediaPipe hand landmarker + the HaGRID
ViT), fed `vision-modules`' bundled fixture photos through a fake robot api.

No robot: the fake api's `get_camera_frame()` serves one fixed photo, the way the real bridge would
serve one camera frame. The models download on first run (~340 MB); a checkout without the sibling
fixtures skips, and a model that cannot be loaded (no network on first run, a missing extra) is
reported by the pipeline as a stage error and turned into a skip here rather than a failure.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest
from reachy_mini_bridge.api import ReachyMiniApi

from reachy_mini_interaction_wica.gesture_input import FIST, GestureInput

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


class FakeCameraApi:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame

    async def get_camera_frame(self) -> np.ndarray | None:
        return self.frame


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)
    loop.close()


def _wait_until(predicate: Any, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _run(
    loop: asyncio.AbstractEventLoop, frame: np.ndarray
) -> tuple[list[str | None], dict]:
    """Run the real pipeline over *frame* until the classifier has produced a result (or a stage
    error), then return the deliveries and the final status."""
    deliveries: list[str | None] = []
    gesture = GestureInput(
        cast(ReachyMiniApi, FakeCameraApi(frame)),
        loop,
        on_gesture=deliveries.append,
        threshold=0.55,
        fps=5.0,
    )
    gesture.start()
    try:

        def classified_or_failed() -> bool:
            status = gesture.status()
            return status["error"] is not None or status["confidence"] > 0.0

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


def test_real_models_deliver_fist_for_the_fist_photo(
    loop: asyncio.AbstractEventLoop,
) -> None:
    deliveries, status = _run(loop, _fixture("hand_fist.jpg"))
    assert status["label"] == FIST, status
    assert deliveries == [FIST]


def test_real_models_deliver_nothing_for_a_non_fist_photo(
    loop: asyncio.AbstractEventLoop,
) -> None:
    deliveries, status = _run(loop, _fixture("hand_palm.jpg"))
    assert status["label"] != FIST, status
    assert deliveries == []


def test_real_models_deliver_nothing_with_no_hand(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """No hand ⇒ the hand stage publishes present=False results; the classifier publishes empty
    ones (confidence stays 0.0), so wait on the hand stage's output instead."""
    deliveries: list[str | None] = []
    gesture = GestureInput(
        cast(ReachyMiniApi, FakeCameraApi(_fixture("no_hand.jpg"))),
        loop,
        on_gesture=deliveries.append,
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
        assert status["label"] is None and deliveries == []
    finally:
        gesture.stop()
