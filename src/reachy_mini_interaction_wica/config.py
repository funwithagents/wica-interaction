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


@dataclass
class AppConfig:
    """The whole app's config: one live engine config per section, plus the robot (and voice)."""

    wica: WicaConfig
    asr_engine: ASREngineConfig
    reachy: ReachyMiniConfig


def load_config(path: str) -> AppConfig:
    """Load the composed app config at *path* into an ``AppConfig``.

    Reads one JSON object with the required `wica` and `asr` sections (and the optional `reachy`
    section) and delegates each to its own ``from_dict`` constructor (no temp files). WICA's section is
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
