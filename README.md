# reachy-mini-interaction-wica

An interaction application for the [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) robot, built on the **WICA** agentic framework. A person speaks to the robot; a WICA agent reasons over the conversation and the robot speaks and acts back — replying out loud, playing emotions, following the person's face, and managing its motors.

Speech and robot control go through [`reachy-mini-bridge`](../reachy-mini-bridge) (`ReachyMiniApi`): the bridge plays synthesized speech through the robot speaker, exposes the robot's echo-cancelled microphone as a stream the app runs its own ASR over, and offers the robot's action verbs. The app runs everything in-process (no MCP servers) behind a small Gradio UI with WICA's observability panels.

## Documentation

- **[specs/_overview.md](specs/_overview.md)** — global view: what the app is, the end-to-end data flow, the two modes, and how the modules fit. Start here.
- **[specs/_index.md](specs/_index.md)** — index of the specs and their status.
- **[plans/_index.md](plans/_index.md)** — implementation plans and their status.
- **[AGENTS.md](AGENTS.md)** — operating manual: specs/plans/status discipline, the project map, commands, verification.

## Sibling dependencies

Consumed as local path dependencies (in the parent folder), per [specs/project.md](specs/project.md):

- `wica` (`../wica-framework`) — the agentic framework.
- `asr-engine` (`../asr-engine`) — speech-to-text over the robot's mic stream.
- `reachy-mini-bridge` (`../reachy-mini-bridge`) — the robot's interaction API (which itself wraps `tts-engine` for the voice).
- `tts-engine` (`../tts-engine`) — pulled directly only for its config type: the app builds the `TTSEngineConfig` it hands to the bridge's synthesizer.

## Development

```
uv sync --dev
uv run ruff check .
uv run pyright
uv run pytest
```

Run the app (opens the Gradio UI; use `reachy.backend = "fake"` or `"sim"` in the config for offline development):

```
uv run reachy-mini-interaction-wica --config config.example.json
```
