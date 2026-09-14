# reachy-mini-interaction-wica

An interaction application for the [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) robot, built on the **WICA** agentic framework. A person speaks to the robot; a WICA agent reasons over the conversation and the robot speaks and acts back — replying out loud, playing emotions, following the person's face, and managing its motors.

Speech and robot control go through [`reachy-mini-bridge`](../reachy-mini-bridge) (`ReachyMiniApi`): the bridge plays synthesized speech through the robot speaker, exposes the robot's echo-cancelled microphone as a stream the app runs its own ASR over, and offers the robot's action verbs. The app runs everything in-process (no MCP servers) behind a small Gradio UI: WICA's own observability panels (the reusable `wica.contrib.gradio` components, via the `wica[gradio]` extra) next to the app's live ASR panel.

## Documentation

- **[specs/_overview.md](specs/_overview.md)** — global view: what the app is, the end-to-end data flow, the two modes, and how the modules fit. Start here.
- **[specs/_index.md](specs/_index.md)** — index of the specs and their status.
- **[plans/_index.md](plans/_index.md)** — implementation plans and their status.
- **[AGENTS.md](AGENTS.md)** — operating manual: specs/plans/status discipline, the project map, commands, verification.

## Sibling dependencies

Consumed as local path dependencies (in the parent folder), per [specs/project.md](specs/project.md):

- `wica` (`../wica-framework`) — the agentic framework.
- `asr-engine` (`../asr-engine`) — speech-to-text over the robot's mic stream.
- `reachy-mini-bridge` (`../reachy-mini-bridge`) — the robot's interaction API: connects to the robot, manages the daemon, and builds the voice itself from the config's `reachy.tts` block (a `tts-engine` `engine` block — `reachy-mini-bridge[tts]` pulls `tts-engine`).

## Development

```
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest
```

Run the app (opens the Gradio UI); `config.example.json` is a template, not a config to run unmodified — copy it, fill in the API keys, and point `reachy.backend` at what you're driving. As committed it targets a **headful `sim`** daemon (`"reachy": {"backend": "sim", "daemon": {"spawn": "auto", "headless": false}}`), which the bridge spawns itself and opens the MuJoCo viewer in (needs `mjpython` on `PATH` and an unlocked GUI session — see [reachy-mini-bridge docs/running-the-sim-daemon.md](../reachy-mini-bridge/docs/running-the-sim-daemon.md)); set `reachy.daemon.headless` to `true` for a windowless daemon. For the offline `fake` backend or the physical robot, change `reachy.backend` to `"fake"` / `"real"` **and delete the `reachy.daemon` block** (daemon spawning is valid only with `sim`, so the loader rejects it otherwise) — keep `reachy.tts`, or the robot comes up without a voice:

```
uv run reachy-mini-interaction-wica --config config.example.json
```
