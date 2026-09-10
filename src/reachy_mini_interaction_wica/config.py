"""Composed application config: one JSON file with `wica`/`tts`/`asr` sections.

Each section holds exactly the document that engine's own loader expects, and is handed to that
engine's `from_dict` constructor so all field-level validation and defaults come from the engine,
not from here. See specs/configuration.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from asr_engine.config import ASREngineConfig
from tts_engine.config import TTSEngineConfig
from wica.config import WicaConfig

_REQUIRED_SECTIONS = ("wica", "tts", "asr")


@dataclass
class AppConfig:
    """The whole app's config: one live engine config per section."""

    wica: WicaConfig
    tts_engine: TTSEngineConfig
    asr_engine: ASREngineConfig


def load_config(path: str) -> AppConfig:
    """Load the composed app config at *path* into an ``AppConfig``.

    Reads one JSON object with `wica`, `tts`, and `asr` sections and delegates each to its engine's
    ``from_dict`` constructor (no temp files). WICA's section is located against the config file's own
    directory so a relative ``system_prompt_file`` resolves there, exactly as ``WicaConfig.from_json``
    would. No env var or prompt file is read here — that stays deferred to engine build — so this
    loads even with credentials unset.

    Raises ``ValueError`` if the file is missing, is not a JSON object, or lacks a required section.
    Field-level errors propagate from the engine loaders unchanged.
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
    )


def _engine_block(section: Any, *, name: str, path: str) -> dict[str, Any]:
    """Return the `engine` block of a `tts`/`asr` section, or raise a clear error about its shape."""
    if not isinstance(section, dict) or "engine" not in section:
        raise ValueError(
            f"config file {path!r}: section {name!r} must be an object with an 'engine' block"
        )
    return section["engine"]
