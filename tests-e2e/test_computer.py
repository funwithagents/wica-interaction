"""The end-to-end tier on the computer embodiment (specs/e2e.md): the real app — Deepgram Flux,
Claude Haiku, phonon, the hand models — with the cable as its mic and an image file as its webcam.
The scenarios are `scenarios.py`'s, shared with the robot."""

from __future__ import annotations

import pytest
import scenarios
from support import E2E


def test_startup(computer: E2E) -> None:
    scenarios.startup(computer)


def test_a_spoken_turn_is_heard_and_answered(computer: E2E) -> None:
    scenarios.spoken_turn(computer)


def test_each_hand_sign_reaches_the_agent(computer: E2E) -> None:
    scenarios.hand_signs(computer)


@pytest.mark.xfail(
    reason="with echo cancellation on, the person's voice can be suppressed while the app speaks, "
    "so a spoken stop never reaches the agent (specs/e2e.md, open question 4)",
)
def test_speaking_is_interrupted_by_a_spoken_stop(computer: E2E) -> None:
    scenarios.interrupt_spoken(computer)


def test_speaking_is_interrupted_by_a_stop_sign(computer: E2E) -> None:
    scenarios.interrupt_sign(computer)


def test_a_dictated_passage_arrives_as_one_input(computer: E2E) -> None:
    scenarios.dictation(computer)
