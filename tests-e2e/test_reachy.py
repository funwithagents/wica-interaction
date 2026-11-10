"""The end-to-end tier on the Reachy Mini embodiment (specs/e2e.md): the real app over the MuJoCo
sim the bridge's harness runs — the cable as the robot's mic, hand photos as props of its scene, and
no echo (the real robot cancels it in hardware). The scenarios are `scenarios.py`'s, shared with the
computer."""

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
    scenarios.question(reachy)


def test_each_hand_sign_reaches_the_agent(reachy: E2E) -> None:
    scenarios.hand_signs(reachy)


class TestConversation:
    """One conversation on one running app, a step per test, in order (specs/e2e.md, "The
    conversation")."""

    def test_1_ready(self, reachy_conversation: E2E) -> None:
        scenarios.startup(reachy_conversation)

    def test_2_a_question_is_answered(self, reachy_conversation: E2E) -> None:
        scenarios.question(reachy_conversation)

    def test_3_a_sign_reaches_the_agent(self, reachy_conversation: E2E) -> None:
        scenarios.sign(reachy_conversation)

    def test_4_an_emotion_is_played(self, reachy_conversation: E2E) -> None:
        scenarios.emotion(reachy_conversation)

    def test_5_a_spoken_stop_interrupts(self, reachy_conversation: E2E) -> None:
        scenarios.spoken_stop(reachy_conversation)

    def test_6_a_shown_stop_interrupts(self, reachy_conversation: E2E) -> None:
        scenarios.shown_stop(reachy_conversation)

    def test_7_a_note_is_dictated(self, reachy_conversation: E2E) -> None:
        scenarios.note(reachy_conversation)

    def test_8_the_app_never_heard_itself(self, reachy_conversation: E2E) -> None:
        scenarios.never_heard_itself(reachy_conversation)
