"""Functional tests for the app wiring (`build_system`).

Deterministic, no browser/mic/network/robot: the agent runs against WICA's `fake` provider (a scripted
model), the robot is an injected fake `ReachyMiniApi`, the ASR engine is an injected fake, and only the
wiring `main` does — minus the Gradio page — is exercised: `build_system`, then `wire` with a
`TranscriptLog` (and a `ReactionLog`) the test builds itself, playing the UI's role exactly as
`build_ui` would, then `_start`. Covers the two input paths (typed text and a closed ASR segment)
reaching the agent and its `say` reaching the robot speaker (proving the output Command is set), the
model's free text reaching the transcript (proving the sink is set), a robot action reaching the robot
(proving `wire` registers the Commands), plus the degrade paths at the robot and ASR build seams.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Self, cast

import gradio as gr
import numpy as np
import pytest
from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment
from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.config import ReachyMiniConfig
from vision_modules.hand import DetectedHand
from wica.config import WicaConfig
from wica.contrib.gradio import ReactionLog, TranscriptLog

from wica_interaction import app
from wica_interaction.app import (
    AppSystem,
    _degraded_system,
    _serve,
    _shutdown,
    _start,
    build_system,
    wire,
)
from wica_interaction.app_ui import display_entry
from wica_interaction.config import AppConfig, VisionConfig
from wica_interaction.gesture_input import GestureModels


class FakeReachyMiniApi:
    """The slice of `ReachyMiniApi` the app touches: an async-context robot whose `say` records.

    `build_system` enters it on the loop (`__aenter__`); `VoiceOutput.say` calls `say`. Set
    `raise_on_enter` to simulate a robot that fails to connect (the broad degrade path), and
    `raise_on_list_emotions` for a robot whose emotions library fails to load. Pass `log` (shared
    with `FakeASREngine`) to record teardown steps in order.
    """

    # What the startup fetch returns — the same names as the bridge's `fake` library.
    emotions = ("happy", "sad", "curious")

    def __init__(
        self,
        *,
        raise_on_enter: str | None = None,
        raise_on_list_emotions: str | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.said: list[str] = []
        self.played: list[str] = []
        self.entered = False
        self.exited = False
        self.emotion_fetches = 0
        self._raise_on_enter = raise_on_enter
        self._raise_on_list_emotions = raise_on_list_emotions
        self._log = log

    async def __aenter__(self) -> Self:
        if self._raise_on_enter is not None:
            raise RuntimeError(self._raise_on_enter)
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._log is not None:
            self._log.append("api.exit")
        self.exited = True

    async def say(self, text: str) -> None:
        self.said.append(text)

    # The perception slice: the camera frame the gesture input polls (a bright synthetic frame the
    # scripted detector below reads as "a hand"; set `frame` to None for "nothing buffered").
    frame: np.ndarray | None = np.full((48, 64, 3), 200, dtype=np.uint8)
    camera_polls = 0

    async def get_camera_frame(self) -> np.ndarray | None:
        self.camera_polls += 1
        return self.frame

    # The robot-control slice: enough for a wiring test that a registered action command routes here.
    async def get_motors_state(self) -> str:
        return "enabled"

    async def play_emotion(self, name: str) -> None:
        self.played.append(name)

    async def list_emotions(self) -> list[str]:
        self.emotion_fetches += 1
        if self._raise_on_list_emotions is not None:
            raise RuntimeError(self._raise_on_list_emotions)
        return list(self.emotions)


class FakeASREngine:
    """The slice of `ASREngine` that `AsrBridge` and `build_system` touch — construction needs no key.

    `AsrBridge.__init__` assigns `on_speech_segment`; `start`/`stop` and the `status()`/`dictating`/
    `segmentation_mode` accessors back the bridge's own surface. Pass `log` (shared with
    `FakeReachyMiniApi`) to record teardown steps in order. `raise_on_start` / `raise_on_stop` make
    the corresponding call raise; `hang_on_stop` makes `stop` never return (a wedged provider).
    """

    def __init__(
        self,
        *,
        log: list[str] | None = None,
        raise_on_start: str | None = None,
        raise_on_stop: str | None = None,
        hang_on_stop: bool = False,
    ) -> None:
        self.on_speech_segment: Any = None
        self.dictating = False
        self.segmentation_mode = "utterance"
        self.started = False
        self._log = log
        self._raise_on_start = raise_on_start
        self._raise_on_stop = raise_on_stop
        self._hang_on_stop = hang_on_stop

    async def start(self) -> None:
        if self._raise_on_start is not None:
            raise RuntimeError(self._raise_on_start)
        self.started = True

    async def stop(self) -> None:
        if self._log is not None:
            self._log.append("asr.stop")
        if self._raise_on_stop is not None:
            raise RuntimeError(self._raise_on_stop)
        if self._hang_on_stop:
            await asyncio.Event().wait()
        self.started = False

    def status(self) -> dict[str, Any]:
        return {"running": self.started}


def _config(
    *,
    script: list[dict],
    reachy: ReachyMiniConfig | None = None,
    vision: VisionConfig | None = None,
) -> AppConfig:
    """An `AppConfig` whose WICA section is the `fake` provider running *script*.

    The ASR section is unused here (tests inject fakes), so it is `None` behind a cast. `vision`
    is off unless given (the gesture input is built only with a section).
    """
    wica = WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt": "You are a test double.",
                "model_kwargs": {"delay_s": 0, "script": script},
            }
        }
    )
    return AppConfig(
        wica=wica,
        asr_engine=cast(ASREngineConfig, None),
        reachy=reachy if reachy is not None else ReachyMiniConfig(backend="fake"),
        vision=vision,
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


def _wire(system: AppSystem) -> tuple[TranscriptLog, ReactionLog]:
    """Play the UI's role: build the two contrib presenters over the agent (exactly what `build_ui`
    creates) and wire the transcript's sink + the Commands, as `main` does before starting."""
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


