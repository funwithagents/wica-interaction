"""Functional tests for the composed app config loader (specs/configuration.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asr_engine.config import ASREngineConfig
from reachy_mini_bridge.config import ReachyMiniConfig
from wica.config import WicaConfig

from reachy_mini_interaction_wica.config import AppConfig, VisionConfig, load_config


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
        "reachy": {
            "tts": {
                "module": {
                    "type": "elevenlabs",
                    "api_key_env": "ELEVENLABS_API_KEY",
                    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
                },
                "player": {"device": None},
            }
        },
    }


def _write(tmp_path: Path, config: dict, name: str = "app.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def test_happy_path_builds_both_engine_configs_and_a_bridge_config(
    tmp_path: Path,
) -> None:
    cfg = load_config(_write(tmp_path, _composed()))

    assert isinstance(cfg, AppConfig)

    assert isinstance(cfg.wica, WicaConfig)
    assert cfg.wica.agent.provider == "anthropic"
    assert cfg.wica.agent.model == "claude-sonnet-5"

    assert isinstance(cfg.asr_engine, ASREngineConfig)
    assert cfg.asr_engine.module.type == "deepgram_v2"
    assert cfg.asr_engine.dictation_default_segmentation_mode == "timeout"

    # The `reachy` section is a verbatim bridge config, delegated to ReachyMiniConfig.from_dict.
    assert isinstance(cfg.reachy, ReachyMiniConfig)
    assert cfg.reachy.backend == "real"
    assert cfg.reachy.tts is not None
    assert cfg.reachy.tts["module"]["type"] == "elevenlabs"
    # The module block is carried through verbatim, extra fields intact.
    assert cfg.reachy.tts["module"]["voice_id"] == "JBFqnCBsd6RMkjVDRZzb"


def test_system_prompt_file_is_located_against_config_dir(tmp_path: Path) -> None:
    # A relative system_prompt_file must resolve against the config file's own directory,
    # proving base_dir was threaded into WicaConfig.from_dict. Compare *resolved* paths:
    # WICA calls Path.resolve(), which canonicalizes symlinks (macOS /var -> /private/var).
    cfg = load_config(
        _write(tmp_path, _composed(system_prompt_file="prompts/assistant.md"))
    )

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
    config["asr"] = {"not_engine": {}}
    with pytest.raises(
        ValueError, match="section 'asr' must be an object with an 'engine' block"
    ):
        load_config(_write(tmp_path, config))


def test_loads_with_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The composed file references keys by env-var name; with those vars unset, loading must still
    # succeed (no env read at load — the "app opens without credentials" contract). Neither WICA's
    # loader, ASR's, nor the bridge's reads an env var at load time.
    for var in ("WICA_ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = load_config(_write(tmp_path, _composed()))
    assert cfg.wica.agent.api_key_env == "WICA_ANTHROPIC_API_KEY"


@pytest.mark.parametrize("backend", ["real", "sim", "fake"])
def test_reachy_backend_round_trips(tmp_path: Path, backend: str) -> None:
    config = _composed()
    config["reachy"]["backend"] = backend
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.backend == backend


def test_reachy_daemon_block_is_parsed(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["backend"] = "sim"
    config["reachy"]["daemon"] = {"spawn": "auto", "headless": False}
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.daemon.spawn == "auto"
    assert cfg.reachy.daemon.headless is False


def test_reachy_audio_profile_is_carried(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["audio"] = {"xvf3800": [["AEC_ENABLE", [1]]]}
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.audio.xvf3800 == [["AEC_ENABLE", [1]]]


def test_reachy_absent_defaults_to_the_bridges_own_defaults(tmp_path: Path) -> None:
    config = _composed()
    config.pop("reachy", None)
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy == ReachyMiniConfig()
    assert cfg.reachy.backend == "real"
    assert cfg.reachy.tts is None


def test_reachy_empty_object_defaults_the_same_way(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"] = {}
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy == ReachyMiniConfig()


def test_invalid_reachy_backend_raises(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["backend"] = "hologram"
    with pytest.raises(ValueError, match="'backend' must be one of"):
        load_config(_write(tmp_path, config))


def test_unknown_reachy_key_raises(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["bogus"] = True
    with pytest.raises(ValueError, match="unknown key"):
        load_config(_write(tmp_path, config))


def test_daemon_spawn_on_fake_backend_raises(tmp_path: Path) -> None:
    # The bridge spawns a daemon for `sim` (MuJoCo) and `real` (a USB-attached robot); only the
    # in-package `fake` has none, so that is the combination its loader rejects.
    config = _composed()
    config["reachy"]["backend"] = "fake"
    config["reachy"]["daemon"] = {"spawn": "auto"}
    with pytest.raises(ValueError, match="requires backend 'sim' or 'real'"):
        load_config(_write(tmp_path, config))


def test_daemon_spawn_on_real_backend_is_accepted(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["backend"] = "real"
    config["reachy"]["daemon"] = {"spawn": "auto"}
    loaded = load_config(_write(tmp_path, config))
    assert loaded.reachy.backend == "real"
    assert loaded.reachy.daemon.spawn == "auto"


def test_reachy_not_an_object_raises(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"] = "real"
    with pytest.raises(ValueError, match="section 'reachy' must be an object"):
        load_config(_write(tmp_path, config))


def test_reachy_section_is_optional_but_required_sections_stay(tmp_path: Path) -> None:
    # `reachy` missing is fine; a missing *required* section still raises even with reachy set.
    config = _composed()
    del config["wica"]
    with pytest.raises(ValueError, match="missing required section.*'wica'"):
        load_config(_write(tmp_path, config))


def test_engine_validation_error_propagates(tmp_path: Path) -> None:
    # A field-level error is the engine's own, raised as-is — not swallowed or re-wrapped here.
    config = _composed()
    config["asr"]["engine"]["dictation_default_segmentation_mode"] = "nonsense"
    with pytest.raises(ValueError, match="dictation_default_segmentation_mode"):
        load_config(_write(tmp_path, config))


# --- The optional `vision` section (specs/configuration.md, "The `vision` section") ------------


def test_vision_absent_means_off(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _composed()))
    assert cfg.vision is None


def test_vision_null_means_off(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {**_composed(), "vision": None}))
    assert cfg.vision is None


def test_vision_empty_object_takes_the_defaults(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {**_composed(), "vision": {}}))
    assert cfg.vision == VisionConfig(
        enabled=False, threshold=0.55, fps=5.0, max_hands=2, device=None
    )


def test_vision_fields_are_carried(tmp_path: Path) -> None:
    section = {
        "enabled": True,
        "threshold": 0.7,
        "fps": 10,
        "max_hands": 1,
        "device": "cpu",
    }
    cfg = load_config(_write(tmp_path, {**_composed(), "vision": section}))
    assert cfg.vision == VisionConfig(
        enabled=True, threshold=0.7, fps=10.0, max_hands=1, device="cpu"
    )


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ({"enabled": "yes"}, "vision.enabled"),
        ({"enabled": 1}, "vision.enabled"),
        ({"threshold": 1.5}, "vision.threshold"),
        ({"threshold": "high"}, "vision.threshold"),
        ({"threshold": True}, "vision.threshold"),
        ({"fps": 0}, "vision.fps"),
        ({"fps": -5}, "vision.fps"),
        ({"max_hands": 0}, "vision.max_hands"),
        ({"max_hands": 1.5}, "vision.max_hands"),
        ({"max_hands": 2.0}, "vision.max_hands"),
        ({"max_hands": True}, "vision.max_hands"),
        ({"max_hands": "two"}, "vision.max_hands"),
        ({"device": 3}, "vision.device"),
    ],
)
def test_vision_invalid_value_names_the_field(
    tmp_path: Path, section: dict, field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        load_config(_write(tmp_path, {**_composed(), "vision": section}))


def test_vision_unknown_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'mirror'"):
        load_config(_write(tmp_path, {**_composed(), "vision": {"mirror": True}}))


def test_vision_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'vision' must be an object"):
        load_config(_write(tmp_path, {**_composed(), "vision": "on"}))
