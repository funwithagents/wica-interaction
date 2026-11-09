# Install

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). The app is not on PyPI; it runs from a checkout, and its first-party dependencies (`wica`, `asr-engine`, `tts-engine`, `reachy-mini-bridge`, `vision-modules`) come from GitHub, each pinned to an exact commit by `uv.lock`, so an install needs no checkout next door.

```
git clone https://github.com/funwithagents/wica-interaction.git
cd wica-interaction
```

## Recipes

What every embodiment needs is a base dependency; what only one body, one provider or one optional feature needs is an **extra**, so an install carries only what it runs. Pick a body, an agent provider, a speech recognizer and a voice:

```
# the computer: Anthropic, Deepgram, an ElevenLabs voice, echo cancellation and hand signs (the computer template)
uv sync --no-dev --extra computer --extra wica-anthropic --extra asr-deepgram --extra tts-elevenlabs --extra audio-processing --extra vision

# the computer, minimal: one provider each, no echo cancellation, no camera
uv sync --no-dev --extra computer --extra wica-openai --extra asr-deepgram --extra tts-elevenlabs

# the Reachy Mini: the Hub, Deepgram, the local pocket voice, hand signs (the reachy template)
uv sync --no-dev --extra reachy --extra wica-huggingface-hub --extra asr-deepgram --extra tts-pocket --extra vision

# everything, one command
uv sync --no-dev --extra all

# the development environment: both bodies, every agent provider, the voices the templates select, the simulator, the tooling
uv sync --dev
```

`--no-dev` keeps an install to what you selected: a bare `uv sync` includes the `dev` group, which pulls both bodies and the tooling. The **simulator** (`reachy-mini-bridge[sim]`, MuJoCo) is part of that `dev` group and of no extra, since a deployment on a real robot needs none of it: to run the robot embodiment against the simulator, install with `uv sync --dev`. Selecting a provider whose extra is not installed does not fail the install or the config load: it lands on that engine's banner line when the app starts, naming the extra to add ([../guides/troubleshooting.md](../guides/troubleshooting.md#the-banner)).

## Extras

One extra per body, per optional feature, per provider, named after the sibling's own extra (`wica-<name>`, `asr-<name>`, `tts-<name>`), so an engine's "install `<package>[<name>]`" hint always has a matching extra here. The voices serve **both** bodies: the computer plays them through its speakers, the bridge builds the robot's voice from the same engine.

| Extra | Pulls | For |
|---|---|---|
| *(base)* | `wica[gradio]`, `asr-engine`, `vision-modules` (its numpy-only core), `gradio`, `numpy` | the lifecycle, the page, speech in, the vision plumbing |
| `computer` | `tts-engine` | the computer embodiment's voice |
| `audio-processing` | `aec-audio-processing`, `samplerate` | the computer's echo cancellation and noise suppression |
| `reachy` | `reachy-mini-bridge` | the Reachy Mini embodiment (pulls the upstream `reachy-mini` SDK); add `reachy-mini-bridge[sim]` for the simulator |
| `vision` | `vision-modules[hand,opencv]` | the hand-sign input, either body: MediaPipe, the torch classifier, OpenCV for the webcam and the snapshots |
| `wica-anthropic` | `wica[anthropic]` | the Anthropic agent provider |
| `wica-openai` | `wica[openai]` | the OpenAI agent provider |
| `wica-google-genai` | `wica[google-genai]` | the Gemini agent provider (Gemini Developer API) |
| `wica-mistralai` | `wica[mistralai]` | the Mistral agent provider (La Plateforme) |
| `wica-huggingface-hub` | `wica[huggingface-hub]` | the Hugging Face Hub agent provider (Inference Providers) |
| `asr-deepgram` | `asr-engine[deepgram]` | the Deepgram speech recognizer, the one the templates select |
| `tts-elevenlabs` | `tts-engine[elevenlabs]` | the ElevenLabs hosted voice |
| `tts-gradium` | `tts-engine[gradium]` | the Gradium hosted voice |
| `tts-mistral` | `tts-engine[mistral]` | the Mistral hosted voice |
| `tts-deepgram` | `tts-engine[deepgram]` | the Deepgram hosted voice |
| `tts-gemini` | `tts-engine[gemini]` | the Gemini hosted voice |
| `tts-cartesia` | `tts-engine[cartesia]` | the Cartesia hosted voice |
| `tts-openai` | `tts-engine[openai]` | the OpenAI Realtime API voice |
| `tts-openai-compatible` | `tts-engine[openai-compatible]` | OpenAI's speech endpoint, or any OpenAI-compatible server |
| `tts-pocket` | `tts-engine[pocket]` | the local pocket-tts voice (torch, weights downloaded on first run), the reachy template's choice |
| `tts-phonon` | `tts-engine[phonon]` | the local Phonon voice |
| `tts-qwen3` | `tts-engine[qwen3]` | the local Qwen3-TTS voice, Apple silicon only |
| `tts-kokoro` | `tts-engine[kokoro]` | the local Kokoro voice |
| `tts-piper` | `tts-engine[piper]` | the local Piper voice (GPL-3.0+) |
| `all` | every extra above | everything in one install |

