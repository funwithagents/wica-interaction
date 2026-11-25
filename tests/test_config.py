"""Functional tests for the shared part of the composed app config (specs/configuration.md).

Drives ``load_app_config`` (the shared sections over a config document, relative paths against the
app's folder, and the unknown-section rule scoped by the caller's section set), which every
embodiment's loader reuses. The sections an embodiment adds are tested with that embodiment
(``tests/reachy/test_config.py``, ``tests/computer/test_config.py``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from asr_engine.config import ASREngineConfig
from wica.config import WicaConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    VisionConfig,
    load_app_config,
)

# What a reachy-flavoured caller passes: the shared names plus its own section.
_SECTIONS = SHARED_SECTIONS | {"reachy"}


def _composed(system_prompt_file: str = "reachy.md") -> dict:
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
            "module": {
                "type": "deepgram-v2",
                "api_key_env": "DEEPGRAM_API_KEY",
                "model": "nova-3",
            },
            "audio_source": {"type": "input-device", "device": None},
            "dictation_default_segmentation_mode": "timeout",
            "segmentation": {"end_of_speech_timeout_s": 2.0},
        },
    }


def _load(
    tmp_path: Path, config: dict, *, sections: frozenset[str] = _SECTIONS
) -> AppConfig:
    """Validate a document against the app's folder, the way an embodiment's ``load_config``
    does."""
    return load_app_config(config, base_dir=tmp_path, sections=sections)


def test_happy_path_builds_both_engine_configs(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _composed())

    assert isinstance(cfg, AppConfig)

    assert isinstance(cfg.wica, WicaConfig)
    assert cfg.wica.agent.provider == "anthropic"
    assert cfg.wica.agent.model == "claude-sonnet-5"

    assert isinstance(cfg.asr_engine, ASREngineConfig)
    assert cfg.asr_engine.module.type == "deepgram-v2"
    assert cfg.asr_engine.dictation_default_segmentation_mode == "timeout"


def test_system_prompt_file_is_located_against_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative system_prompt_file resolves against the app's folder (`base_dir`), whatever the
    # working directory. Compare *resolved* paths: WICA calls Path.resolve(), which canonicalizes
    # symlinks (macOS /var -> /private/var).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    folder = tmp_path / "folder"
    cfg = load_app_config(
        _composed(system_prompt_file="reachy.md"), base_dir=folder, sections=_SECTIONS
    )

    located = cfg.wica.agent.system_prompt_file
    assert located is not None
    assert Path(located).resolve() == (folder / "reachy.md").resolve()
    assert os.getcwd() == str(elsewhere.resolve())


def test_missing_section_names_the_missing_key(tmp_path: Path) -> None:
    config = _composed()
    del config["wica"]
    with pytest.raises(ValueError, match="missing required section.*'wica'"):
        _load(tmp_path, config)


@pytest.mark.parametrize("absent", [None, "deleted"])
def test_asr_absent_means_speech_input_off(tmp_path: Path, absent: str | None) -> None:
    # Optional (specs/configuration.md, "Optional sections"): absent and `null` alike are off.
    config = _composed()
    if absent is None:
        config["asr"] = None
    else:
        del config["asr"]
    cfg = _load(tmp_path, config)
    assert cfg.asr_engine is None
    assert cfg.wica.agent.provider == "anthropic"


def test_asr_that_is_not_an_object_raises(tmp_path: Path) -> None:
    config = _composed()
    config["asr"] = ["deepgram"]
    with pytest.raises(ValueError, match="section 'asr' must be an object"):
        _load(tmp_path, config)


def test_asr_still_wrapped_in_engine_is_refused(tmp_path: Path) -> None:
    # Until 2026-11-24 `asr` was `{"engine": {…}}`; the section is now the engine block itself
    # (specs/configuration.md), and asr-engine's own loader refuses the old wrapper by name.
    config = _composed()
    config["asr"] = {"engine": config["asr"]}
    with pytest.raises(ValueError, match="engine"):
        _load(tmp_path, config)


def test_unknown_top_level_section_names_it(tmp_path: Path) -> None:
    # The whole point of the `sections` argument: a section the calling embodiment does not declare
    # is an error, not a silent no-op — a robot's config handed to the computer's loader fails here.
    config = {**_composed(), "reachy": {"backend": "fake"}}
    with pytest.raises(ValueError, match="unknown section.*'reachy'"):
        _load(tmp_path, config, sections=SHARED_SECTIONS)


def test_section_inside_the_callers_set_is_accepted(tmp_path: Path) -> None:
    # The same document, with 'reachy' in the section set, loads — the embodiment parses it itself.
    cfg = _load(tmp_path, {**_composed(), "reachy": {"backend": "fake"}})
    assert cfg.wica.agent.provider == "anthropic"


def test_loads_with_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The config references keys by env-var name; with those vars unset, loading must still
    # succeed (no env read at load — the "app opens without credentials" contract).
    for var in ("WICA_ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    cfg = _load(tmp_path, _composed())
    assert cfg.wica.agent.api_key_env == "WICA_ANTHROPIC_API_KEY"


def test_engine_validation_error_propagates(tmp_path: Path) -> None:
    # A field-level error is the engine's own, raised as-is — not swallowed or re-wrapped here.
    config = _composed()
    config["asr"]["dictation_default_segmentation_mode"] = "nonsense"
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
        threshold=0.55,
        fps=10.0,
        max_hands=2,
        hold_s=0.3,
        release_hold_s=0.5,
        device=None,
    )


def test_vision_fields_are_carried(tmp_path: Path) -> None:
    section = {
        "threshold": 0.7,
        "fps": 20,
        "max_hands": 1,
        "hold_s": 0,
        "release_hold_s": 1,
        "device": "cpu",
    }
    cfg = _load(tmp_path, {**_composed(), "vision": section})
    assert cfg.vision is not None
    assert cfg.vision == VisionConfig(
        threshold=0.7,
        fps=20.0,
        max_hands=1,
        hold_s=0.0,
        release_hold_s=1.0,
        device="cpu",
    )
    assert isinstance(cfg.vision.hold_s, float)  # the integer 0 is carried as a float


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ({"threshold": 1.5}, "vision 'threshold'"),
        ({"threshold": "high"}, "vision 'threshold'"),
        ({"threshold": True}, "vision 'threshold'"),
        ({"fps": 0}, "vision 'fps'"),
        ({"fps": -5}, "vision 'fps'"),
        ({"max_hands": 0}, "vision 'max_hands'"),
        ({"max_hands": 1.5}, "vision 'max_hands'"),
        ({"max_hands": 2.0}, "vision 'max_hands'"),
        ({"max_hands": True}, "vision 'max_hands'"),
        ({"max_hands": "two"}, "vision 'max_hands'"),
        ({"hold_s": -0.1}, "vision 'hold_s'"),
        ({"hold_s": "0.5"}, "vision 'hold_s'"),
        ({"hold_s": True}, "vision 'hold_s'"),
        ({"release_hold_s": -0.1}, "vision 'release_hold_s'"),
        ({"release_hold_s": "0.5"}, "vision 'release_hold_s'"),
        ({"device": 3}, "vision 'device'"),
    ],
)
def test_vision_invalid_value_names_the_field(
    tmp_path: Path, section: dict, field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        _load(tmp_path, {**_composed(), "vision": section})


@pytest.mark.parametrize("enabled", [True, False])
def test_vision_enabled_is_refused_with_how_to_fix_it(
    tmp_path: Path, enabled: bool
) -> None:
    # The section is the switch now: `enabled` is refused, not ignored — an ignored
    # `enabled: false` would turn hand signs on.
    with pytest.raises(ValueError) as caught:
        _load(tmp_path, {**_composed(), "vision": {"enabled": enabled}})
    message = str(caught.value)
    assert "vision.enabled is no longer a field" in message
    assert "Remove 'enabled'" in message
    assert "remove the whole vision section" in message


def test_vision_unknown_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'mirror'"):
        _load(tmp_path, {**_composed(), "vision": {"mirror": True}})


def test_vision_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="vision must be an object"):
        _load(tmp_path, {**_composed(), "vision": "on"})


def test_a_null_required_section_is_missing(tmp_path: Path) -> None:
    # app-setup's `used_config` gives every declared section, an absent one as `None`.
    with pytest.raises(ValueError, match="missing required section.*'wica'"):
        _load(tmp_path, {**_composed(), "wica": None})
