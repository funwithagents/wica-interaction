# wica-interaction

One interaction application built on the **WICA** agentic framework, in which a person **speaks** to an AI agent and the agent **speaks — and, given a body, acts — back**. The same program runs in different **embodiments**, chosen by the command you launch:

- **`wica-interaction-computer`** — the agent on the machine it runs on: a local microphone, the speakers (a [`tts-engine`](../tts-engine) voice), optionally a webcam for hand signs. A plain voice assistant.
- **`wica-interaction-reachy`** — the agent in a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini): it speaks out of the robot's speaker, listens through the robot's echo-cancelled mic, sees through the robot's camera, plays emotions and manages its motors, all through [`reachy-mini-bridge`](../reachy-mini-bridge)'s `ReachyMiniApi`.

Everything WICA-side is shared — the World and its `speech_input` entry, the dictation commands, the hand-sign entries, the transcript and observability panels, the whole lifecycle. What varies is captured by one small protocol, the [Embodiment](specs/embodiment.md): where audio comes in and goes out, what the agent can act with, and what it sees through. Everything runs in-process (no MCP servers) behind a small Gradio UI: WICA's own observability panels (the reusable `wica.contrib.gradio` components) next to the app's live ASR and Vision panel.

## Documentation

- **[specs/_overview.md](specs/_overview.md)** — global view: what the app is, the end-to-end data flow, the embodiments, and how the modules fit. Start here.
- **[specs/_index.md](specs/_index.md)** — index of the specs and their status.
- **[plans/_index.md](plans/_index.md)** — implementation plans and their status.
- **[AGENTS.md](AGENTS.md)** — operating manual: specs/plans/status discipline, the project map, commands, verification.

## Sibling dependencies

Consumed as local path dependencies (in the parent folder), per [specs/project.md](specs/project.md):

- `wica` (`../wica-framework`) — the agentic framework, plus its reusable Gradio panels.
- `asr-engine` (`../asr-engine`) — speech-to-text over whichever mic the embodiment provides.
- `tts-engine` (`../tts-engine`) — the computer's voice, and the voice the bridge builds for the robot.
- `reachy-mini-bridge` (`../reachy-mini-bridge`) — the robot's interaction API: connects to the robot, manages the daemon, and builds the voice itself from the config's `reachy.tts` block.
- `vision-modules` (`../vision-modules`) — the hand stage + HaGRID gesture classifier behind the hand-sign input: each hand's sign (`stop`, `like`, `dislike`, `ok`, `one` … `five`), once it has held for `vision.hold_s` (0.5 s), reaches the agent as its own World entry (see [specs/gesture-input.md](specs/gesture-input.md)). The models download on first run (a MediaPipe bundle and a ~340 MB ViT); enable it with a `vision` config section.

> **Prerequisite.** `reachy-mini-bridge` must name `tts-engine` by plain name in its own metadata (not a git URL): uv refuses two different URLs for one package, and this project consumes `../tts-engine` as a path source.

## Extras

What every embodiment needs is a base dependency; what only one embodiment, provider or optional feature needs is an extra, so a deployment installs only what it runs ([specs/project.md](specs/project.md)):

| Extra | Pulls | For |
|---|---|---|
| *(base)* | `wica[gradio]`, `asr-engine`, `vision-modules`, `gradio`, `numpy` | the lifecycle, the UI, speech in, the vision plumbing |
| `computer` | `tts-engine` | the computer embodiment's voice |
| `audio-processing` | `aec-audio-processing`, `samplerate` | the computer's echo cancellation / noise suppression |
| `reachy` | `reachy-mini-bridge` | the Reachy Mini embodiment (pulls the upstream `reachy-mini` SDK) |
| `vision` | `vision-modules[hand]` | the hand-sign input, either embodiment |
| `wica-anthropic` | `wica[anthropic]` | the agent provider the example configs select |
| `asr-deepgram` | `asr-engine[deepgram]` | the ASR provider the example configs select |
| `tts-elevenlabs` | `tts-engine[elevenlabs]` | the cloud voice — both embodiments |
| `tts-pocket` | `tts-engine[pocket]` | the local voice (torch + a first-run weight download) — both embodiments |
| `all` | every extra above | the dev environment (`uv sync --dev` installs it) |

A computer-only install, for instance, is `uv sync --no-dev --extra computer --extra wica-anthropic --extra asr-deepgram --extra tts-elevenlabs` — no robot SDK anywhere in the graph.

## Development

```
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest
```

## Running

One config file stands up each embodiment, and each committed example is a **template** — copy it, fill in the API keys, and adapt it; `--config` is required, with no default.

**The computer** ([config.computer.example.json](config.computer.example.json)): the agent's key (`WICA_ANTHROPIC_API_KEY`), ASR's (`DEEPGRAM_API_KEY`) and the voice's (`ELEVENLABS_API_KEY`). Echo cancellation is on by default, so the agent does not hear itself through the speakers; `vision.enabled` is off — turn it on (and set `camera.device`) to read hand signs through a webcam.

```
uv run wica-interaction-computer --config config.computer.example.json
```

**The Reachy Mini** ([config.reachy.example.json](config.reachy.example.json)): the agent's key and ASR's (the committed voice is the local `pocket` model, which needs none, downloading its weights on first run). As committed it targets a **headful `sim`** daemon (`"reachy": {"backend": "sim", "daemon": {"spawn": "auto", "headless": false}}`), which the bridge spawns itself and opens the MuJoCo viewer in (needs `mjpython` on `PATH` and an unlocked GUI session — see [reachy-mini-bridge docs/running-the-sim-daemon.md](../reachy-mini-bridge/docs/running-the-sim-daemon.md)); set `reachy.daemon.headless` to `true` for a windowless daemon. For the physical robot, change `reachy.backend` to `"real"` — the `reachy.daemon` block then spawns the daemon of a robot plugged in over USB (a wireless robot runs its own daemon: set `spawn` to `"never"` and point `reachy.robot.host` at it). For the offline `fake` backend, change it to `"fake"` **and delete the `reachy.daemon` block** (`fake` has no daemon, so the loader rejects spawning there). Either way keep `reachy.tts`, or the robot comes up without a voice:

```
uv run wica-interaction-reachy --config config.reachy.example.json
```

Whichever you launch, the app **opens even when something could not be built** and says what to fix in a banner: with no body the agent still reasons over typed text, with a body but no voice `say` stays wired and reports a TTS error (on either embodiment), and with no ASR key the typed-text box is the way in.
