"""Functional tests for the composed app config loader (specs/configuration.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asr_engine.config import ASREngineConfig
from tts_engine.config import TTSEngineConfig
from wica.config import WicaConfig

from reachy_mini_interaction_wica.config import AppConfig, load_config


def _composed(system_prompt_file: str = "prompts/assistant.md") -> dict:
    """A full, valid composed config using env-reference keys (no literals, no env reads at load)."""
    return {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "api_key_env": "WICA_ANTHROPIC_API_KEY",
                "system_prompt_file": system_prompt_file,
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
                "dictation_default_segmentation_mode": "timeout",
                "segmentation": {"end_of_speech_timeout_s": 2.0},
            }
        },
    }


def _write(tmp_path: Path, config: dict, name: str = "app.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def test_happy_path_builds_all_three_engine_configs(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _composed()))

    assert isinstance(cfg, AppConfig)

    assert isinstance(cfg.wica, WicaConfig)
    assert cfg.wica.agent.provider == "anthropic"
    assert cfg.wica.agent.model == "claude-sonnet-5"

    assert isinstance(cfg.tts_engine, TTSEngineConfig)
    assert cfg.tts_engine.module["type"] == "elevenlabs"
    # The module block is carried through verbatim, extra fields intact.
    assert cfg.tts_engine.module["voice_id"] == "JBFqnCBsd6RMkjVDRZzb"

    assert isinstance(cfg.asr_engine, ASREngineConfig)
    assert cfg.asr_engine.module.type == "deepgram_v2"
    assert cfg.asr_engine.dictation_default_segmentation_mode == "timeout"

    # No `reachy` section in the composed fixture ⇒ the default backend.
    assert cfg.reachy.backend == "real"


def test_system_prompt_file_is_located_against_config_dir(tmp_path: Path) -> None:
    # A relative system_prompt_file must resolve against the config file's own directory,
    # proving base_dir was threaded into WicaConfig.from_dict. Compare *resolved* paths:
    # WICA calls Path.resolve(), which canonicalizes symlinks (macOS /var -> /private/var).
    cfg = load_config(_write(tmp_path, _composed(system_prompt_file="prompts/assistant.md")))

    located = cfg.wica.agent.system_prompt_file
    assert located is not None
    expected = (tmp_path / "prompts" / "assistant.md").resolve()
    assert Path(located).resolve() == expected
    # Located, not read: the file need not exist.
    assert not expected.exists()


def test_missing_file_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not read config file"):
        load_config(str(tmp_path / "does-not-exist.json"))


def test_invalid_json_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_config(str(path))


def test_non_object_top_level_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_config(str(path))


def test_missing_section_names_the_missing_key(tmp_path: Path) -> None:
    config = _composed()
    del config["asr"]
    with pytest.raises(ValueError, match="missing required section.*'asr'"):
        load_config(_write(tmp_path, config))


def test_section_without_engine_block_raises(tmp_path: Path) -> None:
    config = _composed()
    config["tts"] = {"not_engine": {}}
    with pytest.raises(ValueError, match="section 'tts' must be an object with an 'engine' block"):
        load_config(_write(tmp_path, config))


def test_loads_with_credentials_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The composed file references keys by env-var name; with those vars unset, loading must still
    # succeed (no env read at load — the "app opens without credentials" contract).
    for var in ("WICA_ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = load_config(_write(tmp_path, _composed()))
    assert cfg.wica.agent.api_key_env == "WICA_ANTHROPIC_API_KEY"


@pytest.mark.parametrize("backend", ["real", "sim", "fake"])
def test_reachy_backend_parses(tmp_path: Path, backend: str) -> None:
    config = _composed()
    config["reachy"] = {"backend": backend}
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.backend == backend


def test_reachy_absent_defaults_to_real(tmp_path: Path) -> None:
    config = _composed()
    config.pop("reachy", None)  # the fixture has none, but be explicit
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.backend == "real"


def test_reachy_empty_object_defaults_to_real(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"] = {}  # present but no backend key ⇒ default
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.backend == "real"


def test_invalid_reachy_backend_raises(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"] = {"backend": "hologram"}
    with pytest.raises(ValueError, match="reachy.backend must be one of"):
        load_config(_write(tmp_path, config))


def test_reachy_section_is_optional_but_required_sections_stay(tmp_path: Path) -> None:
    # `reachy` missing is fine; a missing *required* engine section still raises even with reachy set.
    config = _composed()
    config["reachy"] = {"backend": "fake"}
    del config["wica"]
    with pytest.raises(ValueError, match="missing required section.*'wica'"):
        load_config(_write(tmp_path, config))


def test_engine_validation_error_propagates(tmp_path: Path) -> None:
    # A field-level error is the engine's own, raised as-is — not swallowed or re-wrapped here.
    config = _composed()
    config["asr"]["engine"]["dictation_default_segmentation_mode"] = "nonsense"
    with pytest.raises(ValueError, match="dictation_default_segmentation_mode"):
        load_config(_write(tmp_path, config))
