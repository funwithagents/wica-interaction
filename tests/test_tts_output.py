"""Functional tests for ``VoiceOutput`` — driven against a fake ``ReachyMiniApi`` (no audio, no robot).

Tests drive the public ``VoiceOutput.say`` coroutine the way WICA's output Command will, and assert
on its guarded, non-raising contract: ``"OK"`` on success (after calling ``api.say`` through the
robot), the empty-text error string without touching the robot, and any ``api.say`` exception mapped
to a readable string rather than raised. See specs/tts-output.md ("Testing").

``say`` is a coroutine; tests drive it with ``asyncio.run`` (no async-test plugin required).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

from reachy_mini_bridge.api import ReachyMiniApi

from reachy_mini_interaction_wica.tts_output import VoiceOutput


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeReachyMiniApi:
    """Minimal stand-in exposing only ``say`` — the one method ``VoiceOutput`` calls.

    Records each spoken text in ``said``; set ``raise_message`` to make ``say`` raise and exercise
    ``VoiceOutput``'s error path (the bridge's ``say`` raises, rather than returning an error string).
    """

    def __init__(self, *, raise_message: str | None = None) -> None:
        self.said: list[str] = []
        self._raise_message = raise_message

    async def say(self, text: str) -> None:
        self.said.append(text)
        if self._raise_message is not None:
            raise RuntimeError(self._raise_message)


def test_say_speaks_through_robot_and_returns_ok() -> None:
    fake = FakeReachyMiniApi()
    voice = VoiceOutput(cast("ReachyMiniApi", fake))

    result = run(voice.say("hello"))

    assert result == "OK"
    assert fake.said == ["hello"]


def test_empty_text_is_guarded_without_calling_robot() -> None:
    fake = FakeReachyMiniApi()
    voice = VoiceOutput(cast("ReachyMiniApi", fake))

    assert run(voice.say("")) == "TTS error: text must not be empty"
    assert run(voice.say("   ")) == "TTS error: text must not be empty"
    assert fake.said == []  # never reached the robot


def test_say_error_is_mapped_not_raised() -> None:
    fake = FakeReachyMiniApi(raise_message="boom")
    voice = VoiceOutput(cast("ReachyMiniApi", fake))

    result = run(voice.say("hi"))

    assert result == "TTS error: boom"
    assert fake.said == ["hi"]  # it did attempt to speak before failing
