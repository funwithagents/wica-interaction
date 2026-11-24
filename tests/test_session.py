"""A session that starts and stops again in one process (specs/session.md).

Drives `Session` the way the page does — Start from the saved config of an app's folder, Stop —
over real sessions: `system.open_session` with the fake embodiment, the fake ASR engine and WICA's
scripted provider, the config saved in a temporary folder through app-setup's `AppModel`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
import wica.setup
from app_setup.app import App, AppModel, Section
from app_setup.folder import ConfigFolder

from tests.fakes import FakeASREngine, FakeEmbodiment, asr_factory, scripted_wica_config
from wica_interaction.config import AppConfig
from wica_interaction.session import Running, Session
from wica_interaction.system import open_session

_ASR = {"module": {"type": "deepgram-v2", "api_key_env": "DEEPGRAM_API_KEY"}}


def _saved(persona: str = "You are a test double.") -> dict[str, Any]:
    return {
        "wica": {
            "agent": {"provider": "fake", "model": "scripted", "system_prompt": persona}
        }
    }


class _Loaded:
    def __init__(self, app: AppConfig) -> None:
        self.app = app


class _Harness:
    """An app with one `wica` section, its folder, and a session over the fake body: each Start
    builds a new `FakeEmbodiment` whose voice answers every turn with *reply*."""

    def __init__(self, folder: Path, saved: dict[str, Any], reply: str = "Hi.") -> None:
        app = App(
            name="wica-interaction-test",
            host="wica-interaction",
            sections=(Section("wica", wica.setup),),
            default_config=saved,
        )
        self.reply = reply
        self.bodies: list[FakeEmbodiment] = []
        self.personas: list[str] = []
        self.session = Session(self._open)
        self.model = AppModel(
            app, ConfigFolder(folder, saved), locked=self.session.locked
        )

    def _load(self, data: Any, *, base_dir: Path) -> _Loaded:
        from asr_engine.config import ASREngineConfig

        persona = data["wica"]["agent"]["system_prompt"]
        self.personas.append(persona)
        script = [{"text": f"<say>{self.reply}</say>"} for _ in range(50)]
        return _Loaded(
            AppConfig(
                wica=scripted_wica_config(script, persona=persona),
                asr_engine=ASREngineConfig.from_dict(_ASR),
            )
        )

    def _body(self, _loaded: Any) -> FakeEmbodiment:
        body = FakeEmbodiment()
        self.bodies.append(body)
        return body

    def _open(self, used_config: Any, folder: Path) -> Running:
        return open_session(
            used_config,
            folder,
            load=self._load,
            embodiment=self._body,
            asr_engine_factory=asr_factory(FakeASREngine()),
        )


def _wait_until(predicate: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_a_start_with_a_blocker_builds_nothing_and_says_why(tmp_path: Path) -> None:
    """A key the saved config needs and the machine lacks is a blocker: nothing is built, and the
    reason stays on the session for every browser tab until a Start runs."""
    saved = {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-haiku-5-5",
                "api_key_env": "WICA_SESSION_TEST_UNSET_KEY",
                "system_prompt": "You are a test double.",
            }
        }
    }
    harness = _Harness(tmp_path / "app", saved)

    reasons = harness.session.start(harness.model)

    assert any("WICA_SESSION_TEST_UNSET_KEY" in r for r in reasons)
    assert harness.bodies == []
    assert harness.session.status == "stopped" and not harness.session.locked()
    assert harness.session.reasons == reasons


def test_start_then_stop(tmp_path: Path) -> None:
    harness = _Harness(tmp_path / "app", _saved())
    session = harness.session
    assert session.status == "stopped" and session.version == 0

    assert session.start(harness.model) == []
    running = session.current
    assert running is not None and session.status == "running"
    assert session.locked() and session.version == 1
    [body] = harness.bodies
    assert body.entered

    session.stop()

    assert session.status == "stopped" and not session.locked()
    assert session.current is None and session.version == 2
    assert body.exited
    assert running.system.loop is not None and running.system.loop.is_closed()


def test_the_config_is_locked_while_a_session_runs(tmp_path: Path) -> None:
    """Save is refused while running, accepted once stopped; the next session runs what was saved."""
    harness = _Harness(tmp_path / "app", _saved("First persona."))
    harness.session.start(harness.model)

    with pytest.raises(RuntimeError):
        harness.model.save(_saved("Second persona."))

    harness.session.stop()
    harness.model.save(_saved("Second persona."))
    harness.session.start(harness.model)
    harness.session.stop()

    assert harness.personas == ["First persona.", "Second persona."]


def test_a_build_that_raises_leaves_the_session_stopped_with_one_reason(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path / "app", _saved())

    def broken(*_args: Any) -> Running:
        raise ValueError("config: vision 'fps' must be a finite number > 0")

    session = Session(broken)
    reasons = session.start(harness.model)

    assert session.status == "stopped" and not session.locked()
    assert reasons == [
        "The session could not start: config: vision 'fps' must be a finite number > 0"
    ]


def test_a_second_start_and_a_stray_stop_do_nothing(tmp_path: Path) -> None:
    harness = _Harness(tmp_path / "app", _saved())
    harness.session.stop()  # nothing runs: nothing happens
    assert harness.session.version == 0

    harness.session.start(harness.model)
    assert harness.session.start(harness.model) == []
    assert len(harness.bodies) == 1
    harness.session.stop()


def test_ten_sessions_leave_nothing_behind(tmp_path: Path) -> None:
    """Start, one answered turn, Stop — ten times: each session answers, and afterwards the process
    has the threads it had before the first (every session's loop thread joined, its body closed)."""
    harness = _Harness(tmp_path / "app", _saved())
    before = set(threading.enumerate())

    for cycle in range(10):
        assert harness.session.start(harness.model) == [], cycle
        running = harness.session.current
        assert running is not None
        running.system.submit_speech(f"turn {cycle}")
        body = harness.bodies[-1]
        assert _wait_until(lambda body=body: body.said == ["Hi."]), cycle
        harness.session.stop()
        assert body.exited

    assert len(harness.bodies) == 10
    assert _wait_until(lambda: set(threading.enumerate()) <= before), [
        t.name for t in set(threading.enumerate()) - before
    ]
