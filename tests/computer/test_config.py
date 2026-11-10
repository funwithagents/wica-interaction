"""Functional tests for the computer embodiment's config loader (specs/computer-embodiment.md).

The shared sections are tested in ``tests/test_config.py``; what is pinned here is this loader's own
sections — ``tts`` and ``camera`` — and that it rejects a section belonging to another body. The
``audio_processing`` cases live with that feature, in ``test_audio_processing.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tts_engine.config import TTSEngineConfig

from wica_interaction.computer.config import CameraConfig, ComputerConfig, load_config


def _composed() -> dict:
    """A full, valid computer config using env-reference keys (no literals, no env reads at load)."""
    return {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "api_key_env": "WICA_ANTHROPIC_API_KEY",
                "system_prompt_file": "prompts/computer.md",
            }
        },
        "tts": {
            "engine": {
                "module": {
                    "type": "elevenlabs",
                    "api_key_env": "ELEVENLABS_API_KEY",
                    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
                },
                "player": {"device": None},
            }
        },
        "asr": {
            "engine": {
                "module": {
                    "type": "deepgram_v2",
                    "api_key_env": "DEEPGRAM_API_KEY",
                    "model": "nova-3",
                },
                "audio": {"device": None},
            }
        },
    }


def _write(tmp_path: Path, config: dict, name: str = "app.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def test_happy_path_yields_the_shared_config_plus_this_bodys_sections(
    tmp_path: Path,
) -> None:
    cfg = load_config(_write(tmp_path, _composed()))

    assert isinstance(cfg, ComputerConfig)
    # The shared sections live under `.app`, untouched by this loader.
    assert cfg.app.wica.agent.provider == "anthropic"
    assert cfg.app.asr_engine.module.type == "deepgram_v2"
    assert cfg.app.vision is None

    assert isinstance(cfg.tts_engine, TTSEngineConfig)
    # The `engine` block goes verbatim to tts-engine's own loader, module dict and all.
    assert cfg.tts_engine.module["type"] == "elevenlabs"
    assert cfg.tts_engine.module["voice_id"] == "JBFqnCBsd6RMkjVDRZzb"
    assert not cfg.audio_processing.any_enabled  # absent ⇒ every feature off
    assert cfg.camera == CameraConfig(device=0)  # absent ⇒ the default device


def test_missing_tts_section_names_it(tmp_path: Path) -> None:
    config = _composed()
    del config["tts"]
    with pytest.raises(ValueError, match="missing required section.*'tts'"):
        load_config(_write(tmp_path, config))


def test_tts_without_an_engine_block_raises(tmp_path: Path) -> None:
    config = _composed()
    config["tts"] = {"module": {"type": "elevenlabs"}}  # the engine block, one level up
    with pytest.raises(
        ValueError, match="section 'tts' must be an object with an 'engine' block"
    ):
        load_config(_write(tmp_path, config))


def test_tts_field_error_propagates_from_the_engines_loader(tmp_path: Path) -> None:
    # A field-level error is tts-engine's own, raised as-is — its ConfigError is a ValueError
    # subclass, so `run_app` still has one error type to catch.
    config = _composed()
    config["tts"]["engine"]["module"] = "elevenlabs"  # a string where the block belongs
    with pytest.raises(ValueError, match="'engine.module' must be an object"):
        load_config(_write(tmp_path, config))


def test_loads_with_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("WICA_ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = load_config(_write(tmp_path, _composed()))
    assert cfg.tts_engine.module["api_key_env"] == "ELEVENLABS_API_KEY"


def test_a_reachy_section_is_rejected(tmp_path: Path) -> None:
    # A robot file handed to the computer command fails on its own section, rather than being
    # silently ignored (specs/configuration.md, "Each embodiment loads its own file").
    config = _composed()
    config["reachy"] = {"backend": "sim"}
    with pytest.raises(ValueError, match="unknown section.*'reachy'"):
        load_config(_write(tmp_path, config))


# --- The `camera` section (specs/computer-embodiment.md, "Configuration") ----------------------


@pytest.mark.parametrize("device", [0, 2, "/dev/video0", "http://cam.local/stream"])
def test_camera_device_round_trips(tmp_path: Path, device: int | str) -> None:
    cfg = load_config(_write(tmp_path, {**_composed(), "camera": {"device": device}}))
    assert cfg.camera == CameraConfig(device=device)


def test_camera_empty_object_takes_the_default(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {**_composed(), "camera": {}}))
    assert cfg.camera == CameraConfig(device=0)


def test_camera_null_takes_the_default(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {**_composed(), "camera": None}))
    assert cfg.camera == CameraConfig(device=0)


def test_camera_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="section 'camera' must be an object"):
        load_config(_write(tmp_path, {**_composed(), "camera": 0}))


def test_camera_unknown_key_names_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'mirror'"):
        load_config(
            _write(tmp_path, {**_composed(), "camera": {"device": 0, "mirror": True}})
        )


@pytest.mark.parametrize("device", [True, 1.5, None, []])
def test_camera_device_of_the_wrong_type_raises(tmp_path: Path, device: object) -> None:
    # `None` here is an explicit null *inside* the section — an absent section is the default, but a
    # null device is a typo worth naming. `True` must not pass for the index 1 (bool subclasses int).
    with pytest.raises(
        ValueError, match="camera.device must be an integer or a string"
    ):
        load_config(_write(tmp_path, {**_composed(), "camera": {"device": device}}))


def test_a_discarded_voice_loads_with_echo_cancellation_off(tmp_path: Path) -> None:
    config = _composed()
    config["tts"]["engine"]["player"] = {"discard": True}
    loaded = load_config(_write(tmp_path, config))
    assert loaded.tts_engine.player.discard is True


def test_a_discarded_voice_with_echo_cancellation_on_is_rejected(
    tmp_path: Path,
) -> None:
    # The coordinator's player would open the default device: the voice would not be discarded.
    config = _composed()
    config["tts"]["engine"]["player"] = {"discard": True}
    config["audio_processing"] = {"echo_cancellation": {"enabled": True}}
    with pytest.raises(ValueError, match="echo_cancellation"):
        load_config(_write(tmp_path, config))
