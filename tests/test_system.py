"""Functional tests for the app's wiring + lifecycle (`system.py`).

Deterministic, no browser/mic/camera/network/body: the agent runs against WICA's `fake` provider (a
scripted model), the body is a `FakeEmbodiment` that only records, the ASR engine is an injected
fake, and only the wiring `run_app` does — minus the Gradio page — is exercised: `build_system`,
then `wire` with a `TranscriptLog` (and a `ReactionLog`) the test builds itself, playing the UI's
role exactly as `build_ui` would, then `_start`. Covers the two input paths (typed text and a closed
ASR segment) reaching the agent and its `say` reaching the embodiment's voice (proving the output
Command is set), the model's free text reaching the transcript (proving the sink is set), an action
Command routing back to the body (proving `wire` registers `embodiment.commands()`), the persona
sections seeded at startup, the vision path, and the degrade and teardown paths.

The lifecycle is tested here *once*, over a fake body; each real embodiment pins its own protocol
members in `tests/<embodiment>/test_embodiment.py` (specs/testing.md).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, cast

import gradio as gr
import numpy as np
import pytest
from asr_engine.config import ASREngineConfig
from asr_engine.segmenter import SpeechSegment
from vision_modules.hand import DetectedHand
from wica import Content, TextPart
from wica.config import WicaConfig
from wica.contrib.gradio import ReactionLog, TranscriptLog

from tests.fakes import (
    FakeASREngine,
    FakeEmbodiment,
    ScriptedInput,
    asr_factory,
    scripted_wica_config,
)
from wica_interaction import system as system_module
from wica_interaction.app_ui import display_entry, entry_display
from wica_interaction.asr_bridge import DictatedPassage
from wica_interaction.config import AppConfig, VisionConfig
from wica_interaction.embodiment import Embodiment, InputEntry
from wica_interaction.gesture_input import GestureModels
from wica_interaction.system import (
    HAND_GESTURE_TTL,
    SPEECH_INPUT_TTL,
    AppSystem,
    _degraded_system,
    _hand_gesture,
    _serve,
    _shutdown,
    _speech_input,
    _start,
    build_system,
    run_app,
    wire,
)


def _config(
    *,
    script: list[dict],
    vision: VisionConfig | None = None,
    persona: str = "You are a test double.",
) -> AppConfig:
    """An `AppConfig` whose WICA section is the `fake` provider running *script*.

    The ASR section is a real one the fake engine never reads (loading it needs no key); the
    lifecycle reads its trigger words for the bridge. `vision` is off unless given (the gesture
    input is built only with a section).
    """
    return AppConfig(
        wica=scripted_wica_config(script, persona=persona),
        asr_engine=_asr_config(),
        vision=vision,
    )


def _asr_config() -> ASREngineConfig:
    return ASREngineConfig.from_dict(
        {"module": {"type": "deepgram_v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )


def _say_once(text: str) -> list[dict]:
    return [
        {"tool_calls": [{"name": "say", "args": {"text": text}}]},
        {"tool_calls": [{"name": "noop", "args": {}}]},
    ]


def _wait_until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _build(
    embodiment: FakeEmbodiment,
    *,
    script: list[dict],
    asr: FakeASREngine | None = None,
    vision: VisionConfig | None = None,
    persona: str = "You are a test double.",
    models: GestureModels | None = None,
) -> AppSystem:
    """`build_system` over the fake body and a fake ASR engine — what every test below starts from."""
    return build_system(
        _config(script=script, vision=vision, persona=persona),
        embodiment,
        asr_engine_factory=asr_factory(asr if asr is not None else FakeASREngine()),
        gesture_models=models,
    )


def _wire(system: AppSystem) -> tuple[TranscriptLog, ReactionLog]:
    """Play the UI's role: build the two contrib presenters over the agent (exactly what `build_ui`
    creates) and wire the transcript's sink + the Commands, as `run_app` does before starting."""
    transcript = TranscriptLog(system.wica, display_entry)
    reactions = ReactionLog(system.wica, display_entry)
    wire(system, transcript)
    return transcript, reactions


def _reaction_captured(reactions: ReactionLog) -> bool:
    """Whether a reasoning step sent a prompt, read off the reaction history the UI's panel polls."""
    return reactions.snapshot().count > 0


def _user_items(transcript: TranscriptLog) -> list[str]:
    return [
        m["content"] for m in transcript.snapshot().conversation if m["role"] == "user"
    ]


def _flat(content: Content) -> str:
    """The text of a rendered `Content` (the World's own rendering — every part here is text)."""
    return "".join(part.text for part in content if isinstance(part, TextPart))


# --- The body: entered, wired, torn down ---------------------------------------------------------


def test_typed_text_drives_agent_and_speaks_through_the_body() -> None:
    """A typed turn becomes `speech_input`, the agent reasons, its `say` reaches the body's voice."""
    embodiment = FakeEmbodiment()
    system = _build(embodiment, script=_say_once("hello there"))
    try:
        assert embodiment.entered  # build_system entered the body on the app loop
        assert embodiment.loop is system.loop  # …on the loop the agent runs on
        assert system.embodiment is embodiment and system.bridge is not None
        _wire(
            system
        )  # sets `say` as the output Command — without it the model could not speak
        _start(system)
        # The typed-text box writes the same World update a closed ASR segment makes.
        system.submit_speech("hi")
        assert _wait_until(lambda: embodiment.said == ["hello there"])
    finally:
        _shutdown(system)