Not offered: asr-engine's Kyutai local recognizers, whose numpy pin conflicts with `vision-modules`; the siblings' MCP extras, a transport this app never runs. What each voice needs, a key, a platform, a model download, is on tts-engine's [module pages](https://github.com/funwithagents/tts-engine/blob/main/docs/modules/index.md); the agent providers' notes are in WICA's [configuration reference](https://github.com/funwithagents/wica-framework/blob/main/docs/reference/configuration.md). The rationale for the shape is [specs/project.md](../../specs/project.md).

## Platforms

What the lock file resolves on each platform, checked on 2026-11-09, apart from what has been run:

| Platform | Resolves | Not available | Run |
|---|---|---|---|
| macOS, Apple silicon | everything | | **yes**: developed here; the computer embodiment, and the robot embodiment on the simulator and on a Reachy Mini Lite over USB |
| Linux x86_64 | everything, with the [system packages](#system-packages) below | | the fast tier, in CI on every change |
| Linux arm64 (a Raspberry Pi, a Jetson) | everything but the `vision` extra | hand signs: MediaPipe ships no wheel, so the `vision` extra does not install, and neither does the `dev` group that pulls it | not yet |
| Windows x86_64 | everything | | the fast tier, in CI on every change; the app itself has not run there, the robot path is unvalidated, and the simulator's viewer and a Lite over USB are untried |
| macOS, Intel | the base, the agent providers, the hosted voices, the robot | hand signs and the local voices: torch, MediaPipe and onnxruntime ship no wheel for an Intel Mac | not yet |

The Reachy Mini side follows the bridge's own matrix, one table of what each setup needs and gives and how far it is validated: [backends and capabilities](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/reference/backends-and-capabilities.md). The wireless Reachy Mini is untested by the bridge's author too.

## System packages

Nothing on macOS beyond `mjpython`, which MuJoCo's wheel installs, for the simulator's viewer window. On Linux:

| For | Package |
|---|---|
| the computer's microphone and speakers (`sounddevice`) | `libportaudio2` |
| echo cancellation (`aec-audio-processing` builds from source) | a C++ toolchain: `build-essential`, `cmake` |
| hand signs (MediaPipe loads the system's GLES and EGL) | `libgles2 libegl1` |
| a Reachy Mini daemon on this machine, the simulator or a Lite over USB | the system GStreamer and the Rust webrtc plugin, which no distribution packages; Mesa's EGL for the headless simulator's camera: the bridge's [Linux guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/linux.md) |

On Windows, the audio and echo-cancellation packages ship wheels; GStreamer comes with the `reachy-mini` wheels.

## First-run downloads

Some features fetch a model the first time they run, so the first start needs the network and a moment:

| Feature | Download | Where |
|---|---|---|
| hand signs (`vision`) | a MediaPipe hand bundle and a ~340 MB gesture classifier | the Hugging Face cache; until they are ready the Vision line shows no label |
| the `pocket` voice, and the other local voices | the model's weights | the Hugging Face cache, at the first `say` |
| the robot's emotions | the recorded-moves library | the Hugging Face cache, preloaded at daemon start (`daemon.preload_datasets`) |
| the robot's face detector (`yunet`) | the detector's weights | the Hugging Face cache, at the first detection |

A download that fails (no network on first run) does not crash the app: hand signs report it on the Vision line, a voice on its banner line or as the `say` Command's reply ([../guides/troubleshooting.md](../guides/troubleshooting.md)).

## Moving to a newer commit of a sibling

The siblings are git dependencies on their `main` branch, pinned by `uv.lock`. To take a newer commit of one: `uv lock --upgrade-package <name>` (`wica`, `asr-engine`, `tts-engine`, `reachy-mini-bridge` or `vision-modules`), then `uv sync --dev`, and commit the lock. Working on a sibling from a checkout next door is a development setup, described in [AGENTS.md](../../AGENTS.md#developing-against-a-sibling-checkout).
