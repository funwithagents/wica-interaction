"""Functional tests for the app wiring (`build_system`).

Deterministic, no browser/mic/network/robot: the agent runs against WICA's `fake` provider (a scripted
model), the robot is an injected fake `ReachyMiniApi`, the ASR engine is an injected fake, and only the
wiring `main` does — minus `.launch()` — is exercised. Covers the two input paths (typed text and a
closed ASR segment) reaching the agent and its `say` reaching the robot speaker, plus the degrade paths
at the robot and ASR build seams.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Self, cast

import pytest
from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment
from reachy_mini_bridge.api import ReachyMiniApi
from tts_engine.config import TTSEngineConfig
from wica.config import WicaConfig

from reachy_mini_interaction_wica import app
from reachy_mini_interaction_wica.app import (
    AppSystem,
    _serve,
    _shutdown,
    _start,
    build_system,
)
from reachy_mini_interaction_wica.config import AppConfig, Backend, ReachyConfig


class FakeReachyMiniApi:
    """The slice of `ReachyMiniApi` the app touches: an async-context robot whose `say` records.

    `build_system` enters it on the loop (`__aenter__`); `VoiceOutput.say` calls `say`. Set
    `raise_on_enter` to simulate a robot that fails to connect (the broad degrade path). Pass `log`
    (shared with `FakeASREngine`) to record teardown steps in order.
    """

    def __init__(self, *, raise_on_enter: str | None = None, log: list[str] | None = None) -> None:
        self.said: list[str] = []
        self.played: list[str] = []
        self.entered = False
        self.exited = False
        self._raise_on_enter = raise_on_enter
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

    # The robot-control slice: enough for a wiring test that a registered action command routes here.
    async def get_motors_state(self) -> str:
        return "enabled"

    async def play_emotion(self, name: str) -> None:
        self.played.append(name)


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


def _config(*, script: list[dict], reachy_backend: Backend = "fake") -> AppConfig:
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


def _prompt_captured(system: AppSystem) -> bool:
    """Whether a reasoning step sent a prompt, read off the public Prompt-selector view the UI polls
    (`tick_conversation` pushes the selector's choices only when a new step's prompt arrived)."""
    _, selector, _ = system.panels.tick_conversation()
    return bool(selector.get("choices"))


def test_typed_text_drives_agent_and_speaks_through_robot() -> None:
    """A typed turn becomes `speech_input`, the agent reasons, its `say` plays through the robot."""
    fake_api = FakeReachyMiniApi()
    system = build_system(
        _config(script=_say_once("hello there")),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    try:
        assert fake_api.entered  # build_system opened the robot's media session on the loop
        assert system.voice is not None and system.bridge is not None
        _start(system)
        # The typed-text box writes the same World update a closed ASR segment makes.
        system.submit_speech("hi")
        assert _wait_until(lambda: fake_api.said == ["hello there"])
    finally:
        _shutdown(system)


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
    try:
        _start(system)
        system.submit_speech("show me happy")
        assert _wait_until(lambda: fake_api.played == ["happy"])
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
        _start(system)
        system.submit_speech("   ")
        system.submit_speech("")
        system.submit_speech("a real turn")
        assert _wait_until(lambda: fake_api.said == ["real reply"])
        # Any trigger (an erroneous empty one included) is recorded before its step's reply, so the
        # transcript is complete for inputs by now.
        chatbot, _, _ = system.panels.tick_conversation()
        assert isinstance(chatbot, list)
        assert [m["content"] for m in chatbot if m["role"] == "user"] == ['🗣️ "a real turn"']
    finally:
        _shutdown(system)


def _final_segment(transcript: str) -> SpeechSegment:
    return SpeechSegment(
        transcript=transcript, is_final=True, end_reason="utterance", utterances=[]
    )


def _emit_segment(system: AppSystem, fake_asr: FakeASREngine, segment: SpeechSegment) -> None:
    """Emit *segment* the way the real ASREngine does: await its `on_speech_segment` callback (which
    the bridge wired to itself) on the app loop."""
    assert system.loop is not None
    assert fake_asr.on_speech_segment is not None  # the bridge claimed the engine's callback
    asyncio.run_coroutine_threadsafe(fake_asr.on_speech_segment(segment), system.loop).result(2.0)


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
        _start(system)
        system.submit_speech("are you there")
        assert _wait_until(lambda: _prompt_captured(system))
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
        assert system.wica is not None
        _start(system)
        system.submit_speech("still works")  # typed text still drives the agent
        assert _wait_until(lambda: _prompt_captured(system))
    finally:
        _shutdown(system)


def _tts_degrade_config() -> AppConfig:
    """A real TTS config referencing an unset env var (so the synthesizer build raises), with a fake
    WICA agent and the bridge's `fake` robot backend — only the voice should degrade."""
    wica = WicaConfig.from_dict(
        {"agent": {"provider": "fake", "model": "scripted", "system_prompt": "double"}}
    )
    tts = TTSEngineConfig.from_dict(
        {"module": {"type": "elevenlabs", "api_key_env": "WICA_TEST_UNSET_TTS_KEY"}}
    )
    return AppConfig(
        wica=wica,
        tts_engine=tts,
        asr_engine=cast(ASREngineConfig, None),  # a fake engine is injected
        reachy=ReachyConfig(backend="fake"),
    )