def test_a_body_without_a_voice_sets_no_output_command() -> None:
    """`voice is None` means the model's free text is its voice: nothing is set as the output
    Command, and the agent still reasons."""
    embodiment = FakeEmbodiment(voice=False)
    system = _build(embodiment, script=_say_once("would have spoken"))
    try:
        assert system.embodiment is not None and system.embodiment.voice is None
        _, reactions = _wire(system)
        _start(system)
        assert system.wica is not None
        assert system.wica.agent.output_command_name is None
        system.submit_speech("hello")
        assert _wait_until(lambda: _reaction_captured(reactions))
        assert embodiment.said == []
    finally:
        _shutdown(system)


def test_free_text_lands_in_the_transcript_sink() -> None:
    """The model's free text for a step reaches the transcript's output sink as the 💭 item — the
    one observable proof that `wire` set the sink on the agent."""
    embodiment = FakeEmbodiment()
    script = [
        {"text": "thinking out loud"},
        {"tool_calls": [{"name": "noop", "args": {}}]},
    ]
    system = _build(embodiment, script=script)
    try:
        transcript, _ = _wire(system)
        _start(system)
        system.submit_speech("what are you thinking")

        def sink_item_shown() -> bool:
            return any(
                m.get("metadata", {}).get("title") == "💭 output sink"
                and m["content"] == "thinking out loud"
                for m in transcript.snapshot().conversation
            )

        assert _wait_until(sink_item_shown)
    finally:
        _shutdown(system)


def test_wire_without_agent_is_a_noop() -> None:
    """With no agent there is nothing to wire into: `wire` returns quietly (the banner already says
    why), so `run_app` stays linear in degraded mode."""
    system = _degraded_system("config unreadable")
    wire(system, TranscriptLog(None, display_entry))  # must not raise
    assert system.wica is None


def test_the_bodys_action_commands_are_registered_and_route_back_to_it() -> None:
    """A turn where the agent issues the body's action command reaches the body — proving `wire`
    registered each of `embodiment.commands()`."""
    embodiment = FakeEmbodiment()
    script = [
        {"tool_calls": [{"name": "wave", "args": {}}]},
        {"tool_calls": [{"name": "noop", "args": {}}]},
    ]
    system = _build(embodiment, script=script)
    try:
        _wire(system)
        _start(system)
        assert system.wica is not None and "wave" in system.wica.agent.commands
        system.submit_speech("say hi with your hand")
        assert _wait_until(lambda: embodiment.waved == 1)
    finally:
        _shutdown(system)


def test_a_body_with_no_actions_registers_none() -> None:
    embodiment = FakeEmbodiment(commands=())
    system = _build(embodiment, script=[{"tool_calls": [{"name": "noop", "args": {}}]}])
    try:
        _wire(system)
        assert system.wica is not None
        assert "wave" not in system.wica.agent.commands
        # The voice is still set — as the output Command, not one of the invokable ones.
        assert system.wica.agent.output_command_name == "say"
    finally:
        _shutdown(system)


def test_prompt_addendum_is_seeded_into_the_persona() -> None:
    """Whatever the body wants the model to know before its first turn (the robot's emotion names)
    is appended verbatim to the resolved persona, ahead of WICA's own primer."""
    embodiment = FakeEmbodiment(
        prompt_addendum="\n\n## Test section\n\nhello from the body"
    )
    system = _build(embodiment, script=[{"tool_calls": [{"name": "noop", "args": {}}]}])
    try:
        assert system.wica is not None
        prompt = system.wica.agent.system_prompt
        assert prompt.startswith("You are a test double.")
        assert "\n\n## Test section\n\nhello from the body" in prompt
    finally:
        _shutdown(system)


def test_missing_lines_from_enter_reach_the_banner() -> None:
    """A component of the body that degraded inside `enter()` is one banner line; the body is up."""
    embodiment = FakeEmbodiment(
        missing=["TTS: key unset", "Audio processing: no binding"]
    )
    system = _build(embodiment, script=[{"tool_calls": [{"name": "noop", "args": {}}]}])
    try:
        assert system.embodiment is embodiment
        assert "TTS: key unset" in system.missing
        assert "Audio processing: no binding" in system.missing
    finally:
        _shutdown(system)


def test_the_mic_is_what_the_asr_engine_reads() -> None:
    """The engine is built over `embodiment.mic` (a `None` there means it opens its own default
    device), and its capture health reaches the ASR panel through the bridge's status."""
    mic = cast(Any, object())  # an opaque AudioSource: only identity matters here
    embodiment = FakeEmbodiment(mic=mic, mic_error="mic stream ended")
    fake_asr = FakeASREngine()
    system = _build(embodiment, script=[], asr=fake_asr)
    try:
        # What the lifecycle handed the engine — not what the fake body holds: dropping the
        # hand-off would leave the robot listening through the laptop's mic, echo and all.
        assert fake_asr.audio_source is mic
        assert system.bridge is not None
        assert system.bridge.status()["capture_error"] == "mic stream ended"
    finally:
        _shutdown(system)


def test_empty_typed_text_is_ignored() -> None:
    """Whitespace-only typed input never wakes the agent. Proven by ordering against a real turn, not
    by sleeping on an absence: the empties go in first, and by the time the real turn's reply lands the
    transcript shows that real turn as the only input the agent ever reacted to."""
    embodiment = FakeEmbodiment()
    system = _build(embodiment, script=_say_once("real reply"))
    try:
        transcript, _ = _wire(system)
        _start(system)
        system.submit_speech("   ")
        system.submit_speech("")
        system.submit_speech("a real turn")
        assert _wait_until(lambda: embodiment.said == ["real reply"])
        # Any trigger (an erroneous empty one included) is recorded before its step's reply, so the
        # transcript is complete for inputs by now.
        assert _user_items(transcript) == ['🗣️ "a real turn"']
    finally:
        _shutdown(system)