def test_typed_text_drives_agent_and_speaks_through_robot() -> None:
    """A typed turn becomes `speech_input`, the agent reasons, its `say` plays through the robot."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=_say_once("hello there")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert (
            fake_api.entered
        )  # build_system opened the robot's media session on the loop
        assert system.voice is not None and system.bridge is not None
        _wire(
            system
        )  # sets `say` as the output Command — without it the model could not speak
        _start(system)
        # The typed-text box writes the same World update a closed ASR segment makes.
        system.submit_speech("hi")
        assert _wait_until(lambda: fake_api.said == ["hello there"])
    finally:
        _shutdown(system)


def test_free_text_lands_in_the_transcript_sink() -> None:
    """The model's free text for a step reaches the transcript's output sink as the 💭 item — the
    one observable proof that `wire` set the sink on the agent."""
    fake_api = FakeReachyMiniApi()
    script = [
        {"text": "thinking out loud"},
        {"tool_calls": [{"name": "noop", "args": {}}]},
    ]
    system = build_system(
        _config(script=script),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
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
    why), so `main` stays linear in degraded mode."""
    system = _degraded_system("config unreadable")
    wire(system, TranscriptLog(None, display_entry))  # must not raise
    assert system.wica is None


def test_robot_action_command_is_registered_and_routes_to_the_robot() -> None:
    """A turn where the agent issues a robot action command reaches the robot — proving `wire`
    registered the RobotControl commands and routes them through the shared api."""
    fake_api = FakeReachyMiniApi()
    script = [
        {"tool_calls": [{"name": "play_emotion", "args": {"name": "happy"}}]},
        {"tool_calls": [{"name": "noop", "args": {}}]},
    ]
    system = build_system(
        _config(script=script),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        _wire(system)
        _start(system)
        system.submit_speech("show me happy")
        assert _wait_until(lambda: fake_api.played == ["happy"])
    finally:
        _shutdown(system)


def _play_emotion_description(system: AppSystem) -> str:
    """The `play_emotion` tool description the model is bound to."""
    assert system.wica is not None
    return system.wica.agent.commands["play_emotion"].tool.description


def test_emotion_names_are_seeded_into_the_prompt_and_the_play_emotion_tool() -> None:
    """The names are fetched once at build and reach the model before its first turn, in both places
    it reads them: the composed system prompt (after the persona) and `play_emotion`'s description.
    There is no `list_emotions` Command any more."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert fake_api.emotion_fetches == 1
        assert system.emotions == fake_api.emotions
        assert system.wica is not None
        prompt = system.wica.agent.system_prompt
        # The persona comes first, the seeded section after it, and both precede WICA's primer.
        assert prompt.startswith("You are a test double.")
        section_at = prompt.index("Your emotions")
        for name in fake_api.emotions:
            assert prompt.index(name) > section_at
        _wire(system)
        _start(system)
        assert "list_emotions" not in system.wica.agent.commands
        description = _play_emotion_description(system)
        for name in fake_api.emotions:
            assert name in description
    finally:
        _shutdown(system)


def test_failed_emotion_fetch_degrades_to_a_banner_line() -> None:
    """A robot whose emotions library fails to load still comes up: the banner says so on the Robot
    axis, the prompt carries no emotion section, and `play_emotion` is still registered (with its
    plain docstring) — the rest of the robot is unaffected."""
    fake_api = FakeReachyMiniApi(raise_on_list_emotions="dataset download failed")
    system = build_system(
        _config(script=_say_once("still here")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert system.emotions is None
        assert any(
            m.startswith("Robot:") and "dataset download failed" in m
            for m in system.missing
        )
        assert system.api is not None and system.voice is not None  # the robot is up
        assert system.wica is not None
        assert "Your emotions" not in system.wica.agent.system_prompt
        _wire(system)
        _start(system)
        assert "Valid names" not in _play_emotion_description(system)
        system.submit_speech("hello")
        assert _wait_until(lambda: fake_api.said == ["still here"])
    finally:
        _shutdown(system)


def test_no_robot_means_no_emotion_section() -> None:
    """Without the robot there is nothing to fetch from: the agent still builds, over the plain
    persona, and no emotion section is invented."""
    fake_api = FakeReachyMiniApi(raise_on_enter="no daemon reachable")
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
    )
    try:
        assert fake_api.emotion_fetches == 0
        assert system.emotions is None
        assert system.wica is not None
        assert "Your emotions" not in system.wica.agent.system_prompt
        assert not any(
            "emotions" in m for m in system.missing
        )  # only the Robot: enter line
    finally:
        _shutdown(system)


def test_empty_typed_text_is_ignored() -> None:
    """Whitespace-only typed input never wakes the agent. Proven by ordering against a real turn, not
    by sleeping on an absence: the empties go in first, and by the time the real turn's reply lands the
    transcript shows that real turn as the only input the agent ever reacted to."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=_say_once("real reply")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        transcript, _ = _wire(system)
        _start(system)
        system.submit_speech("   ")
        system.submit_speech("")
        system.submit_speech("a real turn")
        assert _wait_until(lambda: fake_api.said == ["real reply"])
        # Any trigger (an erroneous empty one included) is recorded before its step's reply, so the
        # transcript is complete for inputs by now.
        assert _user_items(transcript) == ['🗣️ "a real turn"']
    finally:
        _shutdown(system)


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
    agent, whose `say` reaches the robot — the same outcome as a typed turn."""
    fake_api = FakeReachyMiniApi()
    fake_asr = FakeASREngine()
    system = build_system(
        _config(script=_say_once("segment reply")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, fake_asr),
    )
    try:
        _wire(system)
        _start(system)
        _emit_segment(system, fake_asr, _final_segment("please reply"))
        assert _wait_until(lambda: fake_api.said == ["segment reply"])
    finally:
        _shutdown(system)


def test_empty_final_segment_wakes_agent() -> None:
    """A final that closed on silence (empty transcript) still reaches the agent, so it can react to
    the user saying nothing — unlike an empty typed line, which is ignored."""
    fake_api = FakeReachyMiniApi()
    fake_asr = FakeASREngine()
    system = build_system(
        _config(script=_say_once("you went quiet")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, fake_asr),
    )
    try:
        _wire(system)
        _start(system)
        _emit_segment(system, fake_asr, _final_segment(""))
        assert _wait_until(lambda: fake_api.said == ["you went quiet"])
    finally:
        _shutdown(system)


def test_robot_connect_failure_degrades_but_agent_still_reasons() -> None:
    """A robot that fails to connect is captured in `missing`; voice/bridge are None, WICA still runs."""
    fake_api = FakeReachyMiniApi(raise_on_enter="no daemon reachable")
    # No asr_engine injected: with the robot gone there is no mic, so no bridge is built at all.
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
    )
    try:
        assert system.api is None  # the failed enter degraded it to None
        assert system.voice is None  # no robot ⇒ no voice
        assert system.bridge is None  # no robot mic ⇒ no speech-in
        assert any("no daemon reachable" in m for m in system.missing)
        # The fake agent built fine, and still reasons over typed input: a step runs (a prompt is
        # captured).
        assert system.wica is not None
        _, reactions = _wire(system)
        _start(system)
        system.submit_speech("are you there")
        assert _wait_until(lambda: _reaction_captured(reactions))
    finally:
        _shutdown(system)


def _asr_degrade_config() -> AppConfig:
    """A real ASR config referencing an unset env var (so `ASREngine` construction raises), with a fake
    WICA agent and (in the test) an injected fake robot — the ASR side alone should degrade."""
    wica = WicaConfig.from_dict(
        {"agent": {"provider": "fake", "model": "scripted", "system_prompt": "double"}}
    )
    asr = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram_v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )
    return AppConfig(
        wica=wica,
        asr_engine=asr,
        reachy=ReachyMiniConfig(backend="fake"),
    )


def test_missing_asr_key_degrades_but_robot_voice_survive(monkeypatch: Any) -> None:
    """With the robot up but the ASR key unset, ASR degrades (bridge None) while the voice survives."""
    monkeypatch.delenv("WICA_TEST_UNSET_ASR_KEY", raising=False)
    fake_api = FakeReachyMiniApi()
    # No asr_engine injected: build_system builds a real ASREngine (RobotMicSource(api)), which raises.
    system = build_system(_asr_degrade_config(), api=cast(ReachyMiniApi, fake_api))
    try:
        assert fake_api.entered
        assert system.voice is not None  # the robot is up, so the voice built
        assert system.bridge is None  # ASR could not build
        assert any("WICA_TEST_UNSET_ASR_KEY" in m for m in system.missing)
        assert system.wica is not None
        _, reactions = _wire(system)
        _start(system)
        system.submit_speech("still works")  # typed text still drives the agent
        assert _wait_until(lambda: _reaction_captured(reactions))
    finally:
        _shutdown(system)


# A tts-engine module type that is not in its registry, so `load_module` rejects it. Used to fail
# the bridge's synthesizer build deterministically: unlike an unset `api_key_env`, this needs no
# provider SDK installed (a provider module raises on its own lazy import before it ever reads the
# env var, so which failure you get would depend on which extra happens to be installed) and no
# model download. The degrade contract is about *any* failed voice build, not one provider's.
_UNKNOWN_TTS_MODULE = "no_such_tts_module"


def _tts_degrade_config() -> AppConfig:
    """A `reachy.tts` block naming an unknown module type (so the bridge's synthesizer build fails),
    with a fake WICA agent and the bridge's `fake` robot backend — only the voice should degrade
    (the bridge itself, not this app, builds and records the failed synthesizer)."""
    wica = WicaConfig.from_dict(
        {"agent": {"provider": "fake", "model": "scripted", "system_prompt": "double"}}
    )
    reachy = ReachyMiniConfig(
        backend="fake",
        tts={"module": {"type": _UNKNOWN_TTS_MODULE}},
    )
    return AppConfig(
        wica=wica,
        asr_engine=cast(ASREngineConfig, None),  # a fake engine is injected
        reachy=reachy,
    )


def test_failed_voice_build_degrades_only_the_voice() -> None:
    """With the voice failing to build, the robot still comes up (mic/ASR/actions intact) and `say`
    reports a readable TTS error instead of the whole robot being reported missing."""
    fake_asr = FakeASREngine()
    # No `api=` injected: build_system builds the real fake-backend api, whose `tts` block fails to
    # build — the bridge records that as `synthesizer_error` instead of raising.
    system = build_system(_tts_degrade_config(), asr_engine=cast(ASREngine, fake_asr))
    try:
        assert system.api is not None  # the robot is up …
        assert system.voice is not None  # … so the voice exists (say is registered) …
        assert system.bridge is not None  # … and so does the mic-fed ASR bridge
        # The failure is reported on the TTS axis, carrying the bridge's own recorded error (not a
        # canned string) — and not as a robot failure.
        assert any(
            m.startswith("TTS:") and _UNKNOWN_TTS_MODULE in m for m in system.missing
        )
        assert not any(m.startswith("Robot:") for m in system.missing)
        # say degrades to a readable string on the app loop (VoiceOutput maps the BridgeError).
        assert system.loop is not None
        result = asyncio.run_coroutine_threadsafe(
            system.voice.say("hi"), system.loop
        ).result(2.0)
        assert result.startswith("TTS error:")
        # And the agent still reasons over typed input despite the voice being degraded.
        assert system.wica is not None
        _, reactions = _wire(system)
        _start(system)
        system.submit_speech("are you there")
        assert _wait_until(lambda: _reaction_captured(reactions))
    finally:
        _shutdown(system)


def test_absent_tts_block_is_reported_on_the_tts_axis() -> None:
    """A `reachy` config with no `tts` block still brings the robot up (mic/ASR intact), reports the
    missing voice as a `TTS:` banner entry — not a robot failure — and `say` degrades to a string."""
    fake_asr = FakeASREngine()
    config = _config(script=_say_once("hi"), reachy=ReachyMiniConfig(backend="fake"))
    # No `api=` injected, so the real fake-backend api is built and the TTS check runs.
    system = build_system(config, asr_engine=cast(ASREngine, fake_asr))
    try:
        assert system.api is not None
        assert system.voice is not None
        assert system.bridge is not None
        assert (
            "TTS: no reachy.tts block configured (say is unavailable)" in system.missing
        )
        assert not any(m.startswith("Robot:") for m in system.missing)
        assert system.loop is not None
        result = asyncio.run_coroutine_threadsafe(
            system.voice.say("hi"), system.loop
        ).result(2.0)
        assert result.startswith("TTS error:")
    finally:
        _shutdown(system)


def test_shutdown_stops_asr_before_the_robot_exits() -> None:
    """Teardown stops the ASR bridge (cancelling the mic pump) *before* the robot's media session
    closes, then exits the api — the order specs/app.md lists."""
    log: list[str] = []
    fake_api = FakeReachyMiniApi(log=log)
    fake_asr = FakeASREngine(log=log)
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, fake_asr),
    )
    _start(system)
    assert fake_asr.started  # capture was live before teardown

    _shutdown(system)

    assert not fake_asr.started
    assert fake_api.exited
    assert log == ["asr.stop", "api.exit"]
    assert system.wica is not None and not system.wica.is_running


class _NeverLaunched:
    """Stands in for the built page in `_serve`: `_start` raises first, so `launch` must never run."""

    def launch(self) -> None:
        raise AssertionError("launch() must not be reached when _start raises")


def _loop_stopped(system: AppSystem) -> bool:
    """Wait for the app loop's thread to leave run_forever (loop.stop is scheduled, not immediate)."""
    loop = system.loop
    assert loop is not None
    return _wait_until(lambda: not loop.is_running())


def test_start_failure_still_tears_down_robot() -> None:
    """A raise while starting — after build_system has entered the robot session — still exits the
    robot's media session and stops the loop, instead of leaking both (the UI is never launched)."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine(raise_on_start="mic unavailable")),
    )
    assert fake_api.entered

    with pytest.raises(RuntimeError, match="mic unavailable"):
        _serve(system, cast(gr.Blocks, _NeverLaunched()))

    assert fake_api.exited
    assert _loop_stopped(system)
    assert system.wica is not None and not system.wica.is_running


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
    config = AppConfig(
        wica=wica,
        asr_engine=cast(ASREngineConfig, None),
        reachy=ReachyMiniConfig(backend="fake"),
    )
    fake_api = FakeReachyMiniApi()
    system = build_system(
        config,
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert system.wica is None
        assert any(
            m.startswith("Agent:") and "no-such-prompt.md" in m for m in system.missing
        )
        assert system.voice is not None  # the robot side is unaffected
    finally:
        _shutdown(system)
    assert fake_api.exited


def test_shutdown_isolates_a_failing_step() -> None:
    """An ASR stop that raises is logged and skipped past: the robot still exits, the loop stops."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(
            ASREngine, FakeASREngine(raise_on_stop="provider socket broke")
        ),
    )
    _start(system)

    _shutdown(system)  # must not raise

    assert fake_api.exited
    assert _loop_stopped(system)


