"""Composed application config: one JSON file with `wica`/`tts`/`asr` (+ optional `reachy`) sections.

Each engine section holds exactly the document that engine's own loader expects, and is handed to
that engine's `from_dict` constructor so all field-level validation and defaults come from the
engine, not from here. The one exception is `reachy`, which is not an engine with its own loader: it
selects the robot backend handed to `reachy-mini-bridge`'s ``ReachyMiniApi``, so it is parsed here
into a small app-owned ``ReachyConfig``. See specs/configuration.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, get_args

from asr_engine.config import ASREngineConfig
from tts_engine.config import TTSEngineConfig
from wica.config import WicaConfig

_REQUIRED_SECTIONS = ("wica", "tts", "asr")

# The robot backends ReachyMiniApi accepts. The Literal lets pyright check callers; it cannot guard a
# JSON file, so load_config still validates at runtime — against the same set, derived from the type.
Backend = Literal["real", "sim", "fake"]
_REACHY_BACKENDS: tuple[Backend, ...] = get_args(Backend)


@dataclass
class ReachyConfig:
    """Which robot backend the app hands to ``ReachyMiniApi``.

    ``backend`` is one of ``real`` (default), ``sim`` (upstream MuJoCo mockup), or ``fake`` (the
    bridge's in-package stand-in — no daemon, no hardware). Not an engine with its own loader, so it
    is parsed here rather than delegated.
    """

    backend: Backend = "real"


@dataclass
class AppConfig:
    """The whole app's config: one live engine config per section, plus the robot backend."""

    wica: WicaConfig
    tts_engine: TTSEngineConfig
    asr_engine: ASREngineConfig
    reachy: ReachyConfig


def load_config(path: str) -> AppConfig:
    """Load the composed app config at *path* into an ``AppConfig``.

    Reads one JSON object with the required `wica`, `tts`, and `asr` sections (and the optional
    `reachy` section) and delegates each engine section to its engine's ``from_dict`` constructor (no
    temp files). WICA's section is located against the config file's own directory so a relative
    ``system_prompt_file`` resolves there, exactly as ``WicaConfig.from_json`` would. No env var or
    prompt file is read here — that stays deferred to engine build — so this loads even with
    credentials unset. The optional `reachy` section is parsed here into a ``ReachyConfig`` (absent ⇒
    ``backend="real"``).

    Raises ``ValueError`` if the file is missing, is not a JSON object, lacks a required section, or
    names an unknown ``reachy.backend``. Field-level errors propagate from the engine loaders
    unchanged.
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
        tts_engine=TTSEngineConfig.from_dict(_engine_block(data["tts"], name="tts", path=path)),
        asr_engine=ASREngineConfig.from_dict(_engine_block(data["asr"], name="asr", path=path)),
        reachy=_reachy_config(data.get("reachy"), path=path),
    )


def _reachy_config(section: Any, *, path: str) -> ReachyConfig:
    """Parse the optional `reachy` section (absent ⇒ ``backend="real"``), validating the backend."""
    if section is None:
        return ReachyConfig()
    if not isinstance(section, dict):
        raise ValueError(f"config file {path!r}: section 'reachy' must be an object")  # noqa: TRY004
    backend = section.get("backend", "real")
    if backend not in _REACHY_BACKENDS:
        raise ValueError(
            f"config file {path!r}: reachy.backend must be one of "
            f"{', '.join(repr(b) for b in _REACHY_BACKENDS)}, got {backend!r}"
        )
    return ReachyConfig(backend=backend)


def _engine_block(section: Any, *, name: str, path: str) -> dict[str, Any]:
    """Return the `engine` block of a `tts`/`asr` section, or raise a clear error about its shape."""
    if not isinstance(section, dict) or "engine" not in section:
        raise ValueError(
            f"config file {path!r}: section {name!r} must be an object with an 'engine' block"
        )
    return section["engine"]