# --- Speech in -----------------------------------------------------------------------------------


def _final_segment(transcript: str) -> SpeechSegment:
    return SpeechSegment(
        transcript=transcript, is_final=True, end_reason="utterance", utterances=[]
    )


def _emit_segment(
    system: AppSystem, fake_asr: FakeASREngine, segment: SpeechSegment
) -> None:
    """Emit *segment* the way the real ASREngine does: await its `on_speech_segment` callback (which
    the bridge wired to itself) on the app loop."""
    assert system.loop is not None
    assert (
        fake_asr.on_speech_segment is not None
    )  # the bridge claimed the engine's callback
    asyncio.run_coroutine_threadsafe(
        fake_asr.on_speech_segment(segment), system.loop
    ).result(2.0)


def test_closed_segment_reaches_agent_like_typed_text() -> None:
    """A closed ASR segment emitted by the engine flows engine callback → bridge → World and wakes the
    agent, whose `say` reaches the body's voice — the same outcome as a typed turn."""
    embodiment = FakeEmbodiment()
    fake_asr = FakeASREngine()
    system = _build(embodiment, script=_say_once("segment reply"), asr=fake_asr)
    try:
        _wire(system)
        _start(system)
        _emit_segment(system, fake_asr, _final_segment("please reply"))
        assert _wait_until(lambda: embodiment.said == ["segment reply"])
    finally:
        _shutdown(system)


def test_a_dictated_passage_reaches_the_prompt_as_complete() -> None:
    """A segment that closed a dictation (here on a trigger word) flows engine → bridge → World and
    reaches the model's prompt marked as the complete passage."""
    embodiment = FakeEmbodiment()
    fake_asr = FakeASREngine()
    system = _build(embodiment, script=_say_once("noted"), asr=fake_asr)
    try:
        _, reactions = _wire(system)
        _start(system)
        _emit_segment(
            system,
            fake_asr,
            SpeechSegment(
                transcript="buy milk",
                is_final=True,
                end_reason="trigger_word",
                utterances=[],
            ),
        )
        assert _wait_until(lambda: embodiment.said == ["noted"])
        prompt = reactions.prompt_text(0)
        assert prompt is not None
        assert (
            'Dictation is over. You heard this complete dictated passage: "buy milk"'
            in prompt
        )
    finally:
        _shutdown(system)


def test_empty_final_segment_wakes_agent() -> None:
    """A final that closed on silence (empty transcript) still reaches the agent, so it can react to
    the user saying nothing — unlike an empty typed line, which is ignored."""
    embodiment = FakeEmbodiment()
    fake_asr = FakeASREngine()
    system = _build(embodiment, script=_say_once("you went quiet"), asr=fake_asr)
    try:
        _wire(system)
        _start(system)
        _emit_segment(system, fake_asr, _final_segment(""))
        assert _wait_until(lambda: embodiment.said == ["you went quiet"])
    finally:
        _shutdown(system)


# --- Transient inputs: the serializers and the TTLs (specs/app.md, "Inputs are transient") ------


def test_speech_serializer_renders_an_utterance_and_omits_none() -> None:
    """An utterance (an empty one included — a passage that closed on silence) is a line saying what
    was heard, not who said it; `None` — nothing said yet, or the last utterance expired — is an
    empty `Content`, which WICA turns into no entry at all."""
    assert _flat(_speech_input("hello", None)) == 'You hear someone say: "hello"'
    assert _flat(_speech_input("", "hello")) == 'You hear someone say: ""'
    assert _speech_input(None, None) == []
    assert (
        _speech_input(None, "hello") == []
    )  # expired: still nothing, not "nothing yet"


def test_speech_serializer_says_a_dictated_passage_is_complete() -> None:
    """A passage that closed a dictation says the dictation is over, so the model reads it as the
    whole note, not one more sentence of it — empty included."""
    assert (
        _flat(_speech_input(DictatedPassage("buy milk"), None))
        == 'Dictation is over. You heard this complete dictated passage: "buy milk"'
    )
    assert (
        _flat(_speech_input(DictatedPassage(""), None))
        == 'Dictation is over. You heard this complete dictated passage: ""'
    )


def test_hand_serializer_renders_a_sign_and_omits_none() -> None:
    """A sign is a gesture detected on a hand, not something the person is said to be doing."""
    assert (
        _flat(_hand_gesture(2)("three", None))
        == 'Hand gesture detected on hand 2: "three".'
    )
    assert _hand_gesture(1)(None, None) == []
    assert (
        _hand_gesture(1)(None, "three") == []
    )  # dropped or expired: omitted, not "no sign"