def test_missing_tts_key_degrades_only_the_voice(monkeypatch: Any) -> None:
    """With the TTS key unset, the robot still comes up (mic/ASR/actions intact) and `say` reports a
    readable TTS error instead of the whole robot being reported missing."""
    monkeypatch.delenv("WICA_TEST_UNSET_TTS_KEY", raising=False)
    fake_asr = FakeASREngine()
    # No `api=` injected: build_system builds the real fake-backend api with a synthesizer of None.
    system = build_system(_tts_degrade_config(), asr_engine=cast(ASREngine, fake_asr))
    try:
        assert system.api is not None  # the robot is up …
        assert system.voice is not None  # … so the voice exists (say is registered) …
        assert system.bridge is not None  # … and so does the mic-fed ASR bridge
        # The failure is reported on the TTS axis, naming the env var — not as a robot failure.
        assert any(m.startswith("TTS:") and "WICA_TEST_UNSET_TTS_KEY" in m for m in system.missing)
        assert not any(m.startswith("Robot:") for m in system.missing)
        # say degrades to a readable string on the app loop (VoiceOutput maps the BridgeError).
        assert system.loop is not None
        result = asyncio.run_coroutine_threadsafe(system.voice.say("hi"), system.loop).result(2.0)
        assert result.startswith("TTS error:")
        # And the agent still reasons over typed input despite the voice being degraded.
        assert system.wica is not None
        _start(system)
        system.submit_speech("are you there")
        assert _wait_until(lambda: _prompt_captured(system))
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
        _serve(system)

    assert fake_api.exited
    assert _loop_stopped(system)
    assert system.wica is not None and not system.wica.is_running


def test_bad_system_prompt_file_degrades(tmp_path: Path) -> None:
    """A `system_prompt_file` that doesn't exist lands in the banner as an `Agent:` line (WICA raises
    ConfigError from Wica.init) rather than crashing; the rest of the system still builds."""
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
        tts_engine=cast(TTSEngineConfig, None),
        asr_engine=cast(ASREngineConfig, None),
        reachy=ReachyConfig(backend="fake"),
    )
    fake_api = FakeReachyMiniApi()
    system = build_system(
        config, api=cast(ReachyMiniApi, fake_api), asr_engine=cast(ASREngine, FakeASREngine())
    )
    try:
        assert system.wica is None
        assert any(m.startswith("Agent:") and "no-such-prompt.md" in m for m in system.missing)
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
        asr_engine=cast(ASREngine, FakeASREngine(raise_on_stop="provider socket broke")),
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
    back to a default file (the committed example targets a real robot)."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-interaction-wica"])
    built: list[AppConfig] = []
    monkeypatch.setattr(app, "build_system", lambda config, **_kw: built.append(config))

    with pytest.raises(SystemExit) as exc:
        app.main()

    assert exc.value.code == 2
    assert "--config" in capsys.readouterr().err
    assert built == []  # no robot was ever constructed
