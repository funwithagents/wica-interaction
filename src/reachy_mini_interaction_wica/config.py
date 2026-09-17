"""Composed application config: one JSON file with `wica`/`asr` (+ optional `reachy`) sections.

Each section holds exactly the document its owner's own loader expects, and is handed to that
loader's `from_dict` constructor so all field-level validation and defaults come from the owner, not
from here. `wica` and `asr` are engine sections; `reachy` is the whole robot backend, including the
voice (`reachy.tts`) — a verbatim `reachy-mini-bridge` config, delegated to
`reachy_mini_bridge.config.ReachyMiniConfig.from_dict`. See specs/configuration.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from asr_engine.config import ASREngineConfig
from reachy_mini_bridge.config import ReachyMiniConfig
from wica.config import WicaConfig

_REQUIRED_SECTIONS = ("wica", "asr")

_VISION_KEYS = frozenset({"enabled", "threshold", "fps", "device"})


@dataclass(frozen=True)
class VisionConfig:
    """The optional `vision` section: the fist-gesture input over the robot camera (see
    specs/gesture-input.md). `vision-modules` has no config loader of its own, so this is the app's
    own small schema — its defaults are the library's (threshold 0.55) and the app's (5 fps for the
    camera poll, hand stage, and classifier alike; `device` None lets the classifier pick
    mps → cuda → cpu). `enabled` is the switch, off by default: the section can stay in the file
    (tuned) with vision off, and flipping it on is a one-key edit."""

    enabled: bool = False
    threshold: float = 0.55
    fps: float = 5.0
    device: str | None = None

    @classmethod
    def from_dict(cls, data: Any, *, path: str) -> VisionConfig:
        if not isinstance(data, dict):
            # ValueError (not TypeError) keeps one uniform error surface for a malformed file.
            message = f"config file {path!r}: section 'vision' must be an object"
            raise ValueError(message)  # noqa: TRY004
        unknown = sorted(set(data) - _VISION_KEYS)
        if unknown:
            raise ValueError(
                f"config file {path!r}: section 'vision' has unknown key(s): "
                f"{', '.join(repr(k) for k in unknown)}"
            )
        enabled = data.get("enabled", cls.enabled)
        threshold = data.get("threshold", cls.threshold)
        fps = data.get("fps", cls.fps)
        device = data.get("device", cls.device)
        if not isinstance(enabled, bool):
            message = f"config file {path!r}: vision.enabled must be a boolean"
            raise ValueError(message)  # noqa: TRY004
        if not _is_number(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"config file {path!r}: vision.threshold must be a number in [0, 1]"
            )
        if not _is_number(fps) or fps <= 0:
            raise ValueError(f"config file {path!r}: vision.fps must be a number > 0")
        if device is not None and not isinstance(device, str):
            raise ValueError(
                f"config file {path!r}: vision.device must be a string or null"
            )
        return cls(
            enabled=enabled, threshold=float(threshold), fps=float(fps), device=device
        )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class AppConfig:
    """The whole app's config: one live engine config per section, plus the robot (and voice), plus
    the optional vision section (`None`, or `enabled` false ⇒ no gesture input)."""

    wica: WicaConfig
    asr_engine: ASREngineConfig
    reachy: ReachyMiniConfig
    vision: VisionConfig | None = None


def load_config(path: str) -> AppConfig:
    """Load the composed app config at *path* into an ``AppConfig``.

    Reads one JSON object with the required `wica` and `asr` sections (and the optional `reachy` and
    `vision` sections) and delegates each to its own ``from_dict`` constructor (no temp files). WICA's section is
    located against the config file's own directory so a relative ``system_prompt_file`` resolves
    there, exactly as ``WicaConfig.from_json`` would. No env var or prompt file is read here — that
    stays deferred to engine build — so this loads even with credentials unset. The optional `reachy`
    section is a verbatim ``reachy-mini-bridge`` config (absent ⇒ that bridge's own defaults: the
    `real` backend, no voice).

    Raises ``ValueError`` if the file is missing, is not a JSON object, or lacks a required section.
    Field-level errors propagate from the engine loaders unchanged (the bridge's ``ConfigError`` is a
    ``ValueError`` subclass, so this holds for `reachy` too).
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise ValueError(f"could not read config file {path!r}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in config file {path!r}: {exc}") from exc

    if not isinstance(data, dict):
        # ValueError (not TypeError) keeps one uniform error surface for any malformed config file.
        raise ValueError(f"config file {path!r} must contain a JSON object")  # noqa: TRY004

    missing = [key for key in _REQUIRED_SECTIONS if key not in data]
    if missing:
        raise ValueError(
            f"config file {path!r} is missing required section(s): "
            f"{', '.join(repr(k) for k in missing)}"
        )

    base_dir = os.path.dirname(path)

    return AppConfig(
        wica=WicaConfig.from_dict(data["wica"], base_dir=base_dir),
        asr_engine=ASREngineConfig.from_dict(
            _engine_block(data["asr"], name="asr", path=path)
        ),
        reachy=_reachy_config(data.get("reachy"), path=path),
        vision=(
            VisionConfig.from_dict(data["vision"], path=path)
            if data.get("vision") is not None
            else None
        ),
    )


def _reachy_config(section: Any, *, path: str) -> ReachyMiniConfig:
    """Parse the optional `reachy` section (absent ⇒ the bridge's own defaults), delegated to the
    bridge's loader for every field-level validation rule."""
    if section is None:
        return ReachyMiniConfig()
    if not isinstance(section, dict):
        raise ValueError(f"config file {path!r}: section 'reachy' must be an object")  # noqa: TRY004
    return ReachyMiniConfig.from_dict(section)


def _engine_block(section: Any, *, name: str, path: str) -> dict[str, Any]:
    """Return the `engine` block of an engine section, or raise a clear error about its shape."""
    if not isinstance(section, dict) or "engine" not in section:
        raise ValueError(
            f"config file {path!r}: section {name!r} must be an object with an 'engine' block"
        )
    return section["engine"]
