"""Functional tests for the Gradio presentation layer's view logic (`app_ui`) — no browser.

Drives `PanelState` the way WICA's instrumentation Events do (`on_trigger` / `on_prompt` /
`on_command`) and asserts on what the timer callbacks return to Gradio: a fresh value when the view
changed, a `gr.update()` no-op when it didn't. Also covers the World render gate and the ASR status
line. See specs/app.md ("What the user sees").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import gradio as gr
from asr_engine.engine import ASREngine
from langchain_core.messages import HumanMessage
from wica import CommandIssued
from wica.world import WorldEntry, WorldEntryVersion

from reachy_mini_interaction_wica.app import AppSystem
from reachy_mini_interaction_wica.app_ui import PanelState, _RenderGate, tick_asr
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource

_NO_OP = gr.update()


def _speech_entry(text: str) -> WorldEntry:
    return WorldEntry(
        key="speech_input",
        type=str,
        bypass_coalescing=False,
        current=WorldEntryVersion(id=1, value=text, timestamp=datetime.now(UTC)),
        previous=None,
    )


def _step(panels: PanelState, heard: str, *, prompt: str) -> None:
    """One reasoning step as WICA reports it: the trigger, then the prompt sent to the model."""
    panels.on_trigger(_speech_entry(heard))
    panels.on_prompt([HumanMessage(prompt)])


def test_render_gate_only_reports_real_changes() -> None:
    gate = _RenderGate()
    assert gate.changed("<table>a</table>")  # first render always goes out
    assert not gate.changed("<table>a</table>")  # identical ⇒ skip the push
    assert gate.changed("<table>b</table>")  # a real change goes out again
    assert gate.changed("<table>a</table>")  # …including a change back


def test_tick_conversation_pushes_only_on_new_events() -> None:
    panels = PanelState()
    assert panels.tick_conversation()[0] == _NO_OP  # nothing happened yet

    _step(panels, "hi", prompt="PROMPT-1")
    first = panels.tick_conversation()[0]
    assert isinstance(first, list)
    assert [m["role"] for m in first] == ["user", "assistant"]  # the input, then the reaction group
    assert "hi" in first[0]["content"]

    assert panels.tick_conversation()[0] == _NO_OP  # no new events ⇒ no re-push (no autoscroll)

    panels.on_command(CommandIssued(name="say", args={"text": "hello back"}))
    grown = panels.tick_conversation()[0]
    assert isinstance(grown, list) and len(grown) == 3
    assert grown[-1]["content"] == "hello back"
    assert grown[-1]["metadata"]["parent_id"] == first[1]["metadata"]["id"]  # nested in the step


def test_tick_conversation_snaps_prompt_selector_to_newest() -> None:
    panels = PanelState()
    _step(panels, "one", prompt="PROMPT-1")
    _step(panels, "two", prompt="PROMPT-2")

    _, selector, prompt = panels.tick_conversation()
    assert selector["value"] == 1  # the newest of the two steps
    assert len(selector["choices"]) == 2
    assert "PROMPT-2" in prompt["value"]

    _, selector_again, prompt_again = panels.tick_conversation()
    assert selector_again == _NO_OP and prompt_again == _NO_OP  # not re-pushed without a new step

    # Picking an older step from the dropdown shows that step's exact prompt.
    assert "PROMPT-1" in panels.on_select_prompt(0)


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
        panels=PanelState(),
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
