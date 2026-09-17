"""Functional tests for ``AsrBridge`` — driven against a fake ``ASREngine`` (no audio, no network).

Tests push real ``SpeechSegment``s through ``bridge._on_segment`` (the engine's segment callback) and
assert on the display view, the ``on_final_segment`` callback, and the dictation commands' non-raising
string contract, plus the `capture_error` it reads back from the embodiment. The mic sources
themselves are tested with their embodiments. See specs/asr-bridge.md ("Testing").

The bridge's methods are coroutines; tests drive them with ``asyncio.run`` (no async-test plugin
required). State lives on the bridge/engine objects, so independent event loops across calls are fine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment

from wica_interaction.asr_bridge import AsrBridge


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeASREngine:
    """Minimal stand-in exposing only what ``AsrBridge`` reads/calls.

    ``start_dictation`` / ``stop_dictation`` record their calls and update ``dictating`` /
    ``segmentation_mode``; set ``raise_on_start_dictation`` / ``raise_on_stop_dictation`` to a message
    to exercise the bridge's error path.
    """

    def __init__(
        self, *, running: bool = True, segmentation_mode: str = "utterance"
    ) -> None:
        # AsrBridge.__init__ overwrites this with its own _on_segment.
        self.on_speech_segment = None
        self._running = running
        self.dictating = False
        self.segmentation_mode = segmentation_mode
        self.start_dictation_calls: list[dict] = []
        self.stop_dictation_calls = 0
        self.raise_on_start_dictation: str | None = None
        self.raise_on_stop_dictation: str | None = None

    def status(self) -> dict:
        return {"running": self._running, "connected": True}

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def start_dictation(
        self, end_on_final_segment: bool = True, segmentation_mode: str | None = None
    ) -> None:
        self.start_dictation_calls.append(
            {
                "end_on_final_segment": end_on_final_segment,
                "segmentation_mode": segmentation_mode,
            }
        )
        if self.raise_on_start_dictation is not None:
            raise ValueError(self.raise_on_start_dictation)
        self.dictating = True
        self.segmentation_mode = segmentation_mode or "timeout"

    async def stop_dictation(self) -> None:
        self.stop_dictation_calls += 1
        if self.raise_on_stop_dictation is not None:
            raise ValueError(self.raise_on_stop_dictation)
        self.dictating = False
        self.segmentation_mode = "utterance"


def _make_bridge(engine: FakeASREngine) -> tuple[AsrBridge, list[str]]:
    """Build a bridge over *engine* with a recording ``on_final_segment``; return both."""
    finals: list[str] = []
    bridge = AsrBridge(cast(ASREngine, engine), on_final_segment=finals.append)
    return bridge, finals


def _segment(transcript: str, *, is_final: bool) -> SpeechSegment:
    return SpeechSegment(
        transcript=transcript,
        is_final=is_final,
        end_reason="utterance" if is_final else None,
        utterances=[],
    )


def test_constructor_wires_segment_callback() -> None:
    engine = FakeASREngine()
    bridge, _ = _make_bridge(engine)
    assert engine.on_speech_segment == bridge._on_segment


def test_interim_segment_is_visible_but_inert() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hello wor", is_final=False)))

    assert bridge.display_text == "hello wor"
    assert finals == []  # the World is not touched — the agent is not triggered


def test_final_segment_triggers_once_and_clears_display() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hello world", is_final=True)))

    assert finals == ["hello world"]
    assert bridge.display_text == ""


def test_interim_interim_final_yields_one_call_with_final_text() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hel", is_final=False)))
    run(bridge._on_segment(_segment("hello wor", is_final=False)))
    run(bridge._on_segment(_segment("hello world", is_final=True)))

    assert finals == ["hello world"]  # exactly one utterance, once
    assert bridge.display_text == ""


def test_empty_final_still_fires() -> None:
    """A final that closes on silence still wakes the agent (with "")."""
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("", is_final=True)))

    assert finals == [""]
    assert bridge.display_text == ""


def test_start_dictation_wires_engine_and_reports_mode() -> None:
    engine = FakeASREngine()
    bridge, _ = _make_bridge(engine)

    result = run(bridge.start_dictation("timeout"))

    assert engine.start_dictation_calls == [
        {"end_on_final_segment": True, "segmentation_mode": "timeout"}
    ]
    assert "timeout" in result
    assert bridge.status() == {
        "running": True,
        "dictating": True,
        "segmentation_mode": "timeout",
        "capture_error": None,
    }


def test_start_dictation_catches_error() -> None:
    engine = FakeASREngine()
    engine.raise_on_start_dictation = "ASR is not running."
    bridge, _ = _make_bridge(engine)

    result = run(bridge.start_dictation())

    assert "ASR is not running." in result  # readable string, not an exception
    assert not bridge.status()["dictating"]


def test_stop_dictation_happy_and_error_paths() -> None:
    engine = FakeASREngine()
    engine.dictating = True
    bridge, _ = _make_bridge(engine)

    assert run(bridge.stop_dictation()) == "Dictation stopped."
    assert engine.stop_dictation_calls == 1
    assert not bridge.status()["dictating"]

    engine.raise_on_stop_dictation = "No dictation in progress."
    result = run(bridge.stop_dictation())
    assert "No dictation in progress." in result


def test_status_composes_engine_accessors() -> None:
    engine = FakeASREngine(running=False, segmentation_mode="trigger_word")
    engine.dictating = True
    bridge, _ = _make_bridge(engine)

    assert bridge.status() == {
        "running": False,
        "dictating": True,
        "segmentation_mode": "trigger_word",
        "capture_error": None,  # no mic_error callable handed to the bridge
    }


def test_start_and_stop_delegate_to_engine() -> None:
    engine = FakeASREngine(running=False)
    bridge, _ = _make_bridge(engine)

    run(bridge.start())
    assert bridge.status()["running"] is True

    run(bridge.stop())
    assert bridge.status()["running"] is False


def test_status_reads_the_capture_health_back_from_the_embodiment() -> None:
    """`capture_error` is whatever the injected callable returns *at that moment*: the engine can
    keep reporting `running` while the mic stream has died, and the ASR panel must show that."""
    engine = FakeASREngine(running=True)
    error: str | None = None
    bridge = AsrBridge(
        cast(ASREngine, engine),
        on_final_segment=lambda _t: None,
        mic_error=lambda: error,
    )

    assert bridge.status()["capture_error"] is None

    error = "mic stream failed: usb unplugged"
    status = bridge.status()
    assert status["running"] is True  # the engine still thinks it is capturing …
    assert status["capture_error"] == error  # … while the source says why it is not
