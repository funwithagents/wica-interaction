# wica-interaction

**An interaction application on the [WICA](https://github.com/funwithagents/wica-framework) agentic framework: a person speaks to an AI agent, and the agent speaks back, and acts back when it has a body.** The same program runs in different **embodiments**, chosen by the command you launch:

- **`wica-interaction-computer`**: the agent on the machine it runs on. A microphone, the speakers, optionally a webcam for hand signs. A voice assistant.
- **`wica-interaction-reachy`**: the agent in a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini). It speaks out of the robot's speaker, listens through the robot's echo-cancelled microphone, sees hand signs through the robot's camera, plays emotions and manages its motors.

Speech in is a live speech recognizer, speech out real audio, and the robot really moves. Everything the agent is, the World it reasons over, its Commands, its persona, the page you watch it on, is shared; what a body adds is captured by one small protocol, the [Embodiment](specs/embodiment.md).

**Start here:**

- [docs/getting-started.md](docs/getting-started.md): install, a template, the keys, the computer app, then the robot in the simulator and on a Reachy Mini.
- [docs/index.md](docs/index.md): the documentation, by task.
- On this page: [what you get](#what-you-get), [install](#install), the [quick start](#quick-start).

## Project status

> [!WARNING]
> - **Under development: the config and the code change.** The app is at version 0.1 and is shaped as it is used, together with the five first-party packages it is built on; a config field or a persona rule can change between commits, without a deprecation period.
> - **Run on macOS (Apple silicon)**: the computer embodiment, and the robot embodiment on the MuJoCo simulator and on a **Reachy Mini Lite** over USB. Linux and Windows resolve from the lock file but have not been run; the wireless Reachy Mini is untested here as in the bridge ([docs/reference/install.md](docs/reference/install.md#platforms)).

> [!TIP]
> **Feedback is welcome: open an issue.** Bug reports, questions, reports from a platform, a robot or a model the author does not test on, and what you would want the config or the personas to do differently all go to [the issue tracker](https://github.com/funwithagents/wica-interaction/issues). The project is developed by one person and is not taking pull requests yet; [CONTRIBUTING.md](CONTRIBUTING.md) says why and how to help anyway.

## What you get

| Feature | What it is |
|---|---|
| **A spoken conversation** | A speech recognizer over the body's microphone turns each closed utterance into one `speech_input` the agent reasons over; the agent answers through `say`, a Command that plays real audio and stays visibly running for the duration of the speech ([docs/reference/agent.md](docs/reference/agent.md)) |
| **One app, several bodies** | The computer and the Reachy Mini are two implementations of one protocol: where audio comes in and goes out, what the agent can act with, what it sees through. The lifecycle, the World, the dictation Commands and the page are written once ([specs/embodiment.md](specs/embodiment.md)) |
| **A robot that acts** | On the Reachy Mini the agent plays emotions and manages the motors through Commands over [`reachy-mini-bridge`](https://github.com/funwithagents/reachy-mini-bridge); the robot breathes, sways with its voice and follows the person's face on its own ([docs/guides/reachy-mini.md](docs/guides/reachy-mini.md)) |
| **Hand signs** | The body's camera runs through [`vision-modules`](https://github.com/funwithagents/vision-modules)' hand pipeline; a small vocabulary (`stop`, `like`, `dislike`, `ok`, `one` to `five`), one sign per hand, reaches the agent once it has held for half a second, and the agent decides what a sign means, except `stop` ([docs/guides/hand-signs.md](docs/guides/hand-signs.md)) |
| **Dictation** | The agent switches speech capture to one longer passage when asked, ended by a pause or a trigger word, and reverts on its own ([docs/guides/dictation.md](docs/guides/dictation.md)) |
| **No echo** | The agent never hears its own voice: the robot's XVF3800 cancels it in hardware, the computer runs an opt-in WebRTC echo canceller fed with what the voice plays ([docs/guides/computer.md](docs/guides/computer.md#echo-cancellation)) |
| **One JSON config** | The agent provider, the speech recognizer, the voice, the robot backend, the camera and the persona are one file; each engine section is that engine's own document, nested verbatim ([docs/reference/configuration.md](docs/reference/configuration.md)) |
| **Provider choice** | Anthropic, OpenAI, Gemini, Mistral or the Hugging Face Hub for the agent; Deepgram for speech in; thirteen voices, hosted or local, for speech out, each an extra you install only if you use it ([docs/reference/install.md](docs/reference/install.md#extras)) |
| **Watch it work** | A Gradio page: the conversation with every Command's execution state live, the live speech recognition and the Vision line, a typed-text box, and WICA's World and reaction panels behind a debug toggle |
| **Opens even when degraded** | A missing key, an absent extra, a robot that cannot be reached: each lands as one banner line, and what still works keeps working, typed text driving the agent at the least ([docs/guides/troubleshooting.md](docs/guides/troubleshooting.md#the-banner)) |

## Install

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/), from a checkout. Pick a body, an agent provider, a speech recognizer and a voice as extras; the three recipes most people want:

```
git clone https://github.com/funwithagents/wica-interaction.git && cd wica-interaction

# the computer, as the template configures it
uv sync --no-dev --extra computer --extra wica-anthropic --extra asr-deepgram --extra tts-elevenlabs --extra audio-processing --extra vision

# the Reachy Mini, as the template configures it: in the simulator (drop reachy-sim for a real robot)
uv sync --no-dev --extra reachy --extra reachy-sim --extra wica-huggingface-hub --extra asr-deepgram --extra tts-pocket --extra vision

# the development environment: both bodies, every agent provider, the simulator, the tooling
uv sync --dev
```

Every extra, the platforms, the system packages and the first-run downloads are in [docs/reference/install.md](docs/reference/install.md).

## Quick start

One config file stands up each embodiment; the committed files are templates to copy and adapt ([docs/reference/configuration.md](docs/reference/configuration.md)). Keys are referenced by environment variable, never stored.

```
export WICA_ANTHROPIC_API_KEY=sk-...   # the agent
export DEEPGRAM_API_KEY=...            # speech in
export ELEVENLABS_API_KEY=...          # the voice
uv run wica-interaction-computer --config config.computer.example.json
```

Open the address Gradio prints and talk. Each thing you say shows as 🗣️ on the left; the agent's reply is a **🗣️ say** item that stays running while you hear it. Without a key the page still opens, says what to set, and the text box drives the agent wherever its own key is set.

The robot, in the simulator the bridge spawns for you (the MuJoCo viewer opens; needs the `reachy-sim` extra, and on macOS an unlocked GUI session):

```
export WICA_HF_TOKEN=hf_...            # the agent (the template uses the Hugging Face Hub)
export DEEPGRAM_API_KEY=...
uv run wica-interaction-reachy --config config.reachy.example.json
```

The template's voice is the local `pocket` model, which needs no key and downloads its weights on first run. For a Reachy Mini Lite plugged in over USB, set `reachy.backend` to `"real"`; the complete walk-through, the simulator's options and the robot's, continues in [docs/getting-started.md](docs/getting-started.md).

## Documentation

Start at [docs/index.md](docs/index.md), the documentation by task. The pages used most:

| | |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Install, a template, the keys, the first run on the computer, then the robot |
| [docs/reference/configuration.md](docs/reference/configuration.md) | The one JSON file section by section, the templates, what a loader rejects |
| [docs/reference/install.md](docs/reference/install.md) | Every extra, the platforms, the system packages, the first-run downloads |
| [docs/reference/agent.md](docs/reference/agent.md) | What the model sees and can do: the World entries, the Commands, the persona sections, the transcript |
| [docs/guides/](docs/index.md#by-task) | The computer, the Reachy Mini, hand signs, dictation, personas, benchmarking, limits, troubleshooting |

## How it is developed

WICA Interaction is a spec-driven project, like the packages it is built on: every feature is designed before it is coded and pinned by tests at two levels.

- **Specs first.** Each concept has one spec under [specs/](specs/), the normative design with its rationale and its open questions, indexed by [specs/_index.md](specs/_index.md) with a status; [specs/_overview.md](specs/_overview.md) is the architecture in one page. An implementation plan under [plans/](plans/_index.md) turns a settled spec into code. Each spec names the code and tests it governs, and tests keep that map, the statuses and the documentation's links honest.
- **Two test tiers.** `tests/` is fast and deterministic: no network, no key, no audio device, no camera, no robot; the lifecycle runs over a fake body and WICA's scripted `fake` provider. `tests-e2e/` is opt-in and calls real services, skipping what it lacks a key or a model for ([specs/testing.md](specs/testing.md)).
- **Five first-party packages**, each from its GitHub repository and pinned to a commit by `uv.lock`: [wica](https://github.com/funwithagents/wica-framework) (the agent and its Gradio panels), [asr-engine](https://github.com/funwithagents/asr-engine) (speech in), [tts-engine](https://github.com/funwithagents/tts-engine) (the voices), [reachy-mini-bridge](https://github.com/funwithagents/reachy-mini-bridge) (the robot), [vision-modules](https://github.com/funwithagents/vision-modules) (the hand pipeline).
- **[AGENTS.md](AGENTS.md) is the operating manual** for anyone, or any coding agent, working in the repository: the project map (which module does what, with its spec), the verification gate, how to run the live tier, how to develop against a sibling checkout, and how the specs and their statuses are kept in step with the code.

The same gate, locally:

```
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest                 # the fast tier
uv run pytest tests-e2e       # the live tier: what has its models or keys runs, the rest skips
```

## License

MIT, see [LICENSE](LICENSE). Some voice extras pull in differently licensed packages or weights (`tts-piper` is GPL-3.0+); tts-engine's module pages say so.
