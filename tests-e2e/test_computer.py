"""The end-to-end tier on the computer embodiment (specs/e2e.md): the real app — Deepgram Flux,
Claude Haiku, phonon, the hand models — with the cable as its mic and an image file as its webcam.
The scenarios are `scenarios.py`'s, shared with the robot."""

from __future__ import annotations

import scenarios
from support import E2E


def test_startup(computer: E2E) -> None:
    scenarios.startup(computer)


def test_a_spoken_turn_is_heard_and_answered(computer: E2E) -> None:
    scenarios.spoken_turn(computer)


def test_each_hand_sign_reaches_the_agent(computer: E2E) -> None:
    scenarios.hand_signs(computer)
