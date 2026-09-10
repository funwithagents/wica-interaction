"""Functional tests for ``VoiceOutput`` — driven against a fake ``TTSEngine`` (no audio, no network).

Tests drive the public ``VoiceOutput.say`` coroutine the way WICA's output Command will, and assert
on the guarded, non-raising contract inherited from ``TTSTools``: ``"OK"`` on success, the empty-text
error string without touching the engine, and a ``TTSError`` mapped to a readable string rather than
raised. See specs/tts-output.md ("Testing").

``say`` is a coroutine; tests drive it with ``asyncio.run`` (no async-test plugin required).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError

from reachy_mini_interaction_wica.tts_output import VoiceOutput


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeTTSEngine:
    """Minimal stand-in exposing only ``say`` — the one method ``TTSTools`` calls.

    Records each spoken text in ``calls``; set ``raise_message`` to make ``say`` raise ``TTSError`` and
    exercise ``VoiceOutput``'s error path.
    """

    def __init__(self, *, raise_message: str | None = None) -> None:
        self.calls: list[str] = []
        self._raise_message = raise_message

    async def say(self, text: str) -> None:
        self.calls.append(text)
        if self._raise_message is not None:
            raise TTSError(self._raise_message)


def test_say_speaks_and_returns_ok() -> None:
    fake = FakeTTSEngine()
    voice = VoiceOutput(cast("TTSEngine", fake))

    result = run(voice.say("hello"))

    assert result == "OK"
    assert fake.calls == ["hello"]


def test_empty_text_is_guarded_without_calling_engine() -> None:
    fake = FakeTTSEngine()
    voice = VoiceOutput(cast("TTSEngine", fake))

    result = run(voice.say(""))

    assert result == "TTS error: text must not be empty"
    assert fake.calls == []


def test_synthesis_error_is_mapped_not_raised() -> None:
    fake = FakeTTSEngine(raise_message="boom")
    voice = VoiceOutput(cast("TTSEngine", fake))

    result = run(voice.say("hi"))

    assert result == "TTS error: boom"
    assert fake.calls == ["hi"]
