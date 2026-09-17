"""The Reachy Mini entry point: `wica-interaction-reachy --config <file>`."""

from __future__ import annotations

from wica_interaction.config import AppConfig
from wica_interaction.embodiment import Embodiment
from wica_interaction.reachy.config import load_config
from wica_interaction.reachy.embodiment import ReachyEmbodiment
from wica_interaction.system import run_app


def _load(path: str) -> tuple[AppConfig, Embodiment]:
    config = load_config(path)
    return config.app, ReachyEmbodiment(config)


def main() -> None:
    run_app(description="Talk to a WICA-driven Reachy Mini.", load=_load)


if __name__ == "__main__":
    main()
