"""The Reachy Mini embodiment's config: the shared sections plus a verbatim bridge config.

``load_config`` reads the file once, validates the shared part through
``wica_interaction.config.load_app_config`` (which also rejects any section outside this
embodiment's set — a ``tts`` or ``camera`` section in a reachy file is an error, not a silent
no-op), and parses the one section of its own: ``reachy``, handed whole to the bridge's
``ReachyMiniConfig.from_dict`` so every field, default and validation rule — the voice included —
is the bridge's own. See specs/reachy-embodiment.md, "Configuration".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachy_mini_bridge.config import ReachyMiniConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    load_app_config,
    read_config_file,
)

# Every top-level key a reachy config file may carry.
SECTIONS = SHARED_SECTIONS | {"reachy"}


@dataclass
class ReachyConfig:
    """This embodiment's whole config: the shared sections plus the robot backend (voice included)."""

    app: AppConfig
    reachy: ReachyMiniConfig


def load_config(path: str) -> ReachyConfig:
    """Load the composed config at *path* for the Reachy Mini embodiment.

    Raises ``ValueError`` if the file is missing, is not a JSON object, lacks a required section, or
    carries a section this embodiment does not know. Field-level errors propagate from the owners'
    loaders unchanged (the bridge's ``ConfigError`` is a ``ValueError`` subclass, so the one error
    type holds for ``reachy`` too).
    """
    data = read_config_file(path)
    app = load_app_config(data, path=path, sections=SECTIONS)
    return ReachyConfig(app=app, reachy=_reachy_config(data.get("reachy"), path=path))


def _reachy_config(section: Any, *, path: str) -> ReachyMiniConfig:
    """Parse the optional `reachy` section (absent ⇒ the bridge's own defaults: the `real` backend,
    upstream defaults, no voice), delegated to the bridge's loader for every field-level rule."""
    if section is None:
        return ReachyMiniConfig()
    if not isinstance(section, dict):
        raise ValueError(f"config file {path!r}: section 'reachy' must be an object")  # noqa: TRY004
    return ReachyMiniConfig.from_dict(section)
