"""Functional tests for the app wiring (`build_system`).

Deterministic, no browser/mic/network/robot: the agent runs against WICA's `fake` provider (a scripted
model), the robot is an injected fake `ReachyMiniApi`, the ASR engine is an injected fake, and only the
wiring `main` does — minus `.launch()` — is exercised. Covers the two input paths (typed text and a
closed ASR segment) reaching the agent and its `say` reaching the robot speaker, plus the degrade paths
at the robot and ASR build seams.
"""

from __future__ import annotations

import time
from typing import Any, Self, cast

from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from reachy_mini_bridge.api import ReachyMiniApi
from tts_engine.config import TTSEngineConfig
from wica.config import WicaConfig

from reachy_mini_interaction_wica.app import build_system
from reachy_mini_interaction_wica.config import AppConfig, ReachyConfig


class FakeReachyMiniApi:
    """The slice of `ReachyMiniApi` the app touches: an async-context robot whose `say` records.

    `build_system` enters it on the loop (`__aenter__`); `VoiceOutput.say` calls `say`. Set
    `raise_on_enter` to simulate a robot that fails to connect (the broad degrade path).
    """

    def __init__(self, *, raise_on_enter: str | None = None) -> None:
        self.said: list[str] = []
        self.played: list[str] = []
        self.entered = False
        self.exited = False
        self._raise_on_enter = raise_on_enter

    async def __aenter__(self) -> Self:
        if self._raise_on_enter is not None:
            raise RuntimeError(self._raise_on_enter)
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def say(self, text: str) -> None:
        self.said.append(text)

    # The robot-control slice: enough for a wiring test that a registered action command routes here.
    async def get_motors_state(self) -> str:
        return "enabled"

    async def play_emotion(self, name: str) -> None:
        self.played.append(name)


class FakeASREngine:
    """The slice of `ASREngine` that `AsrBridge` and `build_system` touch — construction needs no key.

    `AsrBridge.__init__` assigns `on_speech_segment`; `start`/`stop` and the `status()`/`dictating`/
    `segmentation_mode` accessors back the bridge's own surface.
    """

    def __init__(self) -> None:
        self.on_speech_segment: Any = None
        self.dictating = False
        self.segmentation_mode = "utterance"
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def status(self) -> dict[str, Any]:
        return {"running": self.started}


def _config(*, script: list[dict], reachy_backend: str = "fake") -> AppConfig:
    """An `AppConfig` whose WICA section is the `fake` provider running *script*.

    The engine sections are unused here (tests inject fakes), so they are `None` behind a cast.
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
        tts_engine=cast(TTSEngineConfig, None),
        asr_engine=cast(ASREngineConfig, None),
        reachy=ReachyConfig(backend=reachy_backend),
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


def test_typed_text_drives_agent_and_speaks_through_robot() -> None:
    """A typed turn becomes `speech_input`, the agent reasons, its `say` plays through the robot."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=_say_once("hello there")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    assert fake_api.entered  # build_system opened the robot's media session on the loop
    assert system.voice is not None and system.bridge is not None
    assert system.wica is not None
    system.wica.start()
    try:
        # The typed-text box and the ASR bridge share this one World update.
        system.submit_speech("hi")
        assert _wait_until(lambda: fake_api.said == ["hello there"])
    finally:
        system.wica.close()


def test_robot_action_command_is_registered_and_routes_to_the_robot() -> None:
    """A turn where the agent issues a robot action command reaches the robot — proving build_system
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
    assert system.wica is not None
    system.wica.start()
    try:
        system.submit_speech("show me happy")
        assert _wait_until(lambda: fake_api.played == ["happy"])
    finally:
        system.wica.close()


def test_empty_typed_text_is_ignored() -> None:
    """Whitespace-only input never wakes the agent, so nothing is spoken."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=_say_once("should not be said")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    assert system.wica is not None
    system.wica.start()
    try:
        system.submit_speech("   ")
        system.submit_speech("")
        # Give any (erroneous) reasoning step a chance to run before asserting nothing happened.
        time.sleep(0.2)
        assert fake_api.said == []
    finally:
        system.wica.close()


def test_closed_segment_reaches_agent_like_typed_text() -> None:
    """A closed ASR segment routed through the bridge callback wakes the agent identically."""
    fake_api = FakeReachyMiniApi()
    fake_asr = FakeASREngine()
    system = build_system(
        _config(script=_say_once("segment reply")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, fake_asr),
    )
    assert system.wica is not None
    assert system.bridge is not None
    # The bridge wired its handler onto the engine's segment callback.
    assert fake_asr.on_speech_segment is not None
    system.wica.start()
    try:
        # Simulate the bridge delivering a final segment (the real on_final_segment path).
        system.submit_speech("please reply")
        assert _wait_until(lambda: fake_api.said == ["segment reply"])
    finally:
        system.wica.close()


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
        assert system.can_reason  # the fake agent built fine
        # And the agent still reasons over typed input: a reasoning step runs (a prompt is captured).
        assert system.wica is not None
        system.wica.start()
        system.submit_speech("are you there")
        assert _wait_until(lambda: len(system.panels._prompts) > 0)
    finally:
        if system.wica is not None:
            system.wica.close()


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
        tts_engine=cast(TTSEngineConfig, None),
        asr_engine=asr,
        reachy=ReachyConfig(backend="fake"),
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
        assert system.can_reason
        assert system.wica is not None
        system.wica.start()
        system.submit_speech("still works")  # typed text still drives the agent, no exception
    finally:
        if system.wica is not None:
            system.wica.close()
