"""Functional tests for the Gradio presentation layer's view logic (`app_ui`) — no browser.

Covers the app's `display_entry` hook (how its World entries read to a person in the contrib panels)
and the ASR status line. The three observability panels themselves are WICA's `wica.contrib.gradio`
components, tested in WICA's own suite, not here. See specs/app.md ("What the user sees").
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import gradio as gr
import numpy as np
import pytest
from app_setup.app import App, AppModel, Section
from app_setup.folder import ConfigFolder
from asr_engine.engine import ASREngine
from asr_engine.vad.gate import VadState
from wica import CommandExecution
from wica.contrib.gradio import EntryDisplay, ReactionLog, TranscriptLog
from wica.events import Event
from wica.world import WorldEntry, WorldEntryVersion

from tests.fakes import FakeEmbodiment
from tests.test_latency import reaction_trace
from wica_interaction.app_ui import (
    LatencyPanel,
    _interaction_surfaces,
    build_page,
    display_entry,
    entry_display,
    entry_label,
    session_buttons,
    session_status,
    tick_asr,
    tick_view,
    tick_vision,
)
from wica_interaction.asr_bridge import AsrBridge
from wica_interaction.embodiment import EmbodimentView
from wica_interaction.gesture_input import GestureInput
from wica_interaction.latency import LatencyCollector
from wica_interaction.session import Running, Session
from wica_interaction.system import AppSystem


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


def test_entry_display_labels_the_bodys_entries_by_their_own_wording() -> None:
    """An embodiment's entry is labelled by its own `display_fn` (the robot's `👤 …`); a cleared one
    (`None`, still labelling a reaction whose prompt carried it) and every app entry fall through to
    `display_entry`, so the app's labels are unchanged."""
    system = AppSystem(
        loop=None,
        loop_thread=None,
        display_name="fake body",
        embodiment=None,
        wica=None,
        bridge=None,
        gesture=None,
        missing=[],
        submit_speech=lambda _text: None,
        input_displays={"person_event": lambda value: f"👤 {value}"},
    )
    display = entry_display(system)
    assert display(_entry("person_event", "A person came into view.")) == EntryDisplay(
        "👤 A person came into view."
    )
    assert display(_entry("person_event", None)) is None
    assert display(_entry("speech_input", "hi")) == EntryDisplay('🗣️ "hi"')
    assert display(_entry("something_else", "value")) is None


class _FakeEngine:
    """The slice of `ASREngine` the bridge's `status()` reads."""

    def __init__(self) -> None:
        self.on_speech_segment: Any = None
        self.dictating = False
        self.segmentation_mode = "utterance"
        self.vad_state: VadState | None = None  # no VAD configured

    def status(self) -> dict[str, Any]:
        return {"running": True}


def _system_with_mic(capture_error: str | None) -> AppSystem:
    bridge = AsrBridge(
        cast(ASREngine, _FakeEngine()),
        on_final_segment=lambda _text: None,
        mic_error=lambda: capture_error,
    )
    return _system(bridge=bridge)


def _system(
    *,
    bridge: AsrBridge | None = None,
    gesture: GestureInput | None = None,
    embodiment: FakeEmbodiment | None = None,
    display_name: str | None = None,
) -> AppSystem:
    """A bare `AppSystem` carrying only what the view under test reads. `display_name` defaults to
    the body's, as `build_system` sets it; pass it alone for a body that failed to enter."""
    if display_name is None and embodiment is not None:
        display_name = embodiment.display_name
    return AppSystem(
        loop=None,
        loop_thread=None,
        display_name=display_name,
        embodiment=embodiment,
        wica=None,
        bridge=bridge,
        gesture=gesture,
        missing=[],
        submit_speech=lambda _text: None,
    )


def test_asr_status_line_flags_a_dead_mic_stream() -> None:
    """The engine still says "running", but the status line tells the user capture has stalled."""
    _, healthy, _, _ = tick_asr(_system_with_mic(None))
    assert "running" in healthy and "capture" not in healthy

    _, stalled, _, _ = tick_asr(_system_with_mic("mic stream ended"))
    assert "running" in stalled
    assert "capture" in stalled and "mic stream ended" in stalled