def test_an_utterance_is_gone_from_the_world_and_the_prompt_after_its_ttl() -> None:
    """The step an utterance triggers observes it; `SPEECH_INPUT_TTL` later the World has cleared
    it on its own — a reset that wakes no step — and the World renders no `speech_input` block."""
    embodiment = FakeEmbodiment()
    system = _build(embodiment, script=_say_once("hello there"))
    try:
        assert system.wica is not None
        world = system.wica.world
        assert world.get_config("speech_input").ttl == SPEECH_INPUT_TTL
        _, reactions = _wire(system)
        _start(system)
        assert "speech_input" not in _flat(
            world.render_full_prompt()
        )  # nothing said yet
        system.submit_speech("hi")
        assert _wait_until(lambda: embodiment.said == ["hello there"])
        prompt = reactions.prompt_text(0)
        assert prompt is not None
        assert 'You hear someone say: "hi"' in prompt
        # The say finishing re-triggers one step (the scripted noop); then nothing else may.
        assert _wait_until(lambda: reactions.snapshot().count == 2)

        assert _wait_until(
            lambda: world.get("speech_input") is None,
            timeout=SPEECH_INPUT_TTL.total_seconds() + 1.0,
        )
        assert "speech_input" not in _flat(world.render_full_prompt())
        time.sleep(0.3)  # long enough for a step the reset might (wrongly) have woken
        assert reactions.snapshot().count == 2
    finally:
        _shutdown(system)


# --- Degraded start ------------------------------------------------------------------------------


def test_a_body_that_fails_to_enter_degrades_but_the_agent_still_reasons() -> None:
    """A body that cannot come up is captured in `missing` under its own name; nothing that needs it
    is built, and WICA still runs on typed text."""
    embodiment = FakeEmbodiment(raise_on_enter="no daemon reachable")
    built_over: list[Any] = []
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        embodiment,
        asr_engine_factory=asr_factory(FakeASREngine(), built_over),
    )
    try:
        assert system.embodiment is None  # the failed enter degraded it to None
        assert (
            system.display_name == "fake body"
        )  # … but the page still names what was launched
        # No body ⇒ no mic ⇒ no speech-in: the engine is not even built.
        assert built_over == []
        assert system.bridge is None
        assert any(
            m.startswith("fake body:") and "no daemon reachable" in m
            for m in system.missing
        )
        # The fake agent built fine, and still reasons over typed input: a step runs (a prompt is
        # captured).
        assert system.wica is not None
        _, reactions = _wire(system)
        assert system.wica.agent.output_command_name is None  # no body ⇒ no voice …
        assert "wave" not in system.wica.agent.commands  # … and no action commands
        _start(system)
        system.submit_speech("are you there")
        assert _wait_until(lambda: _reaction_captured(reactions))
    finally:
        _shutdown(system)


def test_no_body_means_no_prompt_addendum() -> None:
    """Without a body there is nothing to seed: the agent builds over the plain persona."""
    embodiment = FakeEmbodiment(
        raise_on_enter="no body", prompt_addendum="\n\n## Test section\n\nhi"
    )
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]), embodiment
    )
    try:
        assert system.wica is not None
        assert "Test section" not in system.wica.agent.system_prompt
    finally:
        _shutdown(system)


def _asr_degrade_config() -> AppConfig:
    """A real ASR config referencing an unset env var (so `ASREngine` construction raises), with a
    fake WICA agent — the ASR side alone should degrade."""
    asr = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram_v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )
    return AppConfig(wica=scripted_wica_config([], persona="double"), asr_engine=asr)


def test_missing_asr_key_degrades_but_the_body_survives(monkeypatch: Any) -> None:
    """With the body up but the ASR key unset, ASR degrades (bridge None) while the voice survives."""
    monkeypatch.delenv("WICA_TEST_UNSET_ASR_KEY", raising=False)
    embodiment = FakeEmbodiment()
    # No asr_engine injected: build_system builds a real ASREngine over the mic, which raises.
    system = build_system(_asr_degrade_config(), embodiment)
    try:
        assert embodiment.entered
        assert system.embodiment is not None  # the body is up …
        assert system.embodiment.voice is not None  # … so the voice is wired …
        assert system.bridge is None  # … while ASR degraded
        assert any(m.startswith("ASR:") for m in system.missing)
        assert not any(m.startswith("fake body:") for m in system.missing)
    finally:
        _shutdown(system)


def test_bad_system_prompt_file_degrades(tmp_path: Path) -> None:
    """A `system_prompt_file` that doesn't exist lands in the banner as an `Agent:` line (the app's
    own persona read raises WICA's ConfigError, before Wica.init) rather than crashing; the rest of
    the system still builds."""
    wica = WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt_file": str(tmp_path / "no-such-prompt.md"),
            }
        }
    )
    config = AppConfig(wica=wica, asr_engine=_asr_config())
    embodiment = FakeEmbodiment()
    system = build_system(
        config, embodiment, asr_engine_factory=asr_factory(FakeASREngine())
    )
    try:
        assert system.wica is None
        assert any(
            m.startswith("Agent:") and "no-such-prompt.md" in m for m in system.missing
        )
        assert system.embodiment is not None  # the body is unaffected
    finally:
        _shutdown(system)
    assert embodiment.exited


# --- Teardown -------------------------------------------------------------------------------------


def test_shutdown_stops_asr_before_the_body_exits() -> None:
    """Teardown stops the ASR bridge (cancelling the mic pump) *before* the body is released, then
    exits it — the order specs/app.md lists."""
    log: list[str] = []
    embodiment = FakeEmbodiment(log=log)
    fake_asr = FakeASREngine(log=log)
    system = _build(
        embodiment,
        script=[{"tool_calls": [{"name": "noop", "args": {}}]}],
        asr=fake_asr,
    )
    _start(system)
    assert fake_asr.started  # capture was live before teardown

    _shutdown(system)

    assert not fake_asr.started
    assert embodiment.exited
    assert log == ["asr.stop", "embodiment.exit"]
    assert system.wica is not None and not system.wica.is_running


