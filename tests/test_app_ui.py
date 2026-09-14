"""Functional tests for the Gradio presentation layer's view logic (`app_ui`) — no browser.

Covers the app's `display_entry` hook (how its World entries read to a person in the contrib panels)
and the ASR status line. The three observability panels themselves are WICA's `wica.contrib.gradio`
components, tested in WICA's own suite, not here. See specs/app.md ("What the user sees").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from asr_engine.engine import ASREngine
from wica import CommandExecution
from wica.contrib.gradio import EntryDisplay
from wica.world import WorldEntry, WorldEntryVersion

from reachy_mini_interaction_wica.app import AppSystem
from reachy_mini_interaction_wica.app_ui import display_entry, tick_asr
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource


def _entry(key: str, value: Any) -> WorldEntry:
    return WorldEntry(
        key=key,
        type=type(value),
        bypass_coalescing=False,
        current=WorldEntryVersion(id=1, value=value, timestamp=datetime.now(UTC)),
        previous=None,
    )


def _command_entry(call_id: str, name: str, args: dict[str, Any]) -> WorldEntry:
    return _entry(
        f"agent:command:{call_id}",
        CommandExecution(name=name, args=args, state="running"),
    )


def test_display_entry_labels_the_app_entries() -> None:
    """The person-facing rendering of the app's entries: the speech input as what was said, the
    voice titled by channel with the spoken text as body, everything else left to the generic
    default (None)."""
    assert display_entry(_entry("speech_input", "hi")) == EntryDisplay('🗣️ "hi"')
    # An empty final (closed on silence) still reads as speech — an empty quote, not a blank.
    assert display_entry(_entry("speech_input", "")) == EntryDisplay('🗣️ ""')
    assert display_entry(
        _command_entry("c1", "say", {"text": "hi there"})
    ) == EntryDisplay("🗣️ say", "hi there")
    assert (
        display_entry(_command_entry("c2", "play_emotion", {"name": "happy"})) is None
    )
    assert display_entry(_entry("something_else", "value")) is None


class _FakeEngine:
    """The slice of `ASREngine` the bridge's `status()` reads."""

    def __init__(self) -> None:
        self.on_speech_segment: Any = None
        self.dictating = False
        self.segmentation_mode = "utterance"

    def status(self) -> dict[str, Any]:
        return {"running": True}


class _Mic:
    """Stands in for a `RobotMicSource` — only its `capture_error` is read."""

    def __init__(self, capture_error: str | None) -> None:
        self.capture_error = capture_error


def _system_with_mic(capture_error: str | None) -> AppSystem:
    bridge = AsrBridge(
        cast(ASREngine, _FakeEngine()),
        on_final_segment=lambda _text: None,
        mic=cast(RobotMicSource, _Mic(capture_error)),
    )
    return AppSystem(
        loop=None,
        api=None,
        wica=None,
        bridge=bridge,
        voice=None,
        missing=[],
        submit_speech=lambda _text: None,
    )


def test_asr_status_line_flags_a_dead_mic_stream() -> None:
    """The engine still says "running", but the status line tells the user capture has stalled."""
    _, healthy, _ = tick_asr(_system_with_mic(None))
    assert "running" in healthy and "capture" not in healthy

    _, stalled, _ = tick_asr(_system_with_mic("mic stream ended"))
    assert "running" in stalled
    assert "capture" in stalled and "mic stream ended" in stalled
