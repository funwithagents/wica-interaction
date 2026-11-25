"""The computer app's declaration: its sections, its default config, its folder (specs/setup.md)."""

from __future__ import annotations

import json
from pathlib import Path

from app_setup.app import AppModel
from app_setup.config_file import ConfigFile
from app_setup.folder import ConfigFolder
from app_setup.ui import config_page

from wica_interaction.computer.app import APP, DEFAULT_CONFIG, ensure_persona
from wica_interaction.computer.config import SECTIONS, load_config


def test_the_app_declares_its_sections_in_the_pages_order() -> None:
    assert [(s.name, s.title) for s in APP.sections] == [
        ("wica", "Agent"),
        ("asr", "Speech recognition"),
        ("tts", "Voice"),
        ("audio_processing", "Microphone processing"),
        ("vision", "Hand signs"),
        ("camera", "Camera"),
    ]
    # What the Setup tab can edit is exactly what the loader accepts.
    assert {s.name for s in APP.sections} == SECTIONS
    assert APP.name == "wica-interaction-computer"
    assert APP.host == "wica-interaction"
    assert APP.autostart is True


def test_a_new_folder_gets_the_default_config_and_the_persona(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    model = AppModel(APP, ConfigFolder(folder, APP.default_config))
    persona = ensure_persona(model.path)

    saved = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert saved == DEFAULT_CONFIG
    assert persona == folder / "computer.md"
    assert "voice assistant" in persona.read_text(encoding="utf-8")
    # The default config loads against the folder, its persona the folder's copy.
    config = load_config(model.used_config(model.saved_config()), base_dir=model.path)
    prompt = config.app.wica.agent.system_prompt_file
    assert prompt is not None and Path(prompt).resolve() == persona.resolve()


def test_an_edited_persona_is_never_overwritten(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "computer.md").write_text("My own persona.", encoding="utf-8")

    ensure_persona(folder)

    assert (folder / "computer.md").read_text(encoding="utf-8") == "My own persona."


def test_the_used_config_holds_exactly_the_apps_sections(tmp_path: Path) -> None:
    model = AppModel(APP, ConfigFolder(tmp_path / "app", APP.default_config))
    saved = {**model.saved_config(), "reachy": {"backend": "sim"}}
    # Every section the app declares (an absent one too, for its loader to default), no other.
    assert set(model.used_config(saved)) == SECTIONS


def test_the_default_config_has_no_problem_for_app_setup(tmp_path: Path) -> None:
    """Every section is in the default config (app-setup counts a missing one as a problem, which
    would keep the first Start from running)."""
    model = AppModel(APP, ConfigFolder(tmp_path / "app", APP.default_config))
    assert model.check_config(model.saved_config()) == []


def test_a_config_file_runs_with_its_persona_next_to_it(tmp_path: Path) -> None:
    """`--config FILE`: the same keyed document as the folder's `config.json`, read as it is, its
    persona resolved against the file's directory (specs/setup.md, "Three launches")."""
    path = tmp_path / "configs" / "desk.json"
    path.parent.mkdir()
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    (path.parent / "computer.md").write_text("A persona of my own.", encoding="utf-8")

    model = AppModel(APP, ConfigFile(path))

    assert not model.editable
    assert model.check_config(model.saved_config()) == []
    config = load_config(model.used_config(model.saved_config()), base_dir=model.path)
    prompt = config.app.wica.agent.system_prompt_file
    assert prompt is not None
    assert Path(prompt).resolve() == (path.parent / "computer.md").resolve()


def test_configure_builds_its_page_over_a_file_and_over_the_folder(
    tmp_path: Path,
) -> None:
    """`configure`'s page (Setup and Secrets, no session) builds over the app's sections."""
    path = tmp_path / "desk.json"
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    for source in (
        ConfigFile(path, writable=True),
        ConfigFolder(tmp_path / "app", APP.default_config),
    ):
        page = config_page(AppModel(APP, source))
        assert page.title == APP.title
