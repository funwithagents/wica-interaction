"""The end-to-end tier on the computer embodiment (specs/e2e.md): the real app — Deepgram Flux,
Claude Haiku, phonon, the hand models — with the cable as its mic and an image file as its webcam,
its echo cancellation on when the room plays its voice back. The scenarios are `scenarios.py`'s,
shared with the robot."""

from __future__ import annotations

import scenarios
from support import E2E


def test_startup(computer: E2E) -> None:
    scenarios.startup(computer)


def test_a_spoken_turn_is_heard_and_answered(computer: E2E) -> None:
    scenarios.question(computer)


def test_each_hand_sign_reaches_the_agent(computer: E2E) -> None:
    scenarios.hand_signs(computer)


class TestConversation:
    """One conversation on one running app, a step per test, in order (specs/e2e.md, "The
    conversation")."""

    def test_1_ready(self, computer_conversation: E2E) -> None:
        scenarios.startup(computer_conversation)

    def test_2_a_question_is_answered(self, computer_conversation: E2E) -> None:
        scenarios.question(computer_conversation)

    def test_3_a_sign_reaches_the_agent(self, computer_conversation: E2E) -> None:
        scenarios.sign(computer_conversation)

    def test_5_a_spoken_stop_interrupts(self, computer_conversation: E2E) -> None:
        scenarios.spoken_stop(computer_conversation)

    def test_6_a_shown_stop_interrupts(self, computer_conversation: E2E) -> None:
        scenarios.shown_stop(computer_conversation)

    def test_7_a_note_is_dictated(self, computer_conversation: E2E) -> None:
        scenarios.note(computer_conversation)

    def test_8_the_app_never_heard_itself(self, computer_conversation: E2E) -> None:
        scenarios.never_heard_itself(computer_conversation)
