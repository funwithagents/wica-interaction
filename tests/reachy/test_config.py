"""Functional tests for the Reachy Mini embodiment's config loader (specs/reachy-embodiment.md).

The shared sections are tested in ``tests/test_config.py``; what is pinned here is this loader's
own section — the verbatim bridge config — and that it rejects a section belonging to another body.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reachy_mini_bridge.config import ReachyMiniConfig

from wica_interaction.reachy.config import ReachyConfig, load_config


def _composed() -> dict:
    """A full, valid reachy config: the shared sections plus a `reachy` block."""
    return {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "api_key_env": "WICA_ANTHROPIC_API_KEY",
                "system_prompt_file": "prompts/reachy.md",
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
        "reachy": {
            "tts": {
                "module": {"type": "pocket", "voice": "george", "device": "auto"},
                "player": {"device": None},
            }
        },
    }


def _write(tmp_path: Path, config: dict, name: str = "app.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def test_happy_path_yields_the_shared_config_and_a_bridge_config(
    tmp_path: Path,
) -> None:
    cfg = load_config(_write(tmp_path, _composed()))

    assert isinstance(cfg, ReachyConfig)
    # The shared sections live under `.app`, untouched by this loader.
    assert cfg.app.wica.agent.provider == "anthropic"
    assert cfg.app.asr_engine.module.type == "deepgram_v2"

    # The `reachy` section is a verbatim bridge config, delegated to ReachyMiniConfig.from_dict.
    assert isinstance(cfg.reachy, ReachyMiniConfig)
    assert cfg.reachy.backend == "real"
    assert cfg.reachy.tts is not None
    assert cfg.reachy.tts["module"]["type"] == "pocket"
    # The module block is carried through verbatim, extra fields intact.
    assert cfg.reachy.tts["module"]["voice"] == "george"


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


def test_a_computer_section_is_rejected(tmp_path: Path) -> None:
    # A computer file handed to the reachy command fails on its own sections, rather than being
    # silently ignored (specs/configuration.md, "Each embodiment loads its own file").
    config = _composed()
    config["tts"] = {"engine": {}}
    with pytest.raises(ValueError, match="unknown section.*'tts'"):
        load_config(_write(tmp_path, config))


# --- Face detection and head tracking: the bridge's `face_detection` block ------------------------


def test_a_face_detection_block_reaches_the_bridge_config(tmp_path: Path) -> None:
    config = _composed()
    config["reachy"]["face_detection"] = {
        "detector": "yunet",
        "enabled": True,
        "width": 640,
        "target_fps": 5,
    }
    config["reachy"]["motion"] = {"tracking": True}
    cfg = load_config(_write(tmp_path, config))
    assert cfg.reachy.face_detection.detector == "yunet"
    assert cfg.reachy.face_detection.enabled is True
    assert cfg.reachy.face_detection.width == 640
    assert cfg.reachy.face_detection.target_fps == 5
    assert cfg.reachy.motion.tracking is True


def test_tracking_without_a_detector_is_the_bridges_config_error(
    tmp_path: Path,
) -> None:
    """The bridge's head tracker needs a detector (detection is opt-in since 2026-09-29): tracking
    on with no `face_detection.detector` is rejected at load, naming both fields, as a ValueError."""
    config = _composed()
    config["reachy"]["motion"] = {"tracking": True}
    with pytest.raises(ValueError, match="motion.tracking.*face_detection.detector"):
        load_config(_write(tmp_path, config))


def test_the_committed_template_loads() -> None:
    """The shipped template must open: it turns tracking on, so it must name a detector."""
    cfg = load_config(
        str(Path(__file__).resolve().parents[2] / "config.reachy.example.json")
    )
    assert cfg.reachy.face_detection.detector == "yunet"
    assert cfg.reachy.motion.tracking is True
