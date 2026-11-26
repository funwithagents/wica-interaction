"""Functional tests for the app's wiring + lifecycle (`system.py`).

Deterministic, no browser/mic/camera/network/body: the agent runs against WICA's `fake` provider (a
scripted model), the body is a `FakeEmbodiment` that only records, the ASR engine is an injected
fake, and only the wiring a session goes through — minus the Gradio page — is exercised:
`build_system`, then `wire` after a `TranscriptLog` (and a `ReactionLog`) the test builds itself,
exactly as `open_session` does, then `_start`. Covers the two input paths (typed text and a closed
ASR segment) reaching the agent and its `say` reaching the embodiment's voice (proving the output
Command is set), the model's private text reaching the transcript (outside the `<say>` tag), an action
Command routing back to the body (proving `wire` registers `embodiment.commands()`), the persona
sections seeded at startup, the vision path, and the degrade and teardown paths.

The lifecycle is tested here *once*, over a fake body; each real embodiment pins its own protocol
members in `tests/<embodiment>/test_embodiment.py` (specs/testing.md).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from app_setup.app import App, AppModel, Section
from asr_engine.audio import AudioFormat, AudioSource
from asr_engine.config import ASREngineConfig
from asr_engine.segmenter import SpeechSegment
from opentelemetry import trace
from vision_modules.hand import DetectedHand
from wica import Content, OutputStream, TextPart
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
from wica_interaction.latency import EffectStart
from wica_interaction.session import Running, Session
from wica_interaction.system import (
    HAND_GESTURE_TTL,
    SPEECH_INPUT_TTL,
    AppSystem,
    _hand_gesture,
    _speech_input,
    _start,
    build_system,
    close_session,
    open_session,
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
        {"module": {"type": "deepgram-v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )


def _say_once(text: str) -> list[dict]:
    """One step that speaks *text* through the `<say>` tag. The output's end wakes no further step
    (no `<next>` note), so one step is the whole reaction."""
    return [{"text": f"<say>{text}</say>"}]


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
    """Play `open_session`'s role: build the two contrib presenters over the agent (subscribing
    before start), then wire the output Command + the Commands, before starting."""
    transcript = TranscriptLog(system.wica, display_entry)
    reactions = ReactionLog(system.wica, display_entry)
    wire(system)
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
        close_session(system)


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
        close_session(system)


def test_private_text_lands_in_the_transcript_and_only_the_tag_is_spoken() -> None:
    """The model's text outside its `<say>` tag is private: it reaches the transcript as the 💭 item
    and never the body's voice, which hears the tag's body alone."""
    embodiment = FakeEmbodiment()
    script = [{"text": "thinking out loud<say>hello</say>"}]
    system = _build(embodiment, script=script)
    try:
        transcript, _ = _wire(system)
        _start(system)
        system.submit_speech("what are you thinking")

        def private_item_shown() -> bool:
            return any(
                m.get("metadata", {}).get("title") == "💭 private text"
                and m["content"] == "thinking out loud"
                for m in transcript.snapshot().conversation
            )

        assert _wait_until(private_item_shown)
        assert _wait_until(lambda: embodiment.said == ["hello"])
    finally:
        close_session(system)


def test_wire_without_agent_is_a_noop() -> None:
    """With no agent there is nothing to wire into: `wire` returns quietly (the banner already says
    why), so `run_app` stays linear in degraded mode."""
    system = AppSystem(
        loop=None,
        loop_thread=None,
        display_name=None,
        embodiment=None,
        wica=None,
        bridge=None,
        gesture=None,
        missing=["Agent: no key"],
        submit_speech=lambda _text: None,
    )
    wire(system)  # must not raise
    assert system.wica is None


def test_the_bodys_action_commands_are_registered_and_route_back_to_it() -> None:
    """A turn where the agent issues the body's action command reaches the body — proving `wire`
    registered each of `embodiment.commands()`."""
    embodiment = FakeEmbodiment()
    script = [
        {"tool_calls": [{"name": "wave", "args": {}}]},
        {"text": "<noop/>"},
    ]
    system = _build(embodiment, script=script)
    try:
        _wire(system)
        _start(system)
        assert system.wica is not None and "wave" in system.wica.agent.commands
        system.submit_speech("say hi with your hand")
        assert _wait_until(lambda: embodiment.waved == 1)
    finally:
        close_session(system)


def test_a_body_with_no_actions_registers_none() -> None:
    embodiment = FakeEmbodiment(commands=())
    system = _build(embodiment, script=[{"text": "<noop/>"}])
    try:
        _wire(system)
        assert system.wica is not None
        assert "wave" not in system.wica.agent.commands
        # The voice is still set — as the output Command, not one of the invokable ones.
        assert system.wica.agent.output_command_name == "say"
    finally:
        close_session(system)


def test_prompt_addendum_is_seeded_into_the_persona() -> None:
    """Whatever the body wants the model to know before its first turn (the robot's emotion names)
    is appended verbatim to the resolved persona, ahead of WICA's own primer."""
    embodiment = FakeEmbodiment(
        prompt_addendum="\n\n## Test section\n\nhello from the body"
    )
    system = _build(embodiment, script=[{"text": "<noop/>"}])
    try:
        assert system.wica is not None
        prompt = system.wica.agent.system_prompt
        assert prompt.startswith("You are a test double.")
        assert "\n\n## Test section\n\nhello from the body" in prompt
    finally:
        close_session(system)


def test_missing_lines_from_enter_reach_the_banner() -> None:
    """A component of the body that degraded inside `enter()` is one banner line; the body is up."""
    embodiment = FakeEmbodiment(
        missing=["TTS: key unset", "Audio processing: no binding"]
    )
    system = _build(embodiment, script=[{"text": "<noop/>"}])
    try:
        assert system.embodiment is embodiment
        assert "TTS: key unset" in system.missing
        assert "Audio processing: no binding" in system.missing
    finally:
        close_session(system)


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
        close_session(system)


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
        close_session(system)


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
        close_session(system)


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
        close_session(system)


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
        close_session(system)


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
        # The say finishing wakes no step (no `<next>` note): one reaction, and nothing else may.
        assert _wait_until(lambda: reactions.snapshot().count == 1)

        assert _wait_until(
            lambda: world.get("speech_input") is None,
            timeout=SPEECH_INPUT_TTL.total_seconds() + 1.0,
        )
        assert "speech_input" not in _flat(world.render_full_prompt())
        time.sleep(0.3)  # long enough for a step the reset might (wrongly) have woken
        assert reactions.snapshot().count == 1
    finally:
        close_session(system)


# --- Degraded start ------------------------------------------------------------------------------


def test_a_body_that_fails_to_enter_degrades_but_the_agent_still_reasons() -> None:
    """A body that cannot come up is captured in `missing` under its own name; nothing that needs it
    is built, and WICA still runs on typed text."""
    embodiment = FakeEmbodiment(raise_on_enter="no daemon reachable")
    built_over: list[Any] = []
    system = build_system(
        _config(script=[{"text": "<noop/>"}]),
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
        close_session(system)


def test_no_body_means_no_prompt_addendum() -> None:
    """Without a body there is nothing to seed: the agent builds over the plain persona."""
    embodiment = FakeEmbodiment(
        raise_on_enter="no body", prompt_addendum="\n\n## Test section\n\nhi"
    )
    system = build_system(_config(script=[{"text": "<noop/>"}]), embodiment)
    try:
        assert system.wica is not None
        assert "Test section" not in system.wica.agent.system_prompt
    finally:
        close_session(system)


class _Mic16k:
    """A body's mic as the engine sees it: a source declaring 16 kHz, like both embodiments'."""

    audio_format = AudioFormat(sample_rate=16000, channels=1, encoding="linear16")

    def start(self) -> asyncio.Queue[bytes]:
        return asyncio.Queue()

    def stop(self) -> None:
        pass


def _converting_asr_config(audio_source: dict[str, Any] | None) -> ASREngineConfig:
    """A real engine config whose module is fed 24 kHz, a rate neither body's mic produces."""
    engine: dict[str, Any] = {
        "module": {"type": "fake", "audio_format": {"sample_rate": 24000}}
    }
    if audio_source is not None:
        engine["audio_source"] = audio_source
    return ASREngineConfig.from_dict(engine)


@pytest.mark.parametrize(
    "audio_source",
    [
        None,
        {"type": "input-device", "device": "Built-in Microphone"},
        {"type": "custom"},
    ],
)
def test_the_engine_is_built_over_the_bodys_mic_and_converts_it(
    audio_source: dict[str, Any] | None,
) -> None:
    """A mic the body supplies is the engine's source whether the file names an input device (the
    computer, whose echo canceller opened it), nothing, or `custom` (the robot) — and its 16 kHz is
    converted for a module fed another rate, not refused."""
    engine = system_module._build_asr_engine(
        _converting_asr_config(audio_source), _Mic16k()
    )

    assert engine.audio_format.sample_rate == 16000
    assert engine.module_audio_format.sample_rate == 24000


def test_without_a_body_mic_the_engine_opens_the_configured_device() -> None:
    """No mic supplied (the computer without audio processing): the config reaches the engine as
    written, and the device is opened at what the module is fed."""
    config = _converting_asr_config(
        {"type": "input-device", "device": "Built-in Microphone"}
    )

    assert system_module.asr_config_for(config, None) is config
    engine = system_module._build_asr_engine(config, None)
    assert engine.audio_format.sample_rate == 24000


def test_a_file_source_next_to_a_body_mic_is_refused_not_overridden(
    tmp_path: Path,
) -> None:
    """Only an input device gives way to the body's mic. A config asking for a file while the body
    supplies a mic is the engine's own error (an `ASR:` banner line), not a silent choice."""
    config = _converting_asr_config(
        {"type": "file", "audio_file": str(tmp_path / "speech.wav")}
    )

    with pytest.raises(ValueError, match="custom"):
        system_module._build_asr_engine(config, _Mic16k())


def _asr_degrade_config() -> AppConfig:
    """A real ASR config referencing an unset env var (so `ASREngine` construction raises), with a
    fake WICA agent — the ASR side alone should degrade."""
    asr = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram-v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )
    return AppConfig(wica=scripted_wica_config([], persona="double"), asr_engine=asr)


def test_no_asr_section_is_speech_input_off_and_typing_still_drives_the_agent() -> None:
    """Optional (specs/configuration.md, "Optional sections"): without an `asr` section no engine is
    built, no bridge and no dictation Commands exist, nothing is said missing (off is the config's
    choice), and a typed turn still reaches the agent and its voice."""
    embodiment = FakeEmbodiment()
    calls: list[AudioSource | None] = []
    config = replace(_config(script=_say_once("typed, heard")), asr_engine=None)
    system = build_system(
        config, embodiment, asr_engine_factory=asr_factory(FakeASREngine(), calls)
    )
    try:
        assert calls == []  # no engine built
        assert system.bridge is None and not system.speech_on
        assert system.missing == []
        _wire(system)
        _start(system)
        assert system.wica is not None
        assert "start_dictation" not in system.wica.agent.commands
        system.submit_speech("hello")
        assert _wait_until(lambda: embodiment.said == ["typed, heard"])
    finally:
        close_session(system)


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
        close_session(system)


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
        close_session(system)
    assert embodiment.exited


# --- Teardown -------------------------------------------------------------------------------------


def test_close_session_stops_asr_before_the_body_exits() -> None:
    """Teardown stops the ASR bridge (cancelling the mic pump) *before* the body is released, then
    exits it — the order specs/app.md lists."""
    log: list[str] = []
    embodiment = FakeEmbodiment(log=log)
    fake_asr = FakeASREngine(log=log)
    system = _build(
        embodiment,
        script=[{"text": "<noop/>"}],
        asr=fake_asr,
    )
    _start(system)
    assert fake_asr.started  # capture was live before teardown

    close_session(system)

    assert not fake_asr.started
    assert embodiment.exited
    assert log == ["asr.stop", "embodiment.exit"]
    assert system.wica is not None and not system.wica.is_running


class _Loaded:
    def __init__(self, app: AppConfig) -> None:
        self.app = app


def _open(
    embodiment: FakeEmbodiment,
    *,
    script: list[dict],
    asr: FakeASREngine | None = None,
) -> Running:
    """`open_session` over *embodiment*, its config the scripted agent (the document is ignored)."""
    config = _config(script=script)
    return open_session(
        {},
        Path("."),
        load=lambda _data, *, base_dir: _Loaded(config),
        embodiment=lambda _loaded: embodiment,
        asr_engine_factory=asr_factory(asr if asr is not None else FakeASREngine()),
    )


def _closed(system: AppSystem) -> bool:
    """The session's loop stopped, its thread joined, the loop closed: nothing left running."""
    loop = system.loop
    thread = system.loop_thread
    assert loop is not None and thread is not None
    return loop.is_closed() and not thread.is_alive()


def _loop_stopped(system: AppSystem) -> bool:
    """Wait for the session's loop thread to leave run_forever and the loop to be closed."""
    return _wait_until(lambda: _closed(system))


def test_open_session_runs_a_session_that_closes_without_a_trace() -> None:
    """A session opened then closed: it answered, and its loop and thread are gone afterwards."""
    before = set(threading.enumerate())
    embodiment = FakeEmbodiment()
    running = _open(embodiment, script=_say_once("Hello there."))
    system = running.system
    assert system.wica is not None and system.wica.is_running

    system.submit_speech("hi")
    assert _wait_until(lambda: embodiment.said == ["Hello there."])
    running.close()

    assert embodiment.exited
    assert _closed(system)
    assert _wait_until(lambda: set(threading.enumerate()) <= before)


def test_a_start_failure_closes_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raise while starting — after build_system has entered the body — still exits it and
    closes the loop, instead of leaking both."""
    built: list[AppSystem] = []
    real_build = system_module.build_system
    monkeypatch.setattr(
        system_module,
        "build_system",
        lambda *a, **k: built.append(real_build(*a, **k)) or built[-1],
    )
    embodiment = FakeEmbodiment()

    with pytest.raises(RuntimeError, match="mic unavailable"):
        _open(
            embodiment,
            script=[{"text": "<noop/>"}],
            asr=FakeASREngine(raise_on_start="mic unavailable"),
        )

    assert embodiment.entered and embodiment.exited
    assert _loop_stopped(built[0])
    assert built[0].wica is not None and not built[0].wica.is_running


def test_presenters_that_fail_still_close_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body is entered by `build_system`, before the presenters exist: a raise while building
    them must exit it (a robot session, a spawned daemon) like a raise while starting does."""

    def broken(*_args: object) -> TranscriptLog:
        raise RuntimeError("the transcript could not subscribe")

    monkeypatch.setattr(system_module, "TranscriptLog", broken)
    embodiment = FakeEmbodiment()

    with pytest.raises(RuntimeError, match="could not subscribe"):
        _open(embodiment, script=[])

    assert embodiment.exited


def test_a_wiring_failure_still_closes_the_session() -> None:
    """`wire` can raise on its own — here the body offers two Commands under one name, which WICA
    rejects — and that, too, happens with the body already entered."""

    async def wave() -> str:
        """A second `wave`, colliding with the first."""
        return "OK"

    embodiment = FakeEmbodiment(commands=None)
    embodiment._commands = (embodiment.wave, wave)

    with pytest.raises(ValueError, match="wave"):
        _open(embodiment, script=[])

    assert embodiment.exited


def test_close_session_isolates_a_failing_step() -> None:
    """An ASR stop that raises is logged and skipped past: the body still exits, the loop stops."""
    embodiment = FakeEmbodiment()
    system = _build(
        embodiment,
        script=[{"text": "<noop/>"}],
        asr=FakeASREngine(raise_on_stop="provider socket broke"),
    )
    _start(system)

    close_session(system)  # must not raise

    assert embodiment.exited
    assert _loop_stopped(system)


def test_close_session_bounds_a_hung_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ASR stop that never returns is abandoned after the per-step timeout; teardown continues."""
    monkeypatch.setattr(system_module, "_SHUTDOWN_TIMEOUT_S", 0.1)
    embodiment = FakeEmbodiment()
    system = _build(
        embodiment,
        script=[{"text": "<noop/>"}],
        asr=FakeASREngine(hang_on_stop=True),
    )
    _start(system)

    close_session(system)

    assert embodiment.exited
    assert _loop_stopped(system)


def test_close_session_skips_never_started_bridge() -> None:
    """A bridge that was built but never started (nothing ran _start) is not stopped at teardown —
    the running-guard holds — while the body is still released."""
    log: list[str] = []
    embodiment = FakeEmbodiment(log=log)
    system = _build(
        embodiment,
        script=[{"text": "<noop/>"}],
        asr=FakeASREngine(log=log),
    )

    close_session(system)

    assert log == ["embodiment.exit"]  # no "asr.stop"
    assert _loop_stopped(system)


# --- The entry point: the app's folder (specs/app.md, "The entry point"; specs/setup.md) -------


_PERSONA = "You are the persona from the folder."

_FOLDER_CONFIG = {
    "wica": {
        "agent": {
            "provider": "fake",
            "model": "scripted",
            "system_prompt_file": "persona.md",
        }
    }
}


def _fake_app(default_config: dict[str, Any] | None = None) -> App:
    """An app with one section, `wica`, described by WICA's own contract; autostart on."""
    import wica.setup

    return App(
        name="wica-interaction-fake",
        host="wica-interaction",
        sections=(Section("wica", wica.setup),),
        default_config=default_config if default_config is not None else _FOLDER_CONFIG,
        autostart=True,
    )


class _Page:
    """Stands in for the built page: records how it was built and launched."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.open_on: str | None = None
        self.launched: dict[str, Any] | None = None
        self.session: Session | None = None
        self.model: AppModel | None = None

    def build(self, model: AppModel, session: Session, *, open_on: str) -> _Page:
        self.model, self.session, self.open_on = model, session, open_on
        return self

    def launch(self, **options: Any) -> None:
        self.launched = options
        assert self.session is not None
        # While the page is served, the session runs (or not) as launch decided.
        self.status_while_served = self.session.status
        if self.raises is not None:
            raise self.raises


# The configs the session was opened with, newest last.
_OPENED: list[AppConfig] = []


def _built(config: AppConfig, embodiment: Embodiment, **_seams: Any) -> AppSystem:
    """A stand-in for `build_system`: records the config it was given and builds nothing."""
    _OPENED.append(config)
    return AppSystem(
        loop=None,
        loop_thread=None,
        display_name=embodiment.display_name,
        embodiment=None,
        wica=None,
        bridge=None,
        gesture=None,
        missing=[],
        submit_speech=lambda _text: None,
    )


def _load_wica(data: Any, *, base_dir: Path) -> _Loaded:
    return _Loaded(
        AppConfig(
            wica=WicaConfig.from_dict(data["wica"], base_dir=base_dir),
            asr_engine=_asr_config(),
        )
    )


# The folders `ensure_persona` was called on, newest last.
_PERSONA_COPIED: list[Path] = []


def _ensure_persona(path: Path) -> Path:
    _PERSONA_COPIED.append(path)
    target = path / "persona.md"
    if not target.exists():
        target.write_text(_PERSONA, encoding="utf-8")
    return target


def _run_argv(
    monkeypatch: pytest.MonkeyPatch,
    *argv: str,
    page: _Page | None = None,
    default_config: dict[str, Any] | None = None,
) -> _Page:
    """`run_app` with *argv*, the page and `build_system` replaced."""
    page = page if page is not None else _Page()
    monkeypatch.setattr(system_module, "build_page", page.build)
    monkeypatch.setattr(system_module, "build_system", _built)
    run_app(
        _fake_app(default_config),
        load=_load_wica,
        embodiment=lambda _loaded: FakeEmbodiment(),
        ensure_persona=_ensure_persona,
        argv=list(argv),
    )
    return page


def _run(
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    *flags: str,
    page: _Page | None = None,
    default_config: dict[str, Any] | None = None,
) -> _Page:
    """`run_app` on *folder* with *flags*, the page and `build_system` replaced."""
    return _run_argv(
        monkeypatch,
        "--folder",
        str(folder),
        *flags,
        page=page,
        default_config=default_config,
    )


def test_run_app_fills_a_new_folder_and_starts_from_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A new folder gets the default config and the persona; with autostart the session starts
    from the saved config before the page is served, the persona resolved in the folder whatever
    the working directory, and the page opens on the Interaction tab."""
    folder = tmp_path / "app"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _OPENED.clear()

    page = _run(monkeypatch, folder, "--port", "7999")

    assert json.loads((folder / "config.json").read_text(encoding="utf-8")) == (
        _FOLDER_CONFIG
    )
    assert (folder / "persona.md").read_text(encoding="utf-8") == _PERSONA
    prompt = _OPENED[-1].wica.agent.system_prompt_file
    assert prompt is not None
    assert Path(prompt).resolve() == (folder / "persona.md").resolve()
    assert page.open_on == "interaction"
    assert page.status_while_served == "running"
    assert page.launched == {"server_name": "127.0.0.1", "server_port": 7999}
    # Serving returned: the session was stopped.
    assert page.session is not None and page.session.status == "stopped"


def test_run_app_with_no_start_opens_on_setup_and_builds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _OPENED.clear()
    page = _run(monkeypatch, tmp_path / "app", "--no-start")

    assert _OPENED == []
    assert page.open_on == "setup"
    assert page.status_while_served == "stopped"


def test_run_app_keeps_what_the_user_changed_in_the_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second launch reads the saved config and leaves an edited persona alone."""
    folder = tmp_path / "app"
    _run(monkeypatch, folder, "--no-start")
    (folder / "persona.md").write_text("Edited.", encoding="utf-8")
    saved = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    saved["wica"]["agent"]["model"] = "another"
    (folder / "config.json").write_text(json.dumps(saved), encoding="utf-8")
    _OPENED.clear()

    _run(monkeypatch, folder)

    assert (folder / "persona.md").read_text(encoding="utf-8") == "Edited."
    assert _OPENED[-1].wica.agent.model == "another"


def test_a_config_with_blockers_keeps_the_page_up_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """At launch, a config that cannot run starts nothing; the page is served all the same, the
    reason on the session for every browser tab — a service started at boot never crash-loops."""
    broken = {"wica": {"agent": {"provider": "fake"}}}  # no model
    _OPENED.clear()

    page = _run(monkeypatch, tmp_path / "app", default_config=broken)

    assert _OPENED == []
    assert page.launched is not None
    assert page.session is not None and page.session.status == "stopped"
    assert any("model" in reason for reason in page.session.reasons)


def test_a_session_running_when_serving_raises_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = _Page(raises=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, tmp_path / "app", page=page)

    assert page.status_while_served == "running"
    assert page.session is not None and page.session.status == "stopped"


# --- From a config file, and `configure` (specs/setup.md, "Three launches") ---------------------


def _config_file(directory: Path) -> Path:
    """A config file in *directory*, its persona next to it."""
    directory.mkdir(parents=True)
    (directory / "persona.md").write_text(
        "You are the persona next to the file.", "utf-8"
    )
    path = directory / "robot.json"
    path.write_text(json.dumps(_FOLDER_CONFIG, indent=2), encoding="utf-8")
    return path


def test_run_app_from_a_file_runs_it_as_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--config FILE`: the session starts at launch with no flag, the persona resolved next to
    the file whatever the working directory, the Setup tab read-only; nothing is written — not
    the file, not a folder, no persona copied."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = _config_file(tmp_path / "configs")
    before = path.read_text(encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _OPENED.clear()
    _PERSONA_COPIED.clear()

    page = _run_argv(monkeypatch, "--config", str(path))

    prompt = _OPENED[-1].wica.agent.system_prompt_file
    assert prompt is not None
    assert Path(prompt).resolve() == (path.parent / "persona.md").resolve()
    assert page.open_on == "interaction"
    assert page.status_while_served == "running"
    assert page.model is not None and not page.model.editable
    assert page.model.path == path.parent.resolve()
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["persona.md", "robot.json"]
    assert _PERSONA_COPIED == []
    assert not (tmp_path / "xdg").exists()


def test_run_app_from_a_file_with_no_start_builds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _config_file(tmp_path / "configs")
    _OPENED.clear()

    page = _run_argv(monkeypatch, "--config", str(path), "--no-start")

    assert _OPENED == []
    assert page.open_on == "setup"


def test_run_app_from_a_file_takes_its_keys_from_the_env_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `.env` is the one next to the file, unless `--env` names another."""
    path = _config_file(tmp_path / "configs")
    keys = tmp_path / "keys" / "robot.env"

    beside = _run_argv(monkeypatch, "--config", str(path), "--no-start")
    named = _run_argv(
        monkeypatch, "--config", str(path), "--env", str(keys), "--no-start"
    )

    assert beside.model is not None and named.model is not None
    assert beside.model.source.env_path == path.parent / ".env"
    assert named.model.source.env_path == keys


@pytest.mark.parametrize("command", [[], ["configure"]])
def test_a_missing_config_file_stops_the_app_at_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    """A file that is not there is a command-line error naming it, before anything is built or
    served — for a launch and for `configure` alike."""
    missing = tmp_path / "nowhere.json"
    page = _Page()
    _OPENED.clear()

    with pytest.raises(SystemExit) as exited:
        _run_argv(monkeypatch, *command, "--config", str(missing), page=page)

    assert exited.value.code == 2
    assert str(missing) in capsys.readouterr().err
    assert page.launched is None and _OPENED == []


def test_folder_and_config_are_one_or_the_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _config_file(tmp_path / "configs")
    with pytest.raises(SystemExit):
        _run_argv(monkeypatch, "--folder", str(tmp_path / "app"), "--config", str(path))


class _ConfigPage:
    """Stands in for app-setup's `config_page`: records the model and how it was launched."""

    def __init__(self) -> None:
        self.model: AppModel | None = None
        self.launched: dict[str, Any] | None = None

    def build(self, model: AppModel) -> _ConfigPage:
        self.model = model
        return self

    def launch(self, **options: Any) -> None:
        self.launched = options


def _configure(monkeypatch: pytest.MonkeyPatch, *argv: str) -> _ConfigPage:
    config = _ConfigPage()
    monkeypatch.setattr(system_module, "config_page", config.build)
    page = _Page()
    _OPENED.clear()
    _run_argv(monkeypatch, "configure", *argv, page=page)
    # No session: neither the session's page nor a build.
    assert page.launched is None and _OPENED == []
    return config


def test_configure_edits_a_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`configure --config FILE`: the Setup and Secrets page over that file, which Save writes
    back; the page served where asked; no session."""
    path = _config_file(tmp_path / "configs")

    config = _configure(
        monkeypatch, "--config", str(path), "--host", "0.0.0.0", "--port", "7861"
    )

    model = config.model
    assert model is not None and model.editable
    assert model.source.config_path == path
    assert config.launched == {"server_name": "0.0.0.0", "server_port": 7861}
    edited = model.saved_config()
    edited["wica"]["agent"]["model"] = "another"
    model.save(edited)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["wica"]["agent"]["model"] == "another"


def test_configure_edits_the_app_folder_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without `--config`, `configure` edits the app's folder (the default one here), made ready
    as a launch would: the default config, the persona."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    folder = tmp_path / "xdg" / "wica-interaction-fake"

    config = _configure(monkeypatch)

    assert config.model is not None and config.model.editable
    assert config.model.path == folder
    assert json.loads((folder / "config.json").read_text(encoding="utf-8")) == (
        _FOLDER_CONFIG
    )
    assert (folder / "persona.md").read_text(encoding="utf-8") == _PERSONA


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
        vision=VisionConfig(fps=100.0, hold_s=0.0),
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
        close_session(system)


def test_another_sign_wakes_the_agent_again() -> None:
    """A hand switching sign with no sign-less frame between is a second trigger: `sign_shown` fires
    on `stop → five` too, and the new prompt carries the new sign."""
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    classifier = _ScriptedClassifier(fist=0.9)
    # One step per trigger, each speaking: the second is the one the new sign wakes.
    script = [
        {"text": "<say>okay, stopping</say>"},
        {"text": "<say>five it is</say>"},
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
        close_session(system)


def test_vision_is_off_without_a_config_section() -> None:
    system = _build(FakeEmbodiment(), script=[])
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert "## Hand signs" not in system.wica.agent.system_prompt
        assert not any(m.startswith("Vision:") for m in system.missing)
    finally:
        close_session(system)


def test_a_vision_section_present_is_hand_signs_on() -> None:
    """The section is the switch (specs/configuration.md, "Optional sections"): present, even
    `{}`, builds the gesture input with its entries and the persona's section."""
    system = _build(FakeEmbodiment(), script=[], vision=VisionConfig(fps=100.0))
    try:
        assert system.gesture is not None and system.signs_on
        assert system.wica is not None
        assert system.wica.world.is_registered("hand_gesture_1")
        assert "## Hand signs" in system.wica.agent.system_prompt
    finally:
        close_session(system)


def test_vision_max_hands_fps_and_hold_reach_the_gesture_input() -> None:
    """The configured hand cap, rate and hold are what the pipeline (and the body's camera) run
    with, not the library's or the module's defaults."""
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    system = _build(
        embodiment,
        script=[],
        vision=VisionConfig(max_hands=1, fps=20.0, hold_s=0.25, release_hold_s=0.75),
        models=GestureModels(
            detector=_ScriptedDetector(), classifier=_ScriptedClassifier(fist=0.9)
        ),
    )
    try:
        assert system.gesture is not None
        assert system.gesture.status()["max_hands"] == 1
        assert system.gesture.status()["target_fps"] == 20.0
        assert system.gesture.status()["hold_s"] == 0.25
        assert system.gesture.status()["release_hold_s"] == 0.75
        # The camera the body handed over was asked for the same rate.
        assert [camera.fps for camera in embodiment.cameras] == [20.0]
    finally:
        close_session(system)


def test_vision_needs_a_body() -> None:
    """No body ⇒ no camera ⇒ no gesture input and no `hand_gesture_<n>` entry (the agent is never
    told about hands it cannot see); the banner carries only the body's line."""
    system = build_system(
        _config(script=[], vision=VisionConfig()),
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
        close_session(system)


def test_close_session_stops_vision_before_the_body_exits() -> None:
    embodiment = FakeEmbodiment(frames=_HAND_FRAME)
    system = _vision_system(embodiment, _ScriptedClassifier(fist=0.1), [])
    try:
        _wire(system)
        _start(system)
        assert system.gesture is not None and system.gesture.running
        assert _wait_until(lambda: _camera_frames(embodiment) > 0)
    finally:
        close_session(system)
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
    return [{"text": "<noop/>"}]


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
        wire(system)
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
        close_session(system)


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
        close_session(system)


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
        close_session(system)
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
        close_session(system)


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
        close_session(system)


def test_no_inputs_without_a_body_or_without_an_agent(tmp_path: Path) -> None:
    """A body that failed to enter is not asked for its inputs; a body with no agent to deliver to
    has them neither registered nor started."""
    no_body = FakeEmbodiment(raise_on_enter="unplugged", inputs=(ScriptedInput(),))
    system = _build(no_body, script=_noop_script())
    try:
        assert system.inputs == () and no_body.inputs_asked == 0
    finally:
        close_session(system)

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
        close_session(system)
    assert body_input.stopped == 0


def test_close_session_stops_the_inputs_right_after_the_agent() -> None:
    """Teardown stops each input before anything the body owns: the agent first, then the inputs,
    then the ASR bridge, then the body — the order specs/app.md lists."""
    log: list[str] = []
    body_input = ScriptedInput(log=log)
    embodiment = FakeEmbodiment(log=log, inputs=(body_input,))
    system = _build(embodiment, script=_noop_script(), asr=FakeASREngine(log=log))
    _wire(system)
    _start(system)

    close_session(system)

    assert body_input.stopped == 1
    assert log == ["input.stop", "asr.stop", "embodiment.exit"]


# --- The latency view's collection (specs/latency-view.md) --------------------------------------


class _SoundingBody(FakeEmbodiment):
    """A body whose voice opens a span of its own with a `sound` event a moment after it starts,
    and declares that event as the voice's effect start."""

    def __init__(self) -> None:
        super().__init__(
            effects={"say": EffectStart("fake.voice", "sound", latency_s=0.01)}
        )

    async def say(self, text: OutputStream) -> str:
        with trace.get_tracer("tests").start_as_current_span("fake.voice") as span:
            await asyncio.sleep(0.05)
            span.add_event("sound")
            self.said.append(await text.read())
        return "OK"


def test_a_typed_turn_is_measured_from_its_write_to_the_voices_declared_sound() -> None:
    embodiment = _SoundingBody()
    system = _build(embodiment, script=_say_once("hello there"))
    try:
        assert system.latency is not None
        _wire(system)
        _start(system)
        system.submit_speech("hi")
        latency = system.latency

        def complete() -> bool:
            records = latency.snapshot().records
            return bool(records) and records[0].complete

        assert _wait_until(complete)
        (record,) = latency.snapshot().records
        assert record.trigger == "typed" and record.origin_measured
        assert record.effect_command == "say"
        say = next(s for s in record.steps if s.lane == "Voice")
        # The effect is the voice's own `sound`, 50 ms into the Command, plus its declared 10 ms.
        assert record.effect_at is not None
        assert (record.effect_at - say.started_at).total_seconds() >= 0.06
        names = {s.name for s in record.steps}
        assert {
            "reaching the agent",
            "gathering inputs",
            "building the prompt",
        } <= names
        assert (
            "first (cold)" in record.flags
        )  # the run's first reaction that called the model
    finally:
        close_session(system)


def test_an_exporter_that_cannot_be_had_is_a_banner_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WICA_INTERACTION_TRACES", "zipkin")
    system = _build(FakeEmbodiment(), script=_noop_script())
    try:
        assert any("WICA_INTERACTION_TRACES" in line for line in system.missing)
        assert system.latency is not None  # collection itself goes on
    finally:
        close_session(system)


def test_with_no_agent_there_is_nothing_to_measure(tmp_path: Path) -> None:
    wica = WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt_file": str(tmp_path / "no-such-prompt.md"),
            }
        }
    )
    system = build_system(
        AppConfig(wica=wica, asr_engine=_asr_config()),
        FakeEmbodiment(),
        asr_engine_factory=asr_factory(FakeASREngine()),
    )
    try:
        assert system.wica is None and system.latency is None
    finally:
        close_session(system)