def test_the_asr_section_says_whether_speech_is_detected() -> None:
    engine = _FakeEngine()
    system = _system(
        bridge=AsrBridge(cast(ASREngine, engine), on_final_segment=lambda _t: None)
    )
    assert "no VAD" in tick_asr(system)[3]
    engine.vad_state = VadState()
    assert tick_asr(system)[3] == "**Voice activity:** ⚪ no speech"
    engine.vad_state = VadState(speaking=True)
    assert tick_asr(system)[3] == "**Voice activity:** 🟢 speech detected"
    engine.vad_state = VadState(speaking=True, failed_open=True)
    assert "VAD failed" in tick_asr(system)[3]
    assert tick_asr(_system())[3] == ""  # no ASR at all: no line


# --- The Vision line (specs/app.md, "Vision line"; specs/gesture-input.md) --------------------


class _Gesture:
    """Stands in for a `GestureInput` — only `status()` is read."""

    def __init__(self, **status: Any) -> None:
        base = {
            "running": True,
            "signs": [None, None],
            "hands": [],
            "max_hands": 2,
            "target_fps": 5.0,
            "stream_fps": None,
            "hand_fps": None,
            "classifier_fps": None,
            "capture_error": None,
            "error": None,
        }
        self._status = {**base, **status}

    def status(self) -> dict[str, Any]:
        return dict(self._status)


def _system_with_gesture(gesture: _Gesture | None) -> AppSystem:
    return _system(gesture=cast(GestureInput, gesture) if gesture is not None else None)


def test_display_entry_labels_each_hand_sign_input() -> None:
    assert display_entry(_entry("hand_gesture_1", "stop")) == EntryDisplay(
        "✋ hand 1: stop"
    )
    assert display_entry(_entry("hand_gesture_2", "two")) == EntryDisplay(
        "✋ hand 2: two"
    )
    assert display_entry(_entry("hand_gesture_1", None)) == EntryDisplay(
        "✋ hand 1: no sign"
    )
    # A key that only looks like one: the generic default (None) still applies.
    assert display_entry(_entry("hand_gesture_left", "stop")) is None


def test_vision_line_without_gesture_input_says_off() -> None:
    assert "off" in tick_vision(_system_with_gesture(None))


def test_vision_shows_each_hand_slot_as_a_row_and_the_rates_as_a_list() -> None:
    no_hand = tick_vision(_system_with_gesture(_Gesture()))
    assert "#### Hand signs\n\n**Status:** running · up to 2 hands" in no_hand
    assert "| #1 | no hand | — |" in no_hand and "| #2 | no hand | — |" in no_hand
    # Before the first sampling window the achieved rates are a placeholder; the stages show their
    # target, the camera (which runs at its own rate) none.
    assert "- **camera:** — fps\n" in no_hand
    assert "- **hand stage:** — fps (target 5)" in no_hand
    assert "- **classifier:** — fps (target 5)" in no_hand
    # The rates come first, above the hand signs' subheading.
    assert no_hand.startswith("- **camera:**")
    assert no_hand.index("- **classifier:**") < no_hand.index("#### Hand signs")

    rates = tick_vision(
        _system_with_gesture(
            _Gesture(stream_fps=5.02, hand_fps=4.93, classifier_fps=2.5, target_fps=5.0)
        )
    )
    assert "- **camera:** 5.0 fps\n" in rates
    assert "- **hand stage:** 4.9 fps (target 5)" in rates
    assert "- **classifier:** 2.5 fps (target 5)" in rates

    # What the hand shows, beside the sign it last delivered.
    signing = tick_vision(
        _system_with_gesture(
            _Gesture(
                signs=["stop", None], hands=[{"label": "fist", "confidence": 0.87}]
            )
        )
    )
    assert "| #1 | fist (0.87) | stop |" in signing

    # A hand whose top class missed the threshold: the raw score shows, unvalidated, no sign.
    weak = tick_vision(
        _system_with_gesture(_Gesture(hands=[{"label": None, "confidence": 0.5}]))
    )
    assert "| #1 | below threshold (0.50) | — |" in weak