class _NeverLaunched:
    """Stands in for the built page in `_serve`: something raises first, so `launch` must never run."""

    def launch(self) -> None:
        raise AssertionError("launch() must not be reached when an earlier step raises")


def _never_launched_ui(system: AppSystem) -> Any:
    """Stand in for `build_ui` (no browser): a real transcript, a page that must not launch."""

    class _Ui:
        blocks = cast(gr.Blocks, _NeverLaunched())
        transcript = TranscriptLog(system.wica, display_entry)

    return _Ui()


def _loop_stopped(system: AppSystem) -> bool:
    """Wait for the app loop's thread to leave run_forever (loop.stop is scheduled, not immediate)."""
    loop = system.loop
    assert loop is not None
    return _wait_until(lambda: not loop.is_running())


def test_start_failure_still_tears_down_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raise while starting — after build_system has entered the body — still exits it and stops
    the loop, instead of leaking both (the UI is never launched)."""
    monkeypatch.setattr(system_module, "build_ui", _never_launched_ui)
    embodiment = FakeEmbodiment()
    system = _build(
        embodiment,
        script=[{"tool_calls": [{"name": "noop", "args": {}}]}],
        asr=FakeASREngine(raise_on_start="mic unavailable"),
    )
    assert embodiment.entered

    with pytest.raises(RuntimeError, match="mic unavailable"):
        _serve(system)

    assert embodiment.exited
    assert _loop_stopped(system)
    assert system.wica is not None and not system.wica.is_running


def test_a_page_that_fails_to_build_still_tears_down_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body is entered by `build_system`, before the page exists: a raise while building the
    page must exit it (a robot session, a spawned daemon) like a raise while starting does."""

    def _broken_ui(_system: AppSystem) -> Any:
        raise RuntimeError("gradio could not build the page")

    monkeypatch.setattr(system_module, "build_ui", _broken_ui)
    embodiment = FakeEmbodiment()
    system = _build(embodiment, script=[])

    with pytest.raises(RuntimeError, match="could not build the page"):
        _serve(system)

    assert embodiment.exited
    assert _loop_stopped(system)


def test_a_wiring_failure_still_tears_down_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wire` can raise on its own — here the body offers two Commands under one name, which WICA
    rejects — and that, too, happens with the body already entered."""
    monkeypatch.setattr(system_module, "build_ui", _never_launched_ui)

    async def wave() -> str:
        """A second `wave`, colliding with the first."""
        return "OK"

    embodiment = FakeEmbodiment(commands=None)
    embodiment._commands = (embodiment.wave, wave)
    system = _build(embodiment, script=[])

    with pytest.raises(ValueError, match="wave"):
        _serve(system)

    assert embodiment.exited
    assert _loop_stopped(system)


def test_shutdown_isolates_a_failing_step() -> None:
    """An ASR stop that raises is logged and skipped past: the body still exits, the loop stops."""
    embodiment = FakeEmbodiment()
    system = _build(
        embodiment,
        script=[{"tool_calls": [{"name": "noop", "args": {}}]}],
        asr=FakeASREngine(raise_on_stop="provider socket broke"),
    )
    _start(system)

    _shutdown(system)  # must not raise

    assert embodiment.exited
    assert _loop_stopped(system)


def test_shutdown_bounds_a_hung_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ASR stop that never returns is abandoned after the per-step timeout; teardown continues."""
    monkeypatch.setattr(system_module, "_SHUTDOWN_TIMEOUT_S", 0.1)
    embodiment = FakeEmbodiment()
    system = _build(
        embodiment,
        script=[{"tool_calls": [{"name": "noop", "args": {}}]}],
        asr=FakeASREngine(hang_on_stop=True),
    )
    _start(system)

    _shutdown(system)

    assert embodiment.exited
    assert _loop_stopped(system)


def test_shutdown_skips_never_started_bridge() -> None:
    """A bridge that was built but never started (nothing ran _start) is not stopped at teardown —
    the running-guard holds — while the body is still released."""
    log: list[str] = []
    embodiment = FakeEmbodiment(log=log)
    system = _build(
        embodiment,
        script=[{"tool_calls": [{"name": "noop", "args": {}}]}],
        asr=FakeASREngine(log=log),
    )

    _shutdown(system)

    assert log == ["embodiment.exit"]  # no "asr.stop"
    assert _loop_stopped(system)


# --- The entry point ------------------------------------------------------------------------------


def test_run_app_requires_a_config_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare run (no --config) exits with usage guidance before loading anything — it never falls
    back to a default file (the committed examples are templates to copy and adapt, not configs meant
    to run unmodified)."""
    monkeypatch.setattr(sys, "argv", ["wica-interaction-fake"])
    loaded: list[str] = []

    def _load(path: str) -> tuple[AppConfig, Embodiment]:
        loaded.append(path)
        raise AssertionError("load must not be reached")

    with pytest.raises(SystemExit) as exc:
        run_app(description="a fake body", load=_load)

    assert exc.value.code == 2
    assert "--config" in capsys.readouterr().err
    assert loaded == []  # no body was ever constructed


def test_run_app_degrades_a_bad_config_into_the_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ValueError` from the embodiment's own loader opens the UI with the message in the banner
    rather than a traceback: nothing is built, nothing is served."""
    monkeypatch.setattr(sys, "argv", ["wica-interaction-fake", "--config", "nope.json"])
    seen: list[AppSystem] = []
    monkeypatch.setattr(system_module, "_serve", seen.append)

    def _load(path: str) -> tuple[AppConfig, Embodiment]:
        raise ValueError(f"could not read config file {path!r}")

    run_app(description="a fake body", load=_load)

    assert len(seen) == 1
    assert seen[0].wica is None and seen[0].embodiment is None
    assert seen[0].display_name is None  # no config ⇒ no body to name the page after
    assert seen[0].missing == ["could not read config file 'nope.json'"]


# --- Vision in: the hand-sign input (specs/gesture-input.md, "App integration") ----------------


_HAND_FRAME = np.full((48, 64, 3), 200, dtype=np.uint8)


class _ScriptedDetector:
    """One centered hand in any non-black frame."""

    def detect(self, image_bgr: np.ndarray, ts: float) -> tuple[DetectedHand, ...]:
        if int(image_bgr[0, 0, 0]) == 0:
            return ()
        return (DetectedHand(box=(0.25, 0.25, 0.75, 0.75), score=0.95),)

    def close(self) -> None:
        pass


class _ScriptedClassifier:
    """Scores one label (`fist` ⇒ the `stop` sign by default) against the unmapped `call`, so a low
    score means "no sign at all", not another sign."""

    labels = ("fist", "call")

    def __init__(self, fist: float, label: str = "fist") -> None:
        self.fist = fist
        self.label = label

    def classify(self, image_bgr: np.ndarray) -> dict[str, float]:
        return {self.label: self.fist, "call": 1.0 - self.fist}

    def close(self) -> None:
        pass


def _vision_system(
    embodiment: FakeEmbodiment, classifier: _ScriptedClassifier, script: list[dict]
) -> AppSystem:
    return _build(
        embodiment,
        script=script,
        # No hold: these tests are about the wiring, not the filter (tested with the module).
        vision=VisionConfig(enabled=True, fps=100.0, hold_s=0.0),
        models=GestureModels(detector=_ScriptedDetector(), classifier=classifier),
    )


def _camera_frames(embodiment: FakeEmbodiment) -> int:
    """Frames the body's camera feed(s) published so far."""
    return sum(camera.published_count for camera in embodiment.cameras)


