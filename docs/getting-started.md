# Getting started

Your first run of the app on your computer, then the same app in a Reachy Mini: in the simulator first, then on a robot. Every config field is in [reference/configuration.md](reference/configuration.md); every extra and what your platform supports in [reference/install.md](reference/install.md).

## Install

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/), from a checkout. The quickest route to both bodies is the development environment, which installs both embodiments, every agent provider, the voices the default configs use, hand signs and the simulator:

```
git clone https://github.com/funwithagents/wica-interaction.git
cd wica-interaction
uv sync --dev
```

To install only what you run, pick extras instead ([reference/install.md](reference/install.md#recipes)). On Linux a few system packages come first ([reference/install.md](reference/install.md#system-packages)).

## The app's folder

Each app runs from **its own folder**: `~/.config/wica-interaction-computer` for the computer, `~/.config/wica-interaction-reachy` for the robot (`--folder` names another). The first launch fills it: `config.json`, the app's default config, and the persona (`computer.md`, `reachy.md`), both yours to edit there; the app reads them at each launch and never overwrites them.

Both default configs run speech in and the voice **on your machine**, and the agent on the Hugging Face Hub, so they need **one key**: a [Hugging Face token](https://huggingface.co/settings/tokens) in `HF_TOKEN`. Keys never go in the config: each section names an **environment variable** (`api_key_env`, the provider's usual one when left out), which you export in the shell that runs the command or write in the folder's `.env` (`HF_TOKEN=hf_...`).

The local speech recognizer, `mlx-audio`, runs on **Apple silicon** only. On another machine, or for another agent, recognizer or voice, change that block in the folder's `config.json` and install its extra: [reference/configuration.md](reference/configuration.md#what-the-default-configs-run-and-the-other-choices) lists what the default configs run and links each provider's own documentation.

## The computer

```
export HF_TOKEN=hf_...
uv run wica-interaction-computer
```

Open the address Gradio prints (`http://127.0.0.1:7860`) and say something. What happens:

1. The **ASR** panel on the right shows your words as they are recognized, then clears when the utterance closes.
2. The closed utterance shows on the left as **🗣️ "what you said"**, and the agent reasons: a **💬 reaction** group opens.
3. Inside it, **🗣️ say** is what you hear, with a spinner while it plays and ✅ with its duration once done. A **💭 private text** item is the model's private thinking, written outside its `<say>` tag, if it produced any; **🚫 noop** means it chose not to react.

As the default config has it, **echo cancellation** is on, so the agent does not answer its own voice, and **hand signs** are on, reading your webcam: the Vision section shows what the camera sees, and showing the camera a flat hand or a fist stops the agent mid-sentence ([guides/hand-signs.md](guides/hand-signs.md)). The first start downloads the speech recognizer's model, the voice and the hand models, so give it a few minutes: the ASR line says `running` once the recognizer is ready. To run without a camera, set `vision.enabled` to `false` in the folder's `config.json`.

Three more things on the page. The **camera**, beside the Conversation, shows what the body sees, mirrored like a selfie: the webcam on the computer once hand signs are on, the robot's camera with each detected face boxed and numbered; who is present is listed in the **Vision** section, beside the ASR one ([guides/reachy-mini.md](guides/reachy-mini.md#the-users-in-front-of-the-robot)). The **text box** sends a typed turn through the same path as speech, so the app works with only the agent's key set. And **Show debug panels**, at the bottom, reveals WICA's own views: the World as the agent sees it, and every reasoning step with its prompt and timing.

macOS asks for microphone and camera permission for the terminal that runs the command, once. Devices, the echo canceller's delay and noise suppression are in [guides/computer.md](guides/computer.md); if nothing happens after you speak, [guides/troubleshooting.md](guides/troubleshooting.md#nothing-happens-after-i-speak).

## The robot in the simulator

The robot's default config targets the **MuJoCo simulator**, which the bridge spawns for you with its viewer window, and uses your **webcam** as the robot's camera, so the simulated robot turns to follow you and reads your hand signs. It needs the simulator, the `reachy-sim` extra, part of `uv sync --dev`, and an unlocked GUI session; on macOS `mjpython`, which the MuJoCo wheel installs.

```
export HF_TOKEN=hf_...
uv run wica-interaction-reachy
```

Its folder is `~/.config/wica-interaction-reachy`, filled at this first launch as the computer's was.

The viewer opens with the webcam picture in its corner, the page opens as before, and the robot speaks with the local `phonon` voice, as the computer does. Ask it to show you it is happy: a **🦾 play_emotion** item runs, the simulated robot moves, and the robot confirms out loud in its next turn. The first emotion loads the recorded-moves library, another first-run download.

A windowless simulator is `reachy.daemon.headless` set to `true`; the rendered scene instead of the webcam is `reachy.daemon.camera.source` set to `"sim"` (on macOS a headless simulator has no camera at all). The simulator's options, the voice and face tracking are in [guides/reachy-mini.md](guides/reachy-mini.md); a simulator that does not come up, [guides/troubleshooting.md](guides/troubleshooting.md#the-robot-does-not-start).

## On a Reachy Mini Lite

Plug the robot in over USB and change one field of `~/.config/wica-interaction-reachy/config.json`:

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
