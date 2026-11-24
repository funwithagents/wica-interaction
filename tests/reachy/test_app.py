"""The Reachy Mini app's declaration: its sections, its default config, its folder (specs/setup.md)."""

from __future__ import annotations

import json
from pathlib import Path

from app_setup.app import AppModel
from app_setup.folder import ConfigFolder

from wica_interaction.reachy.app import APP, DEFAULT_CONFIG, ensure_persona
from wica_interaction.reachy.config import SECTIONS, load_config


def test_the_app_declares_its_sections_in_the_pages_order() -> None:
    assert [(s.name, s.title) for s in APP.sections] == [
        ("wica", "Agent"),
        ("asr", "Speech recognition"),
        ("reachy", "Reachy Mini"),
        ("vision", "Hand signs"),
    ]
    # What the Setup tab can edit is exactly what the loader accepts.
    assert {s.name for s in APP.sections} == SECTIONS
    assert APP.name == "wica-interaction-reachy"
    assert APP.host == "wica-interaction"
    assert APP.autostart is True


def test_a_new_folder_gets_the_default_config_and_the_persona(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    model = AppModel(APP, ConfigFolder(folder, APP.default_config))
    persona = ensure_persona(model.path)

    saved = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert saved == DEFAULT_CONFIG
    assert persona == folder / "reachy.md"
    assert "Reachy Mini" in persona.read_text(encoding="utf-8")
    # The default config loads against the folder, its persona the folder's copy.
    config = load_config(model.used_config(model.saved_config()), base_dir=model.path)
    prompt = config.app.wica.agent.system_prompt_file
    assert prompt is not None and Path(prompt).resolve() == persona.resolve()


def test_an_edited_persona_is_never_overwritten(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "reachy.md").write_text("My own persona.", encoding="utf-8")

    ensure_persona(folder)

    assert (folder / "reachy.md").read_text(encoding="utf-8") == "My own persona."


def test_the_used_config_holds_exactly_the_apps_sections(tmp_path: Path) -> None:
    model = AppModel(APP, ConfigFolder(tmp_path / "app", APP.default_config))
    saved = {**model.saved_config(), "tts": {"module": {"type": "phonon"}}}
    # Every section the app declares (an absent one too, for its loader to default), no other.
    assert set(model.used_config(saved)) == SECTIONS


def test_the_default_config_has_no_problem_for_app_setup(tmp_path: Path) -> None:
    """Every section is in the default config (app-setup counts a missing one as a problem, which
    would keep the first Start from running)."""
    model = AppModel(APP, ConfigFolder(tmp_path / "app", APP.default_config))
    assert model.check_config(model.saved_config()) == []