def _sign_steps(reactions: ReactionLog, hand: int) -> int:
    """How many reasoning steps a sign on *hand* triggered, read off the reaction labels (each step
    is labelled by its trigger through `display_entry`)."""
    return sum(
        f"✋ hand {hand}:" in label and "no sign" not in label
        for label, _ in reactions.snapshot().choices
    )


def test_a_sign_wakes_the_agent_and_is_gone_from_the_prompt_after_its_ttl() -> None:
    """The body's camera → pipeline → `hand_gesture_1` → one reasoning step whose prompt names the
    sign that hand shows and carries no line for the sign-less hand 2; the sign held over many frames
    wakes the agent only once; after `HAND_GESTURE_TTL` the World has cleared it (no step for that)
    and the World renders no hand entry at all; the hand then dropping does not wake the agent."""
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    classifier = _ScriptedClassifier(fist=0.9)
    system = _vision_system(embodiment, classifier, _say_once("okay, stopping"))
    try:
        assert system.gesture is not None
        assert system.wica is not None
        world = system.wica.world
        # One entry per hand the pipeline can publish (`max_hands`, 2 by default), each expiring.
        assert world.is_registered("hand_gesture_1")
        assert world.is_registered("hand_gesture_2")
        assert not world.is_registered("hand_gesture_3")
        assert world.get_config("hand_gesture_1").ttl == HAND_GESTURE_TTL
        # And the vocabulary is in the prompt, seeded from gesture_input at startup.
        assert "## Hand signs" in system.wica.agent.system_prompt
        _, reactions = _wire(system)
        _start(system)
        assert _wait_until(lambda: embodiment.said == ["okay, stopping"])
        prompt = reactions.prompt_text(0)
        assert prompt is not None
        assert 'detected on hand 1: "stop"' in prompt
        assert "hand 2" not in prompt  # a sign-less hand is omitted, not described

        # Still shown, many frames later: no second step for it (the `say` finishing re-triggers one
        # step of its own — WICA's completion re-trigger — which is labelled by the say, not a sign).
        frames_then = _camera_frames(embodiment)
        assert _wait_until(lambda: _camera_frames(embodiment) >= frames_then + 10)
        assert _sign_steps(reactions, 1) == 1

        # Expired: the World cleared the sign on its own (the hand is still up — the gesture input
        # still holds `stop` and re-delivers nothing) with no step, and renders no hand entry.
        assert _wait_until(lambda: world.get("hand_gesture_1") is None)
        assert system.gesture.status()["signs"][0] == "stop"
        assert _sign_steps(reactions, 1) == 1
        assert "hand_gesture" not in _flat(world.render_full_prompt())

        # Gone: the entry updates (silently), the agent is not woken.
        classifier.fist = 0.1
        assert _wait_until(lambda: system.gesture.status()["signs"][0] is None)  # type: ignore[union-attr]
        frames_then = _camera_frames(embodiment)
        assert _wait_until(lambda: _camera_frames(embodiment) >= frames_then + 10)
        assert _sign_steps(reactions, 1) == 1
        assert not any("no sign" in label for label, _ in reactions.snapshot().choices)
        assert embodiment.said == ["okay, stopping"]
    finally:
        _shutdown(system)


