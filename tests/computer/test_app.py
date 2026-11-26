"""The computer app's declaration: its sections, its default config, its folder (specs/setup.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app_setup.app import AppModel
from app_setup.config_file import ConfigFile
from app_setup.folder import ConfigFolder
from app_setup.ui import config_page

from wica_interaction.computer.app import (
    APP,
    DEFAULT_CONFIG,
    STARTING_DOCUMENTS,
    ensure_persona,
)
from wica_interaction.computer.config import (
    SECTIONS,
    VISION_NEEDS_CAMERA,
    config_problems,
    load_config,
)
from wica_interaction.session import Session


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
    # Every section the app declares that the config has (an absent required one too, for its
    # loader to say so), no other.
    assert set(model.used_config(saved)) == SECTIONS


def test_the_default_config_has_no_problem_for_app_setup(tmp_path: Path) -> None:
    """The default config checks clean: every required section is there (app-setup counts a
    missing one as a problem, which would keep the first Start from running), and the optional
    ones on at first launch are too."""
    model = AppModel(APP, ConfigFolder(tmp_path / "app", APP.default_config))
    assert model.check_config(model.saved_config()) == []


def test_hand_signs_without_a_camera_are_a_reason_start_gives(tmp_path: Path) -> None:
    """The rule across sections (specs/computer-embodiment.md, "Hand signs need the camera"): the
    app's check sees the whole used config, so the session bar shows it and Start refuses it."""
    config = {k: v for k, v in DEFAULT_CONFIG.items() if k != "camera"}
    model = AppModel(APP, ConfigFolder(tmp_path / "app", config))
    session = Session(_never_opened, check=config_problems)

    # Camera is optional: app-setup has nothing to say about it being left out.
    assert model.check_config(model.saved_config()) == []
    assert VISION_NEEDS_CAMERA in session.blockers(model)
    assert VISION_NEEDS_CAMERA in session.start(model)
    assert session.status == "stopped"
    # The default config, camera included, has nothing to say across sections.
    assert config_problems(DEFAULT_CONFIG) == []


def _never_opened(_used: Any, _folder: Path) -> Any:
    raise AssertionError("a blocked Start builds nothing")


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


# --- Optional sections (specs/configuration.md, "Optional sections") --------------------------

_OPTIONAL = ("asr", "audio_processing", "vision", "camera")


def test_the_four_are_the_optional_sections_with_their_starting_documents() -> None:
    optional = {s.name: s.default for s in APP.sections if s.optional}
    assert optional == {name: STARTING_DOCUMENTS[name] for name in _OPTIONAL}


def test_left_out_they_are_off_for_app_setup_and_the_loader(tmp_path: Path) -> None:
    """Absent, an optional section is not used, not a problem, not a blocker, and the loader
    reads it as off."""
    config = {k: v for k, v in DEFAULT_CONFIG.items() if k not in _OPTIONAL}
    model = AppModel(APP, ConfigFolder(tmp_path / "app", config))
    saved = model.saved_config()

    assert set(model.optional_off(saved)) == set(_OPTIONAL)
    assert model.check_config(saved) == []
    assert not [r for r in model.blockers(saved) if r.startswith(_OPTIONAL)]
    used = model.used_config(saved)
    assert not set(used) & set(_OPTIONAL)
    loaded = load_config(used, base_dir=model.path)
    assert loaded.app.asr_engine is None and loaded.app.vision is None
    assert loaded.audio_processing is None and loaded.camera is None
    # Nothing across sections either: no hand signs, no camera needed.
    session = Session(_never_opened, check=config_problems)
    assert session.blockers(model) == model.blockers(saved)


def test_add_writes_the_starting_document_and_remove_takes_it_out(
    tmp_path: Path,
) -> None:
    config = {k: v for k, v in DEFAULT_CONFIG.items() if k not in _OPTIONAL}
    model = AppModel(APP, ConfigFolder(tmp_path / "app", config))
    for name in _OPTIONAL:
        added = model.add_section(model.saved_config(), name)
        assert added[name] == STARTING_DOCUMENTS[name]
        assert model.check_config(added) == []
        assert name not in model.remove_section(added, name)


def test_hand_signs_added_alone_ask_for_the_camera_on_the_session_bar(
    tmp_path: Path,
) -> None:
    """Adding Hand signs to a config without the Camera passes app-setup's per-section check, and
    the app's own rule names the missing camera; adding the Camera too clears it."""
    config = {k: v for k, v in DEFAULT_CONFIG.items() if k not in _OPTIONAL}
    model = AppModel(APP, ConfigFolder(tmp_path / "app", config))
    session = Session(_never_opened, check=config_problems)

    model.save(model.add_section(model.saved_config(), "vision"))
    assert model.check_config(model.saved_config()) == []
    assert VISION_NEEDS_CAMERA in session.blockers(model)
    model.save(model.add_section(model.saved_config(), "camera"))
    assert VISION_NEEDS_CAMERA not in session.blockers(model)