def test_shutdown_bounds_a_hung_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ASR stop that never returns is abandoned after the per-step timeout; teardown continues."""
    monkeypatch.setattr(app, "_SHUTDOWN_TIMEOUT_S", 0.1)
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine(hang_on_stop=True)),
    )
    _start(system)

    _shutdown(system)

    assert fake_api.exited
    assert _loop_stopped(system)


def test_shutdown_skips_never_started_bridge() -> None:
    """A bridge that was built but never started (main never ran _start) is not stopped at teardown —
    the running-guard holds — while the robot session is still closed."""
    log: list[str] = []
    fake_api = FakeReachyMiniApi(log=log)
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine(log=log)),
    )

    _shutdown(system)

    assert log == ["api.exit"]  # no "asr.stop"
    assert _loop_stopped(system)


def test_main_requires_a_config_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare run (no --config) exits with usage guidance before building anything — it never falls
    back to a default file (the committed example is a template to copy and adapt, not one meant to
    run unmodified)."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-interaction-wica"])
    built: list[AppConfig] = []
    monkeypatch.setattr(app, "build_system", lambda config, **_kw: built.append(config))

    with pytest.raises(SystemExit) as exc:
        app.main()

    assert exc.value.code == 2
    assert "--config" in capsys.readouterr().err
    assert built == []  # no robot was ever constructed


# --- Vision in: the hand-sign input (specs/gesture-input.md, "App integration") ----------------


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
    fake_api: FakeReachyMiniApi, classifier: _ScriptedClassifier, script: list[dict]
) -> AppSystem:
    return build_system(
        _config(script=script, vision=VisionConfig(enabled=True, fps=100.0)),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
        gesture_models=GestureModels(
            detector=_ScriptedDetector(), classifier=classifier
        ),
    )


def _sign_steps(reactions: ReactionLog, hand: int) -> int:
    """How many reasoning steps a sign on *hand* triggered, read off the reaction labels (each step
    is labelled by its trigger through `display_entry`)."""
    return sum(
        f"✋ hand {hand}:" in label and "no sign" not in label
        for label, _ in reactions.snapshot().choices
    )


def test_a_sign_wakes_the_agent_with_every_hand_in_its_prompt() -> None:
    """The robot camera → pipeline → `hand_gesture_1` → one reasoning step whose prompt names the
    sign that hand shows (and says the other hand shows none); the sign held over many frames wakes
    the agent only once, and it going away does not wake it at all."""
    fake_api = FakeReachyMiniApi()
    classifier = _ScriptedClassifier(fist=0.9)
    system = _vision_system(fake_api, classifier, _say_once("okay, stopping"))
    try:
        assert system.gesture is not None
        assert system.wica is not None
        # One entry per hand the pipeline can publish (`max_hands`, 2 by default).
        assert system.wica.world.is_registered("hand_gesture_1")
        assert system.wica.world.is_registered("hand_gesture_2")
        assert not system.wica.world.is_registered("hand_gesture_3")
        _, reactions = _wire(system)
        _start(system)
        assert _wait_until(lambda: fake_api.said == ["okay, stopping"])
        assert system.wica.world.get("hand_gesture_1") == "stop"
        assert system.wica.world.get("hand_gesture_2") is None
        prompt = reactions.prompt_text(0)
        assert prompt is not None
        assert 'hand 1 is showing the "stop" sign' in prompt
        assert "hand 2 is not showing a sign" in prompt

        # Still shown, many frames later: no second step for it (the `say` finishing re-triggers one
        # step of its own — WICA's completion re-trigger — which is labelled by the say, not a sign).
        polls_then = fake_api.camera_polls
        assert _wait_until(lambda: fake_api.camera_polls >= polls_then + 10)
        assert _sign_steps(reactions, 1) == 1

        # Gone: the entry updates (silently), the agent is not woken.
        classifier.fist = 0.1
        assert _wait_until(lambda: system.wica.world.get("hand_gesture_1") is None)  # type: ignore[union-attr]
        polls_then = fake_api.camera_polls
        assert _wait_until(lambda: fake_api.camera_polls >= polls_then + 10)
        assert _sign_steps(reactions, 1) == 1
        assert not any("no sign" in label for label, _ in reactions.snapshot().choices)
        assert fake_api.said == ["okay, stopping"]
    finally:
        _shutdown(system)


def test_another_sign_wakes_the_agent_again() -> None:
    """A hand switching sign with no sign-less frame between is a second trigger: `sign_shown` fires
    on `stop → five` too, and the new prompt carries the new sign."""
    fake_api = FakeReachyMiniApi()
    classifier = _ScriptedClassifier(fist=0.9)
    # One step per trigger, each speaking: the second is the one the new sign wakes.
    script = [
        {"tool_calls": [{"name": "say", "args": {"text": "okay, stopping"}}]},
        {"tool_calls": [{"name": "say", "args": {"text": "five it is"}}]},
    ]
    system = _vision_system(fake_api, classifier, script)
    try:
        assert system.wica is not None
        _, reactions = _wire(system)
        _start(system)
        assert _wait_until(lambda: fake_api.said == ["okay, stopping"])
        classifier.label = "palm"  # stop → five, no gap
        assert _wait_until(
            lambda: system.wica.world.get("hand_gesture_1") == "five"  # type: ignore[union-attr]
        )
        assert _wait_until(lambda: fake_api.said == ["okay, stopping", "five it is"])
        assert system.wica.world.get("hand_gesture_1") == "five"
        assert _sign_steps(reactions, 1) == 2
    finally:
        _shutdown(system)


def test_vision_is_off_without_a_config_section() -> None:
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert not any(m.startswith("Vision:") for m in system.missing)
    finally:
        _shutdown(system)


def test_vision_section_present_but_disabled_builds_nothing() -> None:
    """`enabled: false` (the default) with the robot up: no gesture input, no `hand_gesture_<n>`
    entry — the section can sit in the file, tuned, without vision running."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=[], vision=VisionConfig(fps=100.0)),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert not any(m.startswith("Vision:") for m in system.missing)
    finally:
        _shutdown(system)


