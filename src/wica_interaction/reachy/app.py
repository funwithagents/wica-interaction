"""The Reachy Mini app, `wica-interaction-reachy`: its declaration for app-setup and its entry point.

The app runs from its folder (`~/.config/wica-interaction-reachy`, or `--folder`), whose
`config.json` holds the sections below, each described to the Setup tab by its owner's setup
contract (specs/setup.md). `wica` and `reachy` are required; `asr` and `vision` are optional —
absent means off — each with the starting document the Setup tab writes when the user adds it
(`STARTING_DOCUMENTS`). `SECTIONS`, `APP` and `STARTING_DOCUMENTS` are public: a host built on this
app composes its own `App` from the same sections.
"""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import asr_engine.setup
import reachy_mini_bridge.setup
import wica.setup
from app_setup.app import App, Section

from wica_interaction.reachy.config import load_config
from wica_interaction.reachy.embodiment import ReachyEmbodiment
from wica_interaction.setup import VISION, VISION_START
from wica_interaction.system import copy_persona, run_app

# The default config and the persona, shipped in the package (specs/setup.md, "The defaults ship
# inside the package").
_DEFAULT = files("wica_interaction.reachy") / "default"
PERSONA_FILE = "reachy.md"
DEFAULT_CONFIG: dict[str, Any] = json.loads(
    (_DEFAULT / "config.json").read_text(encoding="utf-8")
)

# What each optional section starts from when the user adds it (specs/configuration.md, "Optional
# sections"): speech recognition as the default config has it, hand signs at every default.
STARTING_DOCUMENTS: dict[str, dict[str, Any]] = {
    "asr": copy.deepcopy(DEFAULT_CONFIG["asr"]),
    "vision": copy.deepcopy(VISION_START),
}

SECTIONS: tuple[Section, ...] = (
    Section("wica", wica.setup, title="Agent"),
    Section(
        "asr",
        asr_engine.setup,
        title="Speech recognition",
        optional=True,
        default=STARTING_DOCUMENTS["asr"],
    ),
    Section("reachy", reachy_mini_bridge.setup, title="Reachy Mini"),
    Section(
        "vision",
        VISION,
        title="Hand signs",
        optional=True,
        default=STARTING_DOCUMENTS["vision"],
    ),
)

APP = App(
    name="wica-interaction-reachy",
    title="WICA Interaction — Reachy Mini",
    host="wica-interaction",
    sections=SECTIONS,
    default_config=DEFAULT_CONFIG,
    autostart=True,
)


def ensure_persona(folder: Path) -> Path:
    """The persona in the app's folder, copied there from the package when the folder has none.
    Never overwritten: the file in the folder is the user's."""
    return copy_persona(_DEFAULT / PERSONA_FILE, folder / PERSONA_FILE)


def main() -> None:
    run_app(
        APP,
        load=load_config,
        embodiment=ReachyEmbodiment,
        ensure_persona=ensure_persona,
    )


if __name__ == "__main__":
    main()
