# Live tier shared fixtures.
#
# This tier is NOT collected by the default `uv run pytest` (testpaths = ["tests"]);
# run it explicitly with `uv run pytest tests-e2e`. Mirror any isolation fixture the
# fast tier uses here — tests-e2e/ isn't a package that can import from tests/, so the
# few lines are duplicated rather than shared.
#
# The end-to-end fixtures (specs/e2e.md, "Layout and fixtures"): the devices the run names, the
# person, and the real system of each embodiment, built from its committed config with the run's
# values filled in, started, and torn down whatever happens. The robot's run over the sim the
# bridge's harness spawns, through the harness's pytest plugin.

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from person import FIXTURES, HAND_PHOTOS, FileCamera, Person, SceneStage, Stage
from support import (
    E2E,
    InputLog,
    echo_device,
    missing,
    require_device,
    require_env,
)
from wica.contrib.gradio import TranscriptLog
from wica.testing import TestRun

from wica_interaction.app_ui import display_entry
from wica_interaction.computer.config import load_config as load_computer_config
from wica_interaction.computer.embodiment import ComputerEmbodiment
from wica_interaction.gesture_input import preload_mediapipe
from wica_interaction.reachy.config import load_config as load_reachy_config
from wica_interaction.reachy.embodiment import ReachyEmbodiment
from wica_interaction.system import AppSystem, _shutdown, _start, build_system, wire

CONFIGS = Path(__file__).resolve().parent / "configs"

# The app's inputs a scenario reads back: speech, and the hands (`vision.max_hands` is 2).
INPUT_KEYS = ("speech_input", "hand_gesture_1", "hand_gesture_2")

# Each photo's prop is 0.36 m wide (0.24 m tall): the size the hand-render spike validated.
HAND_WIDTH_M = 0.36

# Whether the robot's voice comes back on the cable (specs/e2e.md, "The Reachy Mini embodiment"):
# on Linux, with an echo device, the sim's daemon opens the default input with its software
# canceller on. Set by `pytest_configure`, before the harness spawns the daemon.
ROBOT_ECHO = False


def pytest_configure(config: pytest.Config) -> None:
    """Give the sim the cable before the harness spawns it, then register the harness's plugin.

    The tier names its devices once (`WICA_E2E_*_DEVICE`); the bridge's harness reads its own
    variables when it spawns the sim's daemon, so they are copied across here, unless the run set
    them itself. A conftest below the rootdir cannot declare `pytest_plugins`, hence the import."""
    global ROBOT_ECHO
    mic = os.environ.get("WICA_E2E_MIC_DEVICE")
    speaker = os.environ.get("WICA_E2E_SPEAKER_DEVICE") or None
    # Upstream turns the daemon's echo canceller on only over the default input, never a named one:
    # with the robot's echo, the mic stays unnamed and the run makes the cable the default input.
    ROBOT_ECHO = (
        sys.platform == "linux" and bool(mic) and echo_device(speaker) is not None
    )
    if mic and not ROBOT_ECHO:
        os.environ.setdefault("REACHY_MINI_E2E_AUDIO_INPUT", mic)
    if speaker:
        os.environ.setdefault("REACHY_MINI_E2E_AUDIO_OUTPUT", speaker)
    config.pluginmanager.import_plugin("reachy_mini_bridge.testing.fixtures")
    # The app imports MediaPipe before entering the robot's body, because once GStreamer media runs
    # in a process MediaPipe's import aborts it on Linux (`preload_mediapipe`). In this process the
    # harness's own bridge sessions (its capability probe) run before any app is built, so the
    # import comes first here too.
    preload_mediapipe()


def pytest_reachy_mini_scene_props() -> tuple[Any, ...]:
    """The hand photos as props of the sim's scene: `hand_1` … `hand_5`, in `HAND_PHOTOS`' order,
    hidden until shown. Props must be PNG, so the photos are converted once per run."""
    import cv2
    from reachy_mini_bridge.testing.sim_scene import image_pool

    out = Path(tempfile.gettempdir()) / "wica-e2e-props"
    out.mkdir(exist_ok=True)
    pngs: list[Path] = []
    for photo in HAND_PHOTOS:
        png = out / f"{photo}.png"
        if not png.exists():
            image = cv2.imread(str(FIXTURES / f"{photo}.jpg"))
            if image is None:
                raise pytest.UsageError(f"cannot read the hand photo {photo}.jpg")
            cv2.imwrite(str(png), image)
        pngs.append(png)
    return image_pool("hand", pngs, width=HAND_WIDTH_M)


@dataclass(frozen=True)
class Devices:
    """The audio devices the run names (specs/e2e.md, "The virtual audio cable")."""

    mic: str
    person_voice: str
    speaker: str | None
    echo: str | None


@pytest.fixture(scope="session")
def devices() -> Devices:
    mic = require_device(require_env("WICA_E2E_MIC_DEVICE"), "input")
    person_voice = require_device(
        os.environ.get("WICA_E2E_PERSON_VOICE_DEVICE") or mic, "output"
    )
    speaker = os.environ.get("WICA_E2E_SPEAKER_DEVICE") or None
    if speaker is not None:
        require_device(speaker, "output")
    echo = echo_device(speaker)
    if echo is not None:
        require_device(echo, "input")
    return Devices(mic=mic, person_voice=person_voice, speaker=speaker, echo=echo)


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


