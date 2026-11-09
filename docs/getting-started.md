# Getting started

Your first run of the app on your computer, then the same app in a Reachy Mini: in the simulator first, then on a robot. Every config field is in [reference/configuration.md](reference/configuration.md); every extra and what your platform supports in [reference/install.md](reference/install.md).

## Install

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/), from a checkout. The quickest route to both bodies is the development environment, which installs both embodiments, every agent provider, the voices the templates use, hand signs and the simulator:

```
git clone https://github.com/funwithagents/wica-interaction.git
cd wica-interaction
uv sync --dev
```

To install only what you run, pick extras instead ([reference/install.md](reference/install.md#recipes)). On Linux a few system packages come first ([reference/install.md](reference/install.md#system-packages)).

## A config file of your own

The app is stood up from one JSON file, and `--config` is required. The two committed files are **templates**: copy the one for the body you want, next to it, and point the command at your copy. A `*.local.json` name keeps it out of your commits by convention:

```
cp config.computer.example.json config.computer.local.json
```

Keys never go in the file. Each section names an **environment variable** (`api_key_env`), and you export the variable in the shell that runs the command. The computer template needs three, the reachy template two:

| Variable | For | Template |
|---|---|---|
| `WICA_ANTHROPIC_API_KEY` | the agent, Anthropic | computer |
| `WICA_HF_TOKEN` | the agent, the Hugging Face Hub | reachy |
| `DEEPGRAM_API_KEY` | speech in | both |
| `ELEVENLABS_API_KEY` | the voice | computer; the reachy template's `pocket` voice needs none |

Another agent provider is a change of `wica.agent` (`provider`, `model`, `api_key_env`) with its `wica-*` extra installed; another voice a change of the `tts` block's `module`. The reference page has each section ([reference/configuration.md](reference/configuration.md)).

## The computer

```
export WICA_ANTHROPIC_API_KEY=sk-...
export DEEPGRAM_API_KEY=...
export ELEVENLABS_API_KEY=...
uv run wica-interaction-computer --config config.computer.local.json
```

Open the address Gradio prints (`http://127.0.0.1:7860`) and say something. What happens:

1. The **ASR** panel on the right shows your words as they are recognized, then clears when the utterance closes.
2. The closed utterance shows on the left as **🗣️ "what you said"**, and the agent reasons: a **💬 reaction** group opens.
3. Inside it, **🗣️ say** is what you hear, with a spinner while it plays and ✅ with its duration once done. A **💭 output sink** item is the model's private thinking, if it produced any; **🚫 noop** means it chose not to react.

As the template configures it, **echo cancellation** is on, so the agent does not answer its own voice, and **hand signs** are on, reading your webcam: the Vision line under the ASR panel shows what the camera sees, and showing the camera a flat hand or a fist stops the agent mid-sentence ([guides/hand-signs.md](guides/hand-signs.md)). The first start downloads the hand models, a few hundred megabytes. To run without a camera, set `vision.enabled` to `false`.

Two more things on the page. The **text box** sends a typed turn through the same path as speech, so the app works with only the agent's key set. And **Show debug panels** reveals WICA's own views: the World as the agent sees it, and every reasoning step with its prompt and timing.

macOS asks for microphone and camera permission for the terminal that runs the command, once. Devices, the echo canceller's delay and noise suppression are in [guides/computer.md](guides/computer.md); if nothing happens after you speak, [guides/troubleshooting.md](guides/troubleshooting.md#nothing-happens-after-i-speak).

## The robot in the simulator

The reachy template targets the **MuJoCo simulator**, which the bridge spawns for you with its viewer window, and uses your **webcam** as the robot's camera, so the simulated robot turns to follow you and reads your hand signs. It needs the simulator, the `reachy-sim` extra, part of `uv sync --dev`, and an unlocked GUI session; on macOS `mjpython`, which the MuJoCo wheel installs.

```
cp config.reachy.example.json config.reachy.local.json
export WICA_HF_TOKEN=hf_...
export DEEPGRAM_API_KEY=...
uv run wica-interaction-reachy --config config.reachy.local.json
```

The viewer opens with the webcam picture in its corner, the page opens as before, and the robot speaks with the local `pocket` voice, which downloads its weights on the first `say`. Ask it to show you it is happy: a **🦾 play_emotion** item runs, the simulated robot moves, and the robot confirms out loud in its next turn. The first emotion loads the recorded-moves library, another first-run download.

A windowless simulator is `reachy.daemon.headless` set to `true`; the rendered scene instead of the webcam is `reachy.daemon.camera.source` set to `"sim"` (on macOS a headless simulator has no camera at all). The simulator's options, the voice and face tracking are in [guides/reachy-mini.md](guides/reachy-mini.md); a simulator that does not come up, [guides/troubleshooting.md](guides/troubleshooting.md#the-robot-does-not-start).

## On a Reachy Mini Lite

Plug the robot in over USB and change one field of your copy:

```json
"reachy": { "backend": "real", ... }
```

The `daemon` block then spawns the hardware daemon for the robot on USB; the bridge wakes the robot, and puts it to sleep when you close the app. Keep the `tts` block, or the robot comes up without a voice, and keep `face_detection` with `motion.tracking` for a robot that follows you. macOS asks for camera and microphone permission for the process, once. The daemon can take a while to come up; the bridge waits `reachy.daemon.startup_timeout`, 45 seconds by default, before giving up.

A wireless Reachy Mini runs its own daemon: `daemon.spawn` becomes `"never"` and `robot.host` names the robot. Neither this app nor the bridge has been run against one ([reference/install.md](reference/install.md#platforms)).

## With no robot and no simulator

For development with nothing attached, the `fake` backend stands in: set `reachy.backend` to `"fake"` **and delete the `reachy.daemon` block** (the fake has no daemon to spawn). The agent runs with the robot's persona and Commands; `play_emotion` records the move and takes its real duration, `say` takes the time the utterance would, nothing is heard.

## Where next

- [guides/computer.md](guides/computer.md) and [guides/reachy-mini.md](guides/reachy-mini.md): each body in full.
- [guides/personas.md](guides/personas.md): what the agent is told, and how to change it.
- [reference/agent.md](reference/agent.md): what the model sees and can do, step by step.
- [guides/limits.md](guides/limits.md): what v1 does not do.
