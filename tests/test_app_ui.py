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
from reachy_mini_interaction_wica.app_ui import display_entry, tick_asr, tick_vision
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource
from reachy_mini_interaction_wica.gesture_input import GestureInput


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
        gesture=None,
        emotions=None,
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


# --- The Vision line (specs/app.md, "Vision line"; specs/gesture-input.md) --------------------


class _Gesture:
    """Stands in for a `GestureInput` — only `status()` is read."""

    def __init__(self, **status: Any) -> None:
        base = {
            "running": True,
            "gesture": None,
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
    return AppSystem(
        loop=None,
        api=None,
        wica=None,
        bridge=None,
        voice=None,
        gesture=cast(GestureInput, gesture) if gesture is not None else None,
        emotions=None,
        missing=[],
        submit_speech=lambda _text: None,
    )


def test_display_entry_labels_the_fist_input() -> None:
    assert display_entry(_entry("hand_gesture", "fist")) == EntryDisplay(
        "✊ fist raised"
    )
    assert display_entry(_entry("hand_gesture", None)) == EntryDisplay("🖐️ fist lowered")


def test_vision_line_without_gesture_input_says_off() -> None:
    assert "off" in tick_vision(_system_with_gesture(None))


def test_vision_line_shows_hand_label_score_and_stop_sign() -> None:
    no_hand = tick_vision(_system_with_gesture(_Gesture()))
    assert "running" in no_hand and "no fist" in no_hand
    assert "hands (max 2): no hand" in no_hand.replace("**", "")
    # Before the first sampling window the achieved rates are a placeholder, the target shows.
    assert "stream: — fps (poll 5)" in no_hand.replace("**", "")
    assert "hand stage: — fps (target 5)" in no_hand.replace("**", "")
    assert "classifier: — fps (target 5)" in no_hand.replace("**", "")

    rates = tick_vision(
        _system_with_gesture(
            _Gesture(stream_fps=5.02, hand_fps=4.93, classifier_fps=2.5, target_fps=5.0)
        )
    ).replace("**", "")
    assert "stream: 5.0 fps (poll 5)" in rates
    assert "hand stage: 4.9 fps (target 5)" in rates
    assert "classifier: 2.5 fps (target 5)" in rates

    fist = tick_vision(
        _system_with_gesture(
            _Gesture(gesture="fist", hands=[{"label": "fist", "confidence": 0.87}])
        )
    )
    assert "#1 fist (0.87)" in fist and "✊ fist raised" in fist

    # A hand whose top class missed the threshold: the raw score shows, unvalidated, no fist.
    weak = tick_vision(
        _system_with_gesture(_Gesture(hands=[{"label": None, "confidence": 0.5}]))
    )
    assert "#1 below threshold (0.50)" in weak and "no fist" in weak


def test_vision_line_lists_every_hand_in_slot_order() -> None:
    """Two hands: each shows under its slot number — a relaxed first hand, a fist on the second, a
    hand at the frame edge with no crop — under the configured cap."""
    line = tick_vision(
        _system_with_gesture(
            _Gesture(
                gesture="fist",
                max_hands=3,
                hands=[
                    {"label": "palm", "confidence": 0.91},
                    {"label": "fist", "confidence": 0.77},
                    {"label": None, "confidence": 0.0},
                ],
            )
        )
    ).replace("**", "")
    assert "hands (max 3): #1 palm (0.91), #2 fist (0.77), #3 no crop" in line
    assert "✊ fist raised" in line


def test_vision_line_flags_camera_and_stage_errors() -> None:
    camera = tick_vision(
        _system_with_gesture(_Gesture(capture_error="camera read failed: no camera"))
    )
    assert "camera" in camera and "no camera" in camera

    stage = tick_vision(_system_with_gesture(_Gesture(error="model download failed")))
    assert "error" in stage and "model download failed" in stage

    stopped = tick_vision(_system_with_gesture(_Gesture(running=False)))
    assert "stopped" in stopped
