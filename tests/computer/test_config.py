"""Functional tests for the computer embodiment's config loader (specs/computer-embodiment.md).

The shared sections are tested in ``tests/test_config.py``; what is pinned here is this loader's own
sections — ``tts`` and ``camera`` — and that it rejects a section belonging to another body. The
``audio_processing`` cases live with that feature, in ``test_audio_processing.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tts_engine.config import TTSEngineConfig

from wica_interaction.computer.app import DEFAULT_CONFIG
from wica_interaction.computer.config import CameraConfig, ComputerConfig, load_config


def _composed() -> dict:
    """A full, valid computer config using env-reference keys (no literals, no env reads at load)."""
    return {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "api_key_env": "WICA_ANTHROPIC_API_KEY",
                "system_prompt_file": "computer.md",
            }
        },
        "tts": {
            "module": {
                "type": "elevenlabs",
                "api_key_env": "ELEVENLABS_API_KEY",
                "voice_id": "JBFqnCBsd6RMkjVDRZzb",
            },
            "player": {"device": None},
        },
        "asr": {
            "module": {
                "type": "deepgram-v2",
                "api_key_env": "DEEPGRAM_API_KEY",
                "model": "nova-3",
            },
            "audio_source": {"type": "input-device", "device": None},
        },
    }


def _load(tmp_path: Path, config: dict) -> ComputerConfig:
    return load_config(config, base_dir=tmp_path)


def test_happy_path_yields_the_shared_config_plus_this_bodys_sections(
    tmp_path: Path,
) -> None:
    cfg = _load(tmp_path, _composed())

    assert isinstance(cfg, ComputerConfig)
    # The shared sections live under `.app`, untouched by this loader.
    assert cfg.app.wica.agent.provider == "anthropic"
    assert cfg.app.asr_engine.module.type == "deepgram-v2"
    assert cfg.app.vision is None

    assert isinstance(cfg.tts_engine, TTSEngineConfig)
    # The section goes verbatim to tts-engine's own loader, module dict and all.
    assert cfg.tts_engine.module["type"] == "elevenlabs"
    assert cfg.tts_engine.module["voice_id"] == "JBFqnCBsd6RMkjVDRZzb"
    assert not cfg.audio_processing.any_enabled  # absent ⇒ every feature off
    assert cfg.camera == CameraConfig(device=0)  # absent ⇒ the default device


def test_missing_tts_section_names_it(tmp_path: Path) -> None:
    config = _composed()
    del config["tts"]
    with pytest.raises(ValueError, match="missing required section.*'tts'"):
        _load(tmp_path, config)


def test_tts_that_is_not_an_object_raises(tmp_path: Path) -> None:
    config = _composed()
    config["tts"] = "phonon"
    with pytest.raises(ValueError, match="section 'tts' must be an object"):
        _load(tmp_path, config)


def test_a_relative_voice_file_resolves_against_the_folder(tmp_path: Path) -> None:
    # tts-engine resolves a module's relative paths against `base_dir`: the app's folder.
    config = _composed()
    config["tts"] = {"module": {"type": "piper", "model_path": "voices/fr.onnx"}}
    cfg = _load(tmp_path, config)
    assert cfg.tts_engine.module["base_dir"] == str(tmp_path)


def test_tts_field_error_propagates_from_the_engines_loader(tmp_path: Path) -> None:
    # A field-level error is tts-engine's own, raised as-is — its ConfigError is a ValueError
    # subclass, so `run_app` still has one error type to catch.
    config = _composed()
    config["tts"]["module"] = "elevenlabs"  # a string where the block belongs
    with pytest.raises(ValueError, match="module"):
        _load(tmp_path, config)


def test_loads_with_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("WICA_ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = _load(tmp_path, _composed())
    assert cfg.tts_engine.module["api_key_env"] == "ELEVENLABS_API_KEY"


def test_a_reachy_section_is_rejected(tmp_path: Path) -> None:
    # A robot's config handed to the computer's loader fails on its own section, rather than being
    # silently ignored (specs/configuration.md, "Loading").
    config = _composed()
    config["reachy"] = {"backend": "sim"}
    with pytest.raises(ValueError, match="unknown section.*'reachy'"):
        _load(tmp_path, config)


# --- The `camera` section (specs/computer-embodiment.md, "Configuration") ----------------------


@pytest.mark.parametrize("device", [0, 2, "/dev/video0", "http://cam.local/stream"])
def test_camera_device_round_trips(tmp_path: Path, device: int | str) -> None:
    cfg = _load(tmp_path, {**_composed(), "camera": {"device": device}})
    assert cfg.camera == CameraConfig(device=device)


def test_camera_empty_object_takes_the_default(tmp_path: Path) -> None:
    cfg = _load(tmp_path, {**_composed(), "camera": {}})
    assert cfg.camera == CameraConfig(device=0)


def test_camera_null_takes_the_default(tmp_path: Path) -> None:
    cfg = _load(tmp_path, {**_composed(), "camera": None})
    assert cfg.camera == CameraConfig(device=0)


def test_camera_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="camera must be an object"):
        _load(tmp_path, {**_composed(), "camera": 0})


def test_camera_unknown_key_names_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'mirror'"):
        _load(tmp_path, {**_composed(), "camera": {"device": 0, "mirror": True}})


@pytest.mark.parametrize("device", [True, 1.5, None, []])
def test_camera_device_of_the_wrong_type_raises(tmp_path: Path, device: object) -> None:
    # `None` here is an explicit null *inside* the section — an absent section is the default, but a
    # null device is a typo worth naming. `True` must not pass for the index 1 (bool subclasses int).
    with pytest.raises(
        ValueError, match="camera 'device' must be an integer or a non-empty string"
    ):
        _load(tmp_path, {**_composed(), "camera": {"device": device}})


def test_the_default_config_loads(tmp_path: Path) -> None:
    """The shipped default must open, and run without a hosted speech key: a local recognizer and
    a local voice, echo cancellation and hand signs on, everything else at its default (the
    webcam is device 0), the persona the folder's copy."""
    cfg = _load(tmp_path, DEFAULT_CONFIG)
    assert cfg.app.asr_engine.module.type == "mlx-audio"
    assert cfg.tts_engine.module["type"] == "phonon"
    assert cfg.audio_processing.echo_cancellation.enabled is True
    assert cfg.app.vision is not None and cfg.app.vision.enabled is True
    assert cfg.camera == CameraConfig()
    prompt = cfg.app.wica.agent.system_prompt_file
    assert prompt is not None
    assert Path(prompt).resolve() == (tmp_path / "computer.md").resolve()
