"""The computer entry point: `wica-interaction-computer --config <file>`."""

from __future__ import annotations

from wica_interaction.computer.config import load_config
from wica_interaction.computer.embodiment import ComputerEmbodiment
from wica_interaction.config import AppConfig
from wica_interaction.embodiment import Embodiment
from wica_interaction.system import run_app


def _load(path: str) -> tuple[AppConfig, Embodiment]:
    config = load_config(path)
    return config.app, ComputerEmbodiment(config)


def main() -> None:
    run_app(description="Talk to a WICA voice assistant.", load=_load)


if __name__ == "__main__":
    main()
