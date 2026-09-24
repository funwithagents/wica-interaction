"""Functional tests for the shared part of the composed app config (specs/configuration.md).

Drives the two loading layers every embodiment reuses — ``read_config_file`` (the file rules) and
``load_app_config`` (the shared sections, and the unknown-section rule scoped by the caller's
section set). The sections an embodiment adds are tested with that embodiment
(``tests/reachy/test_config.py``, ``tests/computer/test_config.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asr_engine.config import ASREngineConfig
from wica.config import WicaConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    VisionConfig,
    load_app_config,
    read_config_file,
)

# What a reachy-flavoured caller passes: the shared names plus its own section.
_SECTIONS = SHARED_SECTIONS | {"reachy"}


def _composed(system_prompt_file: str = "prompts/reachy.md") -> dict:
    """A full, valid shared config using env-reference keys (no literals, no env reads at load)."""
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
    }


def _write(tmp_path: Path, config: dict, name: str = "app.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def _load(
    tmp_path: Path, config: dict, *, sections: frozenset[str] = _SECTIONS
) -> AppConfig:
    """Read + validate, the way an embodiment's own ``load_config`` does."""
    path = _write(tmp_path, config)
    return load_app_config(read_config_file(path), path=path, sections=sections)


def test_happy_path_builds_both_engine_configs(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _composed())

    assert isinstance(cfg, AppConfig)

    assert isinstance(cfg.wica, WicaConfig)
    assert cfg.wica.agent.provider == "anthropic"
    assert cfg.wica.agent.model == "claude-sonnet-5"

    assert isinstance(cfg.asr_engine, ASREngineConfig)
    assert cfg.asr_engine.module.type == "deepgram_v2"
    assert cfg.asr_engine.dictation_default_segmentation_mode == "timeout"


def test_system_prompt_file_is_located_against_config_dir(tmp_path: Path) -> None:
    # A relative system_prompt_file must resolve against the config file's own directory,
    # proving base_dir was threaded into WicaConfig.from_dict. Compare *resolved* paths:
    # WICA calls Path.resolve(), which canonicalizes symlinks (macOS /var -> /private/var).
    cfg = _load(tmp_path, _composed(system_prompt_file="prompts/reachy.md"))

    located = cfg.wica.agent.system_prompt_file
    assert located is not None
    expected = (tmp_path / "prompts" / "reachy.md").resolve()
    assert Path(located).resolve() == expected
    # Located, not read: the file need not exist.
    assert not expected.exists()


def test_missing_file_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not read config file"):
        read_config_file(str(tmp_path / "does-not-exist.json"))


def test_invalid_json_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_config_file(str(path))


def test_non_object_top_level_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        read_config_file(str(path))


def test_missing_section_names_the_missing_key(tmp_path: Path) -> None:
    config = _composed()
    del config["asr"]
    with pytest.raises(ValueError, match="missing required section.*'asr'"):
        _load(tmp_path, config)


def test_section_without_engine_block_raises(tmp_path: Path) -> None:
    config = _composed()
    config["asr"] = {"not_engine": {}}
    with pytest.raises(
        ValueError, match="section 'asr' must be an object with an 'engine' block"
    ):
        _load(tmp_path, config)


def test_unknown_top_level_section_names_it(tmp_path: Path) -> None:
    # The whole point of the `sections` argument: a section the calling embodiment does not declare
    # is an error, not a silent no-op — a reachy file handed to the computer command fails here.
    config = {**_composed(), "reachy": {"backend": "fake"}}
    with pytest.raises(ValueError, match="unknown section.*'reachy'"):
        _load(tmp_path, config, sections=SHARED_SECTIONS)


def test_section_inside_the_callers_set_is_accepted(tmp_path: Path) -> None:
    # The same file, with 'reachy' in the section set, loads — the embodiment parses it itself.
    cfg = _load(tmp_path, {**_composed(), "reachy": {"backend": "fake"}})
    assert cfg.wica.agent.provider == "anthropic"


def test_loads_with_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The composed file references keys by env-var name; with those vars unset, loading must still
    # succeed (no env read at load — the "app opens without credentials" contract).
    for var in ("WICA_ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = _load(tmp_path, _composed())
    assert cfg.wica.agent.api_key_env == "WICA_ANTHROPIC_API_KEY"


def test_engine_validation_error_propagates(tmp_path: Path) -> None:
    # A field-level error is the engine's own, raised as-is — not swallowed or re-wrapped here.
    config = _composed()
    config["asr"]["engine"]["dictation_default_segmentation_mode"] = "nonsense"
    with pytest.raises(ValueError, match="dictation_default_segmentation_mode"):
        _load(tmp_path, config)


# --- The optional `vision` section (specs/configuration.md, "The `vision` section") ------------


def test_vision_absent_means_off(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _composed())
    assert cfg.vision is None


def test_vision_null_means_off(tmp_path: Path) -> None:
    cfg = _load(tmp_path, {**_composed(), "vision": None})
    assert cfg.vision is None


def test_vision_empty_object_takes_the_defaults(tmp_path: Path) -> None:
    cfg = _load(tmp_path, {**_composed(), "vision": {}})
    assert cfg.vision == VisionConfig(
        enabled=False, threshold=0.55, fps=5.0, max_hands=2, hold_s=0.5, device=None
    )


def test_vision_fields_are_carried(tmp_path: Path) -> None:
    section = {
        "enabled": True,
        "threshold": 0.7,
        "fps": 10,
        "max_hands": 1,
        "hold_s": 0,
        "device": "cpu",
    }
    cfg = _load(tmp_path, {**_composed(), "vision": section})
    assert cfg.vision is not None
    assert cfg.vision == VisionConfig(
        enabled=True, threshold=0.7, fps=10.0, max_hands=1, hold_s=0.0, device="cpu"
    )
    assert isinstance(cfg.vision.hold_s, float)  # the integer 0 is carried as a float


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
        ({"hold_s": -0.1}, "vision.hold_s"),
        ({"hold_s": "0.5"}, "vision.hold_s"),
        ({"hold_s": True}, "vision.hold_s"),
        ({"device": 3}, "vision.device"),
    ],
)
def test_vision_invalid_value_names_the_field(
    tmp_path: Path, section: dict, field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        _load(tmp_path, {**_composed(), "vision": section})


def test_vision_unknown_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'mirror'"):
        _load(tmp_path, {**_composed(), "vision": {"mirror": True}})


def test_vision_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'vision' must be an object"):
        _load(tmp_path, {**_composed(), "vision": "on"})