def test_another_sign_wakes_the_agent_again() -> None:
    """A hand switching sign with no sign-less frame between is a second trigger: `sign_shown` fires
    on `stop → five` too, and the new prompt carries the new sign."""
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    classifier = _ScriptedClassifier(fist=0.9)
    # One step per trigger, each speaking: the second is the one the new sign wakes.
    script = [
        {"tool_calls": [{"name": "say", "args": {"text": "okay, stopping"}}]},
        {"tool_calls": [{"name": "say", "args": {"text": "five it is"}}]},
    ]
    system = _vision_system(embodiment, classifier, script)
    try:
        assert system.wica is not None
        _, reactions = _wire(system)
        _start(system)
        assert _wait_until(lambda: embodiment.said == ["okay, stopping"])
        classifier.label = "palm"  # stop → five, no gap
        assert _wait_until(
            lambda: system.wica.world.get("hand_gesture_1") == "five"  # type: ignore[union-attr]
        )
        assert _wait_until(lambda: embodiment.said == ["okay, stopping", "five it is"])
        assert _sign_steps(reactions, 1) == 2
        prompt = reactions.prompt_text(reactions.snapshot().count - 1)
        assert prompt is not None
        assert 'detected on hand 1: "five"' in prompt
    finally:
        _shutdown(system)


def test_vision_is_off_without_a_config_section() -> None:
    system = _build(FakeEmbodiment(), script=[])
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert "## Hand signs" not in system.wica.agent.system_prompt
        assert not any(m.startswith("Vision:") for m in system.missing)
    finally:
        _shutdown(system)


def test_vision_section_present_but_disabled_builds_nothing() -> None:
    """`enabled: false` (the default) with the body up: no gesture input, no `hand_gesture_<n>`
    entry, no hand-signs section — the section can sit in the file, tuned, without vision running."""
    system = _build(FakeEmbodiment(), script=[], vision=VisionConfig(fps=100.0))
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert "## Hand signs" not in system.wica.agent.system_prompt
        assert not any(m.startswith("Vision:") for m in system.missing)
    finally:
        _shutdown(system)


def test_vision_max_hands_fps_and_hold_reach_the_gesture_input() -> None:
    """The configured hand cap, rate and hold are what the pipeline (and the body's camera) run
    with, not the library's or the module's defaults."""
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    system = _build(
        embodiment,
        script=[],
        vision=VisionConfig(enabled=True, max_hands=1, fps=20.0, hold_s=0.25),
        models=GestureModels(
            detector=_ScriptedDetector(), classifier=_ScriptedClassifier(fist=0.9)
        ),
    )
    try:
        assert system.gesture is not None
        assert system.gesture.status()["max_hands"] == 1
        assert system.gesture.status()["target_fps"] == 20.0
        assert system.gesture.status()["hold_s"] == 0.25
        # The camera the body handed over was asked for the same rate.
        assert [camera.fps for camera in embodiment.cameras] == [20.0]
    finally:
        _shutdown(system)


def test_vision_needs_a_body() -> None:
    """No body ⇒ no camera ⇒ no gesture input and no `hand_gesture_<n>` entry (the agent is never
    told about hands it cannot see); the banner carries only the body's line."""
    system = build_system(
        _config(script=[], vision=VisionConfig(enabled=True)),
        FakeEmbodiment(raise_on_enter="no body"),
        asr_engine_factory=asr_factory(FakeASREngine()),
    )
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert "## Hand signs" not in system.wica.agent.system_prompt
        assert [m for m in system.missing if m.startswith("Vision:")] == []
    finally:
        _shutdown(system)


def test_shutdown_stops_vision_before_the_body_exits() -> None:
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    system = _vision_system(embodiment, _ScriptedClassifier(fist=0.1), [])
    try:
        _wire(system)
        _start(system)
        assert system.gesture is not None and system.gesture.running
        assert _wait_until(lambda: _camera_frames(embodiment) > 0)
    finally:
        _shutdown(system)
    assert system.gesture is not None and not system.gesture.running
    assert embodiment.exited
    frames_then = _camera_frames(embodiment)
    time.sleep(0.05)
    assert (
        _camera_frames(embodiment) == frames_then
    )  # the released camera publishes nothing


# --- The body's own inputs (specs/embodiment.md, "`inputs()` is the mirror of `commands()`") -------


def _input_steps(reactions: ReactionLog) -> int:
    """How many reasoning steps the scripted input's triggering entry woke, read off the reaction
    labels — each labelled through the entry's own `display_fn` (`👀 <value>`)."""
    return sum("👀" in label for label, _ in reactions.snapshot().choices)


def _noop_script() -> list[dict]:
    return [{"tool_calls": [{"name": "noop", "args": {}}]}]


def test_a_body_input_is_registered_delivered_and_wakes_the_agent() -> None:
    """The body describes its entries and plays the producer; the lifecycle registers them as
    declared, starts the producer on the app loop once the agent runs, and what it delivers reaches
    the prompt — the triggering entry waking a step labelled by the body's own wording, the standing
    one waking none and still rendered."""
    body_input = ScriptedInput()
    embodiment = FakeEmbodiment(inputs=(body_input,))
    system = _build(embodiment, script=_say_once("a cat, how nice"))
    try:
        assert system.wica is not None
        world = system.wica.world
        assert world.is_registered("seen") and world.is_registered("around")
        assert world.get_config("seen").triggers_llm_call
        assert not world.get_config("around").triggers_llm_call
        assert body_input.started == 0  # nothing runs before the agent does
        display = entry_display(system)
        transcript = TranscriptLog(system.wica, display)
        reactions = ReactionLog(system.wica, display)
        wire(system, transcript)
        _start(system)
        assert body_input.started == 1 and body_input.loop is system.loop
        assert body_input.deliver is not None

        body_input.deliver("around", "a table")  # standing: no step
        time.sleep(0.4)  # past the coalescing window a trigger would have opened
        assert not _reaction_captured(reactions)

        body_input.deliver("seen", "a cat")
        assert _wait_until(lambda: embodiment.said == ["a cat, how nice"])
        prompt = reactions.prompt_text(0)
        assert prompt is not None
        assert "Seen: a cat" in prompt and "Seen: a table" in prompt
        assert _input_steps(reactions) == 1
        assert "👀 a cat" in _user_items(transcript)
    finally:
        _shutdown(system)


