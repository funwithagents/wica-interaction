"""The Reachy Mini embodiment's config: the shared sections plus a verbatim bridge config.

``load_config`` takes the config document (the app's `config.json`, as app-setup read it) and the
app's folder, validates the shared part through ``wica_interaction.config.load_app_config`` (which
also rejects any section outside this embodiment's set — a ``tts`` or ``camera`` section in a robot
config is an error, not a silent no-op), and parses the one section of its own: ``reachy``, handed
whole to the bridge's ``ReachyMiniConfig.from_dict`` with the folder as ``base_dir``, so every
field, default, validation rule and relative path — the voice included — is the bridge's own. See
specs/reachy-embodiment.md, "Configuration".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reachy_mini_bridge.config import ReachyMiniConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    load_app_config,
    section_object,
)

# Every top-level key a robot config may carry.
SECTIONS = SHARED_SECTIONS | {"reachy"}


@dataclass
class ReachyConfig:
    """This embodiment's whole config: the shared sections plus the robot backend (voice included)."""

    app: AppConfig
    reachy: ReachyMiniConfig


def load_config(data: Mapping[str, Any], *, base_dir: str | Path) -> ReachyConfig:
    """Load the composed config document *data* for the Reachy Mini embodiment, relative paths
    against *base_dir* (the app's folder).

    Raises ``ValueError`` if a required section is missing or the document carries a section this
    embodiment does not know. Field-level errors propagate from the owners' loaders unchanged (the
    bridge's ``ConfigError`` is a ``ValueError`` subclass, so the one error type holds for
    ``reachy`` too).
    """
    app = load_app_config(data, base_dir=base_dir, sections=SECTIONS)
    section = data.get("reachy")
    # Absent ⇒ the bridge's own defaults: the `real` backend, upstream defaults, no voice.
    reachy = (
        ReachyMiniConfig()
        if section is None
        else ReachyMiniConfig.from_dict(
            section_object(section, name="reachy"), base_dir=base_dir
        )
    )
    return ReachyConfig(app=app, reachy=reachy)