def test_vision_max_hands_reaches_the_gesture_input() -> None:
    """The configured hand cap is what the pipeline runs with, not the library's default."""
    system = build_system(
        _config(script=[], vision=VisionConfig(enabled=True, max_hands=1)),
        api=cast(ReachyMiniApi, FakeReachyMiniApi()),
        asr_engine=cast(ASREngine, FakeASREngine()),
        gesture_models=GestureModels(
            detector=_ScriptedDetector(), classifier=_ScriptedClassifier(fist=0.9)
        ),
    )
    try:
        assert system.gesture is not None
        assert system.gesture.status()["max_hands"] == 1
    finally:
        _shutdown(system)


def test_vision_needs_the_robot() -> None:
    """No robot ⇒ no camera ⇒ no gesture input and no `hand_gesture_<n>` entry (the agent is never
    told about hands it cannot see); the banner carries only the robot line."""
    system = build_system(
        _config(script=[], vision=VisionConfig(enabled=True)),
        api=cast(ReachyMiniApi, FakeReachyMiniApi(raise_on_enter="no robot")),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert system.gesture is None
        assert system.wica is not None
        assert not system.wica.world.is_registered("hand_gesture_1")
        assert [m for m in system.missing if m.startswith("Vision:")] == []
    finally:
        _shutdown(system)


def test_shutdown_stops_vision_before_the_robot_exits() -> None:
    fake_api = FakeReachyMiniApi()
    system = _vision_system(fake_api, _ScriptedClassifier(fist=0.1), [])
    try:
        _wire(system)
        _start(system)
        assert system.gesture is not None and system.gesture.running
        assert _wait_until(lambda: fake_api.camera_polls > 0)
    finally:
        _shutdown(system)
    assert system.gesture is not None and not system.gesture.running
    assert fake_api.exited
    polls_then = fake_api.camera_polls
    time.sleep(0.05)
    assert fake_api.camera_polls == polls_then  # nothing polls the exited robot