def _running(
    system: AppSystem,
    devices: Devices,
    stage: Stage,
    out: Path,
    scenario: str,
    *,
    echo: bool,
) -> Iterator[E2E]:
    """Wire and start a built *system*, yield it with what observes it and the person in front of
    it, then write the evidence and tear it all down, whatever happens."""
    person: Person | None = None
    run: TestRun | None = None
    try:
        assert not system.missing, system.missing
        assert system.wica is not None
        wire(system, TranscriptLog(system.wica, display_entry))
        run = TestRun(system.wica, scenario=scenario)
        inputs = InputLog(system.wica.world, INPUT_KEYS)
        # The person's voice opens the cable on its first `say`, after the scenario has waited
        # for the app to listen (specs/e2e.md, "The virtual audio cable").
        person = Person(devices.person_voice, stage, devices.echo if echo else None)
        _start(system)
        e2e = E2E(system=system, run=run, inputs=inputs, person=person, echo=echo)
        try:
            yield e2e
        finally:
            _evidence(e2e, out)
    finally:
        if run is not None:
            run.close()
        if person is not None:
            person.close()
        _shutdown(system)


def _computer(devices: Devices, out: Path, scenario: str) -> Iterator[E2E]:
    """The computer embodiment's real system: the cable as its mic, an image file as its webcam,
    echo cancellation on when the room plays its voice back, started, with the person in front."""
    camera = FileCamera(out / "camera.jpg")
    config_path = _fill(
        "computer.json",
        {
            "asr.engine.audio.device": devices.mic,
            "tts.engine.player.device": devices.speaker,
            "audio_processing.echo_cancellation.enabled": devices.echo is not None,
            "camera.device": str(camera.path),
        },
        out / "config.json",
    )
    config = load_computer_config(str(config_path))
    system = build_system(config.app, ComputerEmbodiment(config))
    yield from _running(
        system, devices, camera, out, scenario, echo=devices.echo is not None
    )


@pytest.fixture
def computer(devices: Devices, keys: None, tmp_path: Path) -> Iterator[E2E]:
    yield from _computer(devices, tmp_path, "computer")


@pytest.fixture(scope="class")
def computer_conversation(
    devices: Devices, keys: None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[E2E]:
    """One computer app for a whole conversation (specs/e2e.md, "The conversation")."""
    out = tmp_path_factory.mktemp("computer-conversation")
    yield from _computer(devices, out, "computer conversation")


@pytest.fixture(scope="session")
def sim_audio(live_daemon: tuple[str, int], devices: Devices) -> None:
    """The sim's daemon listens on the cable (specs/e2e.md, "The Reachy Mini embodiment"). It
    reports the devices it opened; a daemon borrowed from an earlier run, started without the
    cable, would leave the robot on the real mic and every spoken scenario timing out."""
    host, port = live_daemon
    with urllib.request.urlopen(
        f"http://{host}:{port}/api/bridge/daemon", timeout=10
    ) as answer:
        report = json.load(answer)
    audio = report.get("audio") or {}
    heard = audio.get("input_device")
    # With the robot's echo, the default input, which the run made the cable (`ROBOT_ECHO`).
    expected = None if ROBOT_ECHO else devices.mic
    if heard != expected:
        pytest.fail(
            f"the sim's daemon listens on {heard!r}, not {expected!r}: stop it "
            f"and let the run spawn its own (its report: {report})",
            pytrace=False,
        )


def _reachy(
    devices: Devices,
    live_daemon: tuple[str, int],
    sim_scene: Any,
    out: Path,
    scenario: str,
) -> Iterator[E2E]:
    """The Reachy Mini embodiment's real system over the run's sim: its own bridge on the harness's
    daemon, the cable as the robot's mic, the hand props in front of its camera."""
    from reachy_mini_bridge.testing import robot_options

    if sim_scene is None:
        missing("the sim has no scene to show hands in")
    sim_scene.clear()
    host, port = live_daemon
    config_path = _fill(
        "reachy.json",
        {"reachy.robot": robot_options(host, port)},
        out / "config.json",
    )
    config = load_reachy_config(str(config_path))
    system = build_system(config.app, ReachyEmbodiment(config))
    try:
        yield from _running(
            system, devices, SceneStage(sim_scene), out, scenario, echo=ROBOT_ECHO
        )
    finally:
        sim_scene.clear()


@pytest.fixture
def reachy(
    devices: Devices,
    keys: None,
    live_daemon: tuple[str, int],
    sim_audio: None,
    sim_scene: Any,
    tmp_path: Path,
) -> Iterator[E2E]:
    yield from _reachy(devices, live_daemon, sim_scene, tmp_path, "reachy")


@pytest.fixture(scope="class")
def reachy_conversation(
    devices: Devices,
    keys: None,
    live_daemon: tuple[str, int],
    sim_audio: None,
    sim_scene: Any,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[E2E]:
    """One robot app for a whole conversation (specs/e2e.md, "The conversation")."""
    out = tmp_path_factory.mktemp("reachy-conversation")
    yield from _reachy(devices, live_daemon, sim_scene, out, "reachy conversation")
