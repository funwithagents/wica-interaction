"""Functional tests for the app wiring (`build_system`).

Deterministic, no browser/mic/network: the agent runs against WICA's `fake` provider (a scripted
model), the TTS/ASR engines are injected fakes, and only the wiring `main` does — minus `.launch()` —
is exercised. Covers the two input paths (typed text and a closed ASR segment) reaching the agent and
its `say` reaching TTS, plus the no-credentials degrade path at the engine build seams.
"""

from __future__ import annotations

import time
from typing import Any, cast

from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine
from wica.config import WicaConfig

from reachy_mini_interaction_wica.app import build_system
from reachy_mini_interaction_wica.config import AppConfig


class FakeTTSEngine:
    """Records each `say`; the only method `TTSTools` (inside VoiceOutput) calls."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:
        self.said.append(text)


class FakeASREngine:
    """The slice of `ASREngine` that `AsrBridge` and `build_system` touch — construction needs no key.

    `AsrBridge.__init__` assigns `on_speech_segment`; `start`/`stop`/`start_dictation`/`stop_dictation`
    and the `status()`/`dictating`/`segmentation_mode` accessors back the bridge's own surface.
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


def _scripted_config(say_text: str) -> AppConfig:
    """An `AppConfig` whose WICA section is the `fake` provider scripted to speak `say_text` once.

    The engine sections are unused here (tests inject fake engines), so they are `None` behind a cast.
    """
    wica = WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt": "You are a test double.",
                "model_kwargs": {
                    "delay_s": 0,
                    "script": [
                        {"tool_calls": [{"name": "say", "args": {"text": say_text}}]},
                        {"tool_calls": [{"name": "noop", "args": {}}]},
                    ],
                },
            }
        }
    )
    return AppConfig(
        wica=wica,
        tts_engine=cast(TTSEngineConfig, None),
        asr_engine=cast(ASREngineConfig, None),
    )


def _wait_until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_typed_text_drives_agent_and_reaches_tts() -> None:
    """A typed turn becomes `speech_input`, the agent reasons, its `say` command plays through TTS."""
    fake_tts = FakeTTSEngine()
    system = build_system(
        _scripted_config("hello there"),
        tts_engine=cast(TTSEngine, fake_tts),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    assert system.wica is not None
    system.wica.start()
    try:
        # The typed-text box and the ASR bridge share this one World update.
        system.submit_speech("hi")
        assert _wait_until(lambda: fake_tts.said == ["hello there"])
    finally:
        system.wica.close()


def test_empty_typed_text_is_ignored() -> None:
    """Whitespace-only input never wakes the agent, so nothing is spoken."""
    fake_tts = FakeTTSEngine()
    system = build_system(
        _scripted_config("should not be said"),
        tts_engine=cast(TTSEngine, fake_tts),
        asr_engine=cast(ASREngine, FakeASREngine()),
    )
    assert system.wica is not None
    system.wica.start()
    try:
        # Whitespace-only and empty input are both ignored, so the agent never wakes.
        system.submit_speech("   ")
        system.submit_speech("")
        # Give any (erroneous) reasoning step a chance to run before asserting nothing happened.
        time.sleep(0.2)
        assert fake_tts.said == []
    finally:
        system.wica.close()


def test_closed_segment_reaches_agent_like_typed_text() -> None:
    """A closed ASR segment routed through the bridge callback wakes the agent identically."""
    fake_tts = FakeTTSEngine()
    fake_asr = FakeASREngine()
    system = build_system(
        _scripted_config("segment reply"),
        tts_engine=cast(TTSEngine, fake_tts),
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
        assert _wait_until(lambda: fake_tts.said == ["segment reply"])
    finally:
        system.wica.close()


def _degrade_config() -> AppConfig:
    """Real TTS/ASR configs referencing unset env vars (so their engine build raises), with a fake
    WICA agent that builds fine — the app should still open with both engines reported missing."""
    wica = WicaConfig.from_dict(
        {"agent": {"provider": "fake", "model": "scripted", "system_prompt": "double"}}
    )
    tts = TTSEngineConfig.from_dict(
        {
            "module": {
                "type": "elevenlabs",
                "api_key_env": "WICA_TEST_UNSET_TTS_KEY",
                "voice_id": "x",
            }
        }
    )
    asr = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram_v2", "api_key_env": "WICA_TEST_UNSET_ASR_KEY"}}
    )
    return AppConfig(wica=wica, tts_engine=tts, asr_engine=asr)


def test_missing_engine_keys_degrade_without_raising(monkeypatch: Any) -> None:
    """Unset TTS/ASR keys are captured (naming the env var), the agent still builds, typed text works."""
    monkeypatch.delenv("WICA_TEST_UNSET_TTS_KEY", raising=False)
    monkeypatch.delenv("WICA_TEST_UNSET_ASR_KEY", raising=False)

    system = build_system(_degrade_config())  # real engine builds, no fakes injected
    try:
        assert system.voice is None
        assert system.bridge is None
        assert system.can_reason  # the fake agent built fine
        # Each missing engine reported its unset env var by name.
        blob = " ".join(system.missing)
        assert "WICA_TEST_UNSET_TTS_KEY" in blob
        assert "WICA_TEST_UNSET_ASR_KEY" in blob
        # And the agent still runs: typed text with no ASR still updates speech_input without error.
        assert system.wica is not None
        system.wica.start()
        system.submit_speech("still works")
    finally:
        if system.wica is not None:
            system.wica.close()