def test_vision_lists_every_hand_in_slot_order() -> None:
    """Three slots: each hand shows under its number — a hand classified outside the vocabulary, a
    fist on the second, a hand at the frame edge with no crop — under the configured cap."""
    text = tick_vision(
        _system_with_gesture(
            _Gesture(
                signs=[None, "stop", None],
                max_hands=3,
                hands=[
                    {"label": "call", "confidence": 0.91},
                    {"label": "fist", "confidence": 0.77},
                    {"label": None, "confidence": 0.0},
                ],
            )
        )
    )
    assert "up to 3 hands" in text
    assert (
        "| #1 | call (0.91) | — |\n| #2 | fist (0.77) | stop |\n| #3 | no crop | — |"
        in (text)
    )


def test_vision_line_flags_camera_and_stage_errors() -> None:
    camera = tick_vision(
        _system_with_gesture(_Gesture(capture_error="camera read failed: no camera"))
    )
    assert "camera" in camera and "no camera" in camera

    stalled = tick_vision(
        _system_with_gesture(_Gesture(capture_error="no camera frame for 3 s"))
    )
    # Each problem on a line of its own, after the rates.
    assert stalled.endswith("\n\n⚠️ **camera:** no camera frame for 3 s")

    stage = tick_vision(_system_with_gesture(_Gesture(error="model download failed")))
    assert "error" in stage and "model download failed" in stage

    stopped = tick_vision(_system_with_gesture(_Gesture(running=False)))
    assert "stopped" in stopped


# --- The page (specs/app.md, "What the user sees"; specs/session.md) ---------------------------


def _model(tmp_path: Path) -> AppModel:
    import wica.setup

    app = App(
        name="wica-interaction-fake",
        title="WICA Interaction — Fake",
        host="wica-interaction",
        sections=(Section("wica", wica.setup),),
        default_config={"wica": {"agent": {"provider": "fake", "model": "scripted"}}},
        autostart=True,
    )
    return AppModel(app, ConfigFolder(tmp_path / "app", app.default_config))


def _running(system: AppSystem) -> Running:
    display = entry_display(system)
    return Running(
        system=system,
        transcript=TranscriptLog(system.wica, display),
        reactions=ReactionLog(system.wica, display),
        close=lambda: None,
    )


def test_the_page_has_its_four_tabs_and_is_named_after_the_app(tmp_path: Path) -> None:
    model = _model(tmp_path)
    page = build_page(model, Session(lambda *_: _running(_system())))
    assert page.title == "WICA Interaction — Fake"
    config = page.get_config_file()
    tabs = [
        c["props"].get("label") for c in config["components"] if c["type"] == "tabitem"
    ]
    assert tabs == ["Interaction", "Latency", "Setup", "Secrets"]


def test_a_running_sessions_surfaces_are_drawn_over_its_own_objects() -> None:
    """What the Interaction render draws for a running session: the body's camera, the ASR and the
    Vision, the debug panels, and the banner when something is missing."""
    system = replace(
        _system(embodiment=FakeEmbodiment()), missing=["ASR: no key under 'X'"]
    )
    running = _running(system)
    session = Session(lambda *_: running)
    with gr.Blocks() as blocks:
        _interaction_surfaces(session, running)
    texts = [
        str(c["props"].get("value"))
        for c in blocks.get_config_file()["components"]
        if c["type"] == "markdown"
    ]
    assert any("no key under" in t for t in texts)
    assert any("embodied in a fake body" in t for t in texts)


class _Stub:
    """A session whose status and reasons a test sets."""

    def __init__(self, status: str, reasons: list[str] | None = None) -> None:
        self.status = status
        self.reasons = reasons or []


@pytest.mark.parametrize(
    ("status", "buttons", "words"),
    [
        ("stopped", (True, False), "ready to start"),
        ("starting", (False, False), "Starting"),
        ("running", (False, True), "Running"),
        ("stopping", (False, False), "Stopping"),
    ],
)
def test_the_session_bar_follows_the_status(
    status: str, buttons: tuple[bool, bool], words: str
) -> None:
    session = cast(Session, _Stub(status))
    assert session_buttons(session) == buttons
    assert words in session_status(session)


def test_the_bar_says_why_the_session_cannot_start() -> None:
    """The last Start's reasons and the saved config's blockers, each once, with where to fix."""
    session = cast(Session, _Stub("stopped", ["wica: model is required"]))
    text = session_status(session, ["wica: model is required", "HF_TOKEN is not set"])
    assert "Not started" in text and "Setup" in text
    assert text.count("model is required") == 1
    assert "HF_TOKEN is not set" in text


# --- The Camera panel ------------------------------------------------------------------------------