def test_an_input_writes_only_its_own_keys() -> None:
    """`deliver` takes the keys the input declared: another — the app's own `speech_input` — is
    dropped, never written, and wakes nothing."""
    body_input = ScriptedInput()
    system = _build(FakeEmbodiment(inputs=(body_input,)), script=_noop_script())
    try:
        _, reactions = _wire(system)
        _start(system)
        assert body_input.deliver is not None and system.wica is not None
        body_input.deliver("speech_input", "pretending to be heard")
        time.sleep(0.4)
        assert system.wica.world.get("speech_input") is None
        assert not _reaction_captured(reactions)
    finally:
        _shutdown(system)


def test_a_delivery_outside_the_running_agent_is_dropped() -> None:
    """Before the agent starts and after teardown, a delivery is dropped, not raised — as speech and
    signs are."""
    body_input = ScriptedInput()
    system = _build(FakeEmbodiment(inputs=(body_input,)), script=_noop_script())
    ((built_input, deliver),) = system.inputs
    assert built_input is body_input
    try:
        deliver("seen", "too early")  # the World is not running yet
        _wire(system)
        _start(system)
    finally:
        _shutdown(system)
    deliver("seen", "too late")  # the World is stopped: dropped, no raise
    assert system.wica is not None and system.wica.world.get("seen") is None


def test_an_entry_the_world_refuses_drops_that_input_with_a_banner_line() -> None:
    """An input declaring a key the World already holds (here the app's `speech_input`) is dropped
    whole — its other entry unregistered — with a line naming the body; another input is unaffected
    and the agent runs."""
    clashing = ScriptedInput(
        entries=(
            InputEntry("fine_so_far", str, serialize_fn=lambda v, p: []),
            InputEntry("speech_input", str, serialize_fn=lambda v, p: []),
        )
    )
    fine = ScriptedInput()
    system = _build(
        FakeEmbodiment(inputs=(clashing, fine)), script=_say_once("still here")
    )
    try:
        assert system.wica is not None
        assert any(
            m.startswith("fake body:") and "speech_input" in m for m in system.missing
        )
        assert not system.wica.world.is_registered("fine_so_far")
        assert [i for i, _ in system.inputs] == [fine]
        _wire(system)
        _start(system)
        assert clashing.started == 0 and fine.started == 1
    finally:
        _shutdown(system)


def test_an_input_that_fails_to_start_leaves_the_app_running() -> None:
    """A producer whose `start` raises is logged and stays silent; the next input still starts and
    typed text still drives the agent."""
    failing = ScriptedInput(
        entries=(InputEntry("broken", str, serialize_fn=lambda v, p: []),),
        raise_on_start="no camera",
    )
    fine = ScriptedInput()
    embodiment = FakeEmbodiment(inputs=(failing, fine))
    system = _build(embodiment, script=_say_once("hello"))
    try:
        _wire(system)
        _start(system)
        assert failing.started == 1 and fine.started == 1
        system.submit_speech("hi")
        assert _wait_until(lambda: embodiment.said == ["hello"])
    finally:
        _shutdown(system)


def test_no_inputs_without_a_body_or_without_an_agent(tmp_path: Path) -> None:
    """A body that failed to enter is not asked for its inputs; a body with no agent to deliver to
    has them neither registered nor started."""
    no_body = FakeEmbodiment(raise_on_enter="unplugged", inputs=(ScriptedInput(),))
    system = _build(no_body, script=_noop_script())
    try:
        assert system.inputs == () and no_body.inputs_asked == 0
    finally:
        _shutdown(system)

    body_input = ScriptedInput()
    wica = WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt_file": str(tmp_path / "no-such-prompt.md"),
            }
        }
    )
    embodiment = FakeEmbodiment(inputs=(body_input,))
    system = build_system(
        AppConfig(wica=wica, asr_engine=_asr_config()),
        embodiment,
        asr_engine_factory=asr_factory(FakeASREngine()),
    )
    try:
        assert system.wica is None and system.inputs == ()
        _start(system)
        assert body_input.started == 0
    finally:
        _shutdown(system)
    assert body_input.stopped == 0


def test_shutdown_stops_the_inputs_right_after_the_agent() -> None:
    """Teardown stops each input before anything the body owns: the agent first, then the inputs,
    then the ASR bridge, then the body — the order specs/app.md lists."""
    log: list[str] = []
    body_input = ScriptedInput(log=log)
    embodiment = FakeEmbodiment(log=log, inputs=(body_input,))
    system = _build(embodiment, script=_noop_script(), asr=FakeASREngine(log=log))
    _wire(system)
    _start(system)

    _shutdown(system)

    assert body_input.stopped == 1
    assert log == ["input.stop", "asr.stop", "embodiment.exit"]
