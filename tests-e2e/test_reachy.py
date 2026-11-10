"""The end-to-end tier on the Reachy Mini embodiment (specs/e2e.md): the real app — Deepgram Flux,
Claude Haiku, phonon, the hand models — in the MuJoCo sim the bridge's harness runs, the cable as
the robot's mic and hand photos as props before its camera. The scenarios are `scenarios.py`'s,
plus the emotion only a body can play."""

from __future__ import annotations

import pytest
import scenarios
from support import E2E

# The sim must render its camera (the viewer on macOS, EGL on Linux), hold the hand props, and
# have audio; otherwise these skip, or fail under `REACHY_MINI_E2E_REQUIRED_CAPS`.
pytestmark = pytest.mark.requires("audio", "camera", "props:hand")


def test_startup(reachy: E2E) -> None:
    scenarios.startup(reachy)


def test_a_spoken_turn_is_heard_and_answered(reachy: E2E) -> None:
    scenarios.spoken_turn(reachy)


def test_each_hand_sign_reaches_the_agent(reachy: E2E) -> None:
    scenarios.hand_signs(reachy)


def test_an_emotion_asked_for_is_played(reachy: E2E) -> None:
    scenarios.emotion(reachy)