def test_the_camera_panel_shows_the_bodys_view() -> None:
    """The frame and the status come from the body's `view()`; without a frame yet, a note before
    the status; with no body, a note alone."""
    assert tick_view(_system()) == (None, "_No body._")
    body = FakeEmbodiment(view=EmbodimentView(status="**Head:** tracking off"))
    system = _system(embodiment=body)
    assert tick_view(system) == (
        None,
        "_No camera frame yet._\n\n**Head:** tracking off",
    )
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    body.shown = EmbodimentView(frame=frame, status="**Head:** follows #4")
    shown, status = tick_view(system)
    assert shown is frame and status == "**Head:** follows #4"


def test_a_failing_view_is_a_line_on_the_page_not_a_crash() -> None:
    class Broken(FakeEmbodiment):
        def view(self) -> EmbodimentView:
            raise RuntimeError("camera <gone>")

    frame, status = tick_view(_system(embodiment=Broken()))
    assert frame is None
    assert status == "⚠️ _View unavailable: camera &lt;gone&gt;_"


# --- The latency view (specs/latency-view.md, "What the page shows") ------------------------------


def _collector_with(*rids: int) -> LatencyCollector:
    """A collector that has seen typed reactions *rids*, each speaking from 0.5 s after its write."""
    collector = LatencyCollector()
    wica = _Events()
    collector.attach(wica)  # type: ignore[arg-type]
    for rid in rids:
        wica.on_agent_reaction_ended.emit(reaction_trace(rid, written=float(rid)))
    return collector


class _Events:
    def __init__(self) -> None:
        self.on_agent_reaction_ended: Event[Any] = Event()
        self.on_agent_command_ended: Event[Any] = Event()
        self.on_agent_trigger: Event[Any] = Event()
        self.on_agent_trigger_dropped: Event[Any] = Event()


def test_the_latency_panel_sends_only_what_changed() -> None:
    panel = LatencyPanel(_collector_with(1, 2))
    first = panel.tick(True)
    assert all(not isinstance(part, type(gr.skip())) for part in first)
    # The table, oldest first: every row is in the markup, the newest marked and selected.
    assert first[3].index('data-id="1"') < first[3].index('data-id="2"')
    assert '<tr data-id="2" data-last="" class="lv-selected">' in first[3]
    # Nothing new: nothing is sent. The timeline's window slides, so it alone may change.
    again = panel.tick(True)
    assert [again[i] == gr.skip() for i in (0, 2, 3, 4)] == [True] * 4


def test_the_detail_is_not_read_while_its_tab_is_closed() -> None:
    panel = LatencyPanel(_collector_with(1))
    hidden = panel.tick(False)
    assert hidden[2:] == (gr.skip(), gr.skip(), gr.skip())


def test_a_selected_reaction_picks_the_waterfall_and_a_negative_one_follows_the_newest() -> (
    None
):
    panel = LatencyPanel(_collector_with(1, 2, 3))
    assert "reaction 3 " in panel.tick(True)[4]  # none picked: the newest
    panel.select_reaction(2)
    table, waterfall = panel.tick(True)[3:]
    assert "reaction 2 " in waterfall
    assert '<tr data-id="2" class="lv-selected">' in table
    panel.select_reaction(-1)  # "Latest", or a click on the newest row
    assert panel.selected is None and "reaction 3 " in panel.tick(True)[4]


def test_with_no_agent_the_latency_parts_say_so() -> None:
    glance, *_ = LatencyPanel(None).tick(True)
    assert "No agent" in glance


def test_the_vision_panel_shows_the_newest_results_age_and_stage_times() -> None:
    line = tick_vision(
        _system_with_gesture(
            _Gesture(
                frame_age_s=0.123,
                stage_ms=[("hand detection", 34.6), ("classification", 18.2)],
            )
        )
    )
    assert (
        "- **newest result:** frame 123 ms old · hand detection 35 ms · classification 18 ms"
        in line
    )
    assert "newest result" not in tick_vision(_system_with_gesture(_Gesture()))


def test_a_follow_ups_label_names_the_command_that_woke_it() -> None:
    entry = _command_entry("c2", "play_emotion", {"name": "happy"})
    assert entry_label(display_entry, entry).startswith("🦾 play_emotion: ")
    assert "CommandExecution" not in entry_label(display_entry, entry)
