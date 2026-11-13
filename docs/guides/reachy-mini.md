# The Reachy Mini embodiment

The agent in a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini): it speaks out of the robot's speaker, listens through the robot's microphone, sees through the robot's camera, plays emotions and manages its motors, all through [reachy-mini-bridge](https://github.com/funwithagents/reachy-mini-bridge). The design is [specs/reachy-embodiment.md](../../specs/reachy-embodiment.md).

## What it is

One `ReachyMiniBridge` session backs everything: the voice, the microphone stream, the camera feed and the action commands. Audio on the robot is a daemon-owned pipeline with an XVF3800 voice processor doing echo cancellation, noise suppression and beamforming in firmware, which only works when playback goes through that pipeline and capture is read from it. That is why this body plays and listens through the bridge and needs none of the computer's [audio processing](computer.md#echo-cancellation). The simulator delivers a clean signal too.

The whole robot side of the config file is the `reachy` section, a bridge config carried verbatim ([reference](../reference/configuration.md#reachy-the-robot)); every field is documented in the bridge's [configuration reference](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/reference/configuration.md). The persona is [prompts/reachy.md](../../prompts/reachy.md) ([personas.md](personas.md)). What each way of running the robot needs and gives, and how far the bridge has validated it, is one table: [backends and capabilities](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/reference/backends-and-capabilities.md).

## The simulator

The committed template, [config.reachy.example.json](../../config.reachy.example.json), targets the MuJoCo simulator: `backend` is `sim` and the `daemon` block has `spawn: "auto"` and `headless: false`, so the bridge starts a simulator daemon itself and opens the MuJoCo viewer window. The viewer needs an unlocked GUI session and, on macOS, `mjpython` on the `PATH`, which the MuJoCo wheel installs. The simulator is the `reachy-sim` extra, next to `reachy`: part of `uv sync --dev`, added by name to an install of your own ([install.md](../reference/install.md#recipes)).

The template makes your **webcam** the simulated robot's camera (`daemon.camera.source: "webcam"`), shown in the corner of the viewer window with the `sim_displays` overlays on: the face detector then sees you and the simulated head turns to follow you, and your hand signs reach the agent through the same picture. Set `camera.source` to `"sim"` for the rendered scene instead; nothing is in it to detect unless the bridge's test scene puts a portrait there.

For a simulator with no window, set `headless: true` and drop the `sim_displays` block, which needs the viewer. The webcam source works headless on every platform; the rendered camera renders offscreen on Linux only, and a headless simulator on macOS has no camera at all. The bridge's [daemon guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/running-daemons.md) has every launch mode, and its [Linux guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/linux.md) what a daemon on a Linux machine needs installed.

## A Reachy Mini Lite over USB

Change `backend` to `"real"` and keep `daemon.spawn: "auto"`: the bridge spawns the hardware daemon of the robot plugged in over USB, wakes the robot, and puts it to sleep when the app closes. On macOS the process needs camera and microphone permission. The daemon's bring-up is bounded by `daemon.startup_timeout` (45 s in the template); the app adds no timeout of its own, so a daemon that never becomes ready fails after that long, and the page opens with the robot missing ([troubleshooting.md](troubleshooting.md#the-robot-does-not-start)). The recorded-moves library the emotions play from is downloaded into the Hugging Face cache on first use, preloaded at daemon start with `daemon.preload_datasets` ([first-run downloads](../reference/install.md#first-run-downloads)). This is the setup the app has been run on, with the simulator ([platforms](../reference/install.md#platforms)).

## A wireless Reachy Mini

A wireless robot runs its own daemon: set `robot.host` to the robot's address and `daemon.spawn` to `"never"`. Neither this app nor the bridge has been run against one; the bridge's author has no wireless robot, and its documentation lists what differs on it. If you try, an issue saying what happened is welcome.

## Offline: the fake backend

To develop with no robot and no simulator, set `backend` to `"fake"` **and delete the `daemon` block**: the fake has no daemon, and the loader rejects a `spawn` other than `"never"` there. The fake records every command and returns synthetic audio and frames, so the whole app runs, the agent reasons, `say` takes the time the speech would take, and nothing is heard or moves.

When a `real` or `sim` connection fails, the banner's `Reachy Mini:` line carries the error and this hint, appended by the embodiment: `(to develop with no robot, set reachy.backend to "fake" and remove the reachy.daemon block, or to "sim" for the simulator)`. The agent still runs on typed text with nothing to speak or move through.

## The voice

The robot's voice is the `reachy.tts` block, a tts-engine engine block the bridge builds the synthesizer from, the same shape as the computer's `tts.engine`. The template selects the local `pocket` model with the voice `george`: no key, no network once its weights are downloaded. Any tts-engine provider works with its `tts-<type>` extra installed ([extras](../reference/install.md#extras)); the bridge's [audio guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/audio.md) covers choosing and tuning a voice on the robot, and head wobbling, which sways the head with the audio while it speaks.

Without a `tts` block the robot comes up with no voice: the banner shows `TTS: no reachy.tts block configured (say is unavailable)`, and a voice that failed to build (its extra missing, its key unset) shows `TTS:` with the cause. Either way `say` stays wired, so the model learns why nothing was heard from the command's reply, `TTS error: …`, and everything else about the body works ([troubleshooting.md](troubleshooting.md#no-voice-or-a-quiet-one)).

## The people in front of the robot

The template names the bridge's shipped detector, `face_detection.detector: "yunet"`, and turns detection on; its weights are downloaded on the first detection. With a detector, the robot **sees people and tells the agent**: who comes into view, who leaves, who comes very close, and who is in view right now, each person under a number while they stay in view ([agent.md](../reference/agent.md#the-world)). The bridge decides who is present: a face counts once it has been seen for a third of a second, a person the robot's own emotion turned the head away from is still there, and someone is gone once missing for two seconds where the camera looks. With a detector configured, the app keeps detection running for the whole session even if `face_detection.enabled` says `false` (a log line says so), since the people exist only while it runs.

**Following a person is the agent's.** The template starts with head tracking off (`motion.tracking.enabled: false`); the agent starts it with `start_tracking`, on whoever is in front of it or on one person by number, and stops it with `stop_tracking`. Its persona asks it to follow someone who comes into view, the person it talks with when several are there, and to stop when everyone has left. `motion.tracking` keeps the posture the bridge gives the head while it follows someone (mirroring their head roll, leaning toward them, stepping back from someone too close), each with the bridge's defaults: the config says how the head follows, the agent whether and whom. "Very close" is judged for the person the head follows, so with tracking off nobody is ever very close. `motion.tracking.enabled: true` still works: the session then starts following the bridge's automatic choice, until the agent says otherwise.

Without a `face_detection` block the robot sees nobody: no people in the World, no tracking commands, no "People around you" section in the persona; the head does not follow anyone. Tracking needs a detector; `enabled: true` with no `detector` is rejected at load.

The detector's cost knobs are `width`, the width each frame is subsampled to before detection (the template's `640`; the bridge's default `320`), and `target_fps`, a ceiling on detections per second (`null` runs once per camera frame). Hand signs are read from the same camera feed the detector samples: the bridge reads the robot's camera once and every consumer samples it ([hand-signs.md](hand-signs.md)). The bridge's [perception guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/perception-and-tracking.md) has the rest: what the head does while it follows someone, and what the report says.

## What the agent can do with the body

Beyond `say`, the robot registers three commands, and two more when it sees people ([agent.md](../reference/agent.md)):

| Command | What it does |
|---|---|
| `play_emotion(name)` | plays a short recorded motion. The names are fetched from the robot once at startup and seeded into the persona and into the command's description, so the model never has to ask |
| `set_motors_state(state)` | `enabled`, `disabled`, or `gravity_compensation`, a gentle rest that still holds the head up |
| `get_motors_state()` | reads the state back |
| `start_tracking(person=None)` | with a detector: follow a person with the head, the one given by number, or whoever is in front of the robot; a person no longer in view is answered, not raised |
| `stop_tracking()` | with a detector: stop following; the head goes back to its idle movement |

The motors are assumed enabled: the model is told to just move, and to call `set_motors_state("enabled")` only after a move comes back saying the motors are off, then play it again. Because the robot has one body and one speaker, the persona forbids issuing colliding commands in one turn, `say` together with `play_emotion` among them, and asks for a spoken confirmation after every action ([the action rules](personas.md#the-action-rules)).

Presence, breathing and wobbling are the bridge's own aliveness between actions, driven by the `motion` block; none of it is agent-facing, and the persona does not mention it. Following a person is the one exception, framed for the model as whom it pays attention to. If the emotion library cannot be read at startup, the banner shows `Robot: emotions unavailable:` with the cause, the persona carries no emotion names, and `play_emotion` keeps its plain description.

## Running it

```
uv run wica-interaction-reachy --config config.reachy.example.json
```

The template needs `WICA_HF_TOKEN` for the agent and `DEEPGRAM_API_KEY` for the speech recognizer; the `pocket` voice needs no key ([keys](../reference/configuration.md#keys-stay-in-the-environment)). The page is titled `WICA Interaction — Reachy Mini` and laid out as the computer's ([computer.md](computer.md#running-it)); the speech recognizer's `audio.device` is ignored here, since the microphone is the robot's.
