# Live tier shared fixtures.
#
# This tier is NOT collected by the default `uv run pytest` (testpaths = ["tests"]);
# run it explicitly with `uv run pytest tests-e2e`. Mirror any isolation fixture the
# fast tier uses here — tests-e2e/ isn't a package that can import from tests/, so the
# few lines are duplicated rather than shared.
#
# The end-to-end fixtures (specs/e2e.md, "Layout and fixtures"): the devices the run names, the
# person, and the real system of each embodiment, built from its committed config with the run's
# values filled in, started, and torn down whatever happens.

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from person import FileCamera, Person
from support import E2E, InputLog, require_device, require_env
from wica.contrib.gradio import TranscriptLog
from wica.testing import TestRun

from wica_interaction.app_ui import display_entry
from wica_interaction.computer.config import load_config as load_computer_config
from wica_interaction.computer.embodiment import ComputerEmbodiment
from wica_interaction.system import _shutdown, _start, build_system, wire

CONFIGS = Path(__file__).resolve().parent / "configs"

# The app's inputs a scenario reads back: speech, and the hands (`vision.max_hands` is 2).
INPUT_KEYS = ("speech_input", "hand_gesture_1", "hand_gesture_2")


@dataclass(frozen=True)
class Devices:
    """The audio devices the run names (specs/e2e.md, "The virtual audio cable")."""

    mic: str
    person_voice: str
    speaker: str | None


@pytest.fixture(scope="session")
def devices() -> Devices:
    mic = require_device(require_env("WICA_E2E_MIC_DEVICE"), "input")
    person_voice = require_device(
        os.environ.get("WICA_E2E_PERSON_VOICE_DEVICE") or mic, "output"
    )
    speaker = os.environ.get("WICA_E2E_SPEAKER_DEVICE") or None
    if speaker is not None:
        require_device(speaker, "output")
    return Devices(mic=mic, person_voice=person_voice, speaker=speaker)


@pytest.fixture(scope="session")
def keys() -> None:
    require_env("WICA_ANTHROPIC_API_KEY")
    require_env("DEEPGRAM_API_KEY")


def _fill(name: str, values: dict[str, Any], out: Path) -> Path:
    """The committed config *name* with the run's *values* set (dotted paths) and its persona path
    made absolute, written to *out*."""
    path = CONFIGS / name
    data = json.loads(path.read_text())
    agent = data["wica"]["agent"]
    agent["system_prompt_file"] = str(
        (path.parent / agent["system_prompt_file"]).resolve()
    )
    for dotted, value in values.items():
        *parents, leaf = dotted.split(".")
        node = data
        for part in parents:
            node = node[part]
        node[leaf] = value
    out.write_text(json.dumps(data, indent=2))
    return out


def _evidence(e2e: E2E, out: Path) -> None:
    """What a failed scenario is read from (specs/e2e.md, "Evidence on failure")."""
    report = e2e.run.report()
    report.write(out / "report.json")
    report.write(out / "report.md")
    delivered = [
        {"t": d.t, "key": d.key, "value": repr(d.value)} for d in e2e.inputs.delivered()
    ]
    (out / "inputs.json").write_text(json.dumps(delivered, indent=2))


@pytest.fixture
def computer(devices: Devices, keys: None, tmp_path: Path) -> Iterator[E2E]:
    """The computer embodiment's real system: the cable as its mic, an image file as its webcam,
    started, with the person in front of it."""
    camera = FileCamera(tmp_path / "camera.jpg")
    config_path = _fill(
        "computer.json",
        {
            "asr.engine.audio.device": devices.mic,
            "tts.engine.player.device": devices.speaker,
            "camera.device": str(camera.path),
        },
        tmp_path / "config.json",
    )
    config = load_computer_config(str(config_path))
    system = build_system(config.app, ComputerEmbodiment(config))
    person: Person | None = None
    run: TestRun | None = None
    try:
        assert not system.missing, system.missing
        assert system.wica is not None
        wire(system, TranscriptLog(system.wica, display_entry))
        run = TestRun(system.wica, scenario="computer")
        inputs = InputLog(system.wica.world, INPUT_KEYS)
        person = Person(devices.person_voice, camera)
        _start(system)
        e2e = E2E(system=system, run=run, inputs=inputs, person=person)
        try:
            yield e2e
        finally:
            _evidence(e2e, tmp_path)
    finally:
        if run is not None:
            run.close()
        if person is not None:
            person.close()
        _shutdown(system)
