# Overview — Reachy Mini Interaction WICA

*A global map of the project: what it is, how the pieces fit, and where to read next. This is an orientation document, not a governing spec — it declares no `code:`/`tests:` and the drift-guard tests skip it (the leading `_`). The authoritative design lives in the per-concept specs it links.*

## What this is

An **interaction application for the [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) robot**: a person **speaks** to the robot and it **speaks and acts** back — replying out loud, playing emotions, following the person's face, resting or engaging its motors. It is built by wiring speech-in and a WICA agent to the robot's own interaction API:

- **[ASR engine](../../asr-engine/AGENTS.md)** — turns the robot's microphone audio into text *segments* (speech in). The app runs its own ASR; the bridge only hands it clean mic audio.
- **[WICA](../../wica-framework/INTEGRATING.md)** — the agent: serializes state into a prompt, reasons, and acts (the brain).
- **[`reachy-mini-bridge`](../../reachy-mini-bridge/specs/_overview.md)** — the robot. Its `ReachyMiniApi` owns the robot: it **speaks** (`say`, synthesized speech routed to the robot speaker), exposes the **echo-cancelled microphone** as a stream, and offers action verbs (`play_emotion`, `start_head_tracking`, `set_motors_state`, …). The bridge, in turn, wraps the first-party [`tts-engine`](../../tts-engine) for its voice.

`asr-engine`, `wica`, and `reachy-mini-bridge` are **sibling path dependencies** (`../asr-engine`, `../wica-framework`, `../reachy-mini-bridge`) consumed **in-process** as libraries — this app runs no MCP servers. The voice is configured as part of the robot: the `reachy` config section is a verbatim `reachy-mini-bridge` config, including its `tts` block, so the bridge builds and owns the synthesizer — see [configuration.md](configuration.md). Compared with WICA's [conversation demo](../../wica-framework/specs/conversation-demo.md) (a text box faking speech), here speech in is a live robot mic, speech out is real audio on the robot, and the agent can move the robot — while the same observability panels (WICA's reusable `wica.contrib.gradio` components, behind the `wica[gradio]` extra) let you watch the framework work.

## Why the robot, not local audio

Audio on Reachy Mini is **not** a pair of laptop `sounddevice` streams — it is a daemon-owned pipeline with an XMOS **XVF3800** voice processor doing **acoustic echo cancellation**, noise suppression, AGC, and beamforming in firmware. Echo cancellation only works if playback goes **through** that pipeline, so TTS **must** be played through the robot (the bridge owns that), and the mic must be read **from** the robot (the bridge exposes the post-processed stream). That is why this app plays and listens through `reachy-mini-bridge` and needs **no** local audio-processing of its own — the hardware (and the simulator) already deliver a clean, echo-cancelled signal. See [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md).

## The core idea: state, not a transcript

WICA's model is that you don't thread a conversation — you maintain **World state**, and every reasoning step renders the relevant state into the prompt. This app has a deliberately tiny World: one **`speech_input`** entry (the thing the person just said) plus the command entries WICA tracks automatically. Speaking is a **Command** (`say`), and so is every robot action — so the user-facing voice and the robot's moves are real, observable, cancellable actions, and the model's free text becomes visible *private thinking*.

## End-to-end data flow

```
 robot mic (echo-cancelled) ─▶ AsrBridge (asr-engine)
                        │  interim segment ─▶ ASR panel (live partial text)   [agent NOT triggered]
                        └─ final segment ───▶ world.update("speech_input") ──▶ WICA Agent
                                                                                ├─ say(text) ─▶ bridge ─▶ robot speaker
                                                                                ├─ play_emotion / head-tracking / motors ─▶ bridge ─▶ robot
                                                                                └─ free text ─▶ output sink (💭 thought, in the transcript)
```

The load-bearing rule: **partial speech is visible but inert; only a *closed* segment reaches the agent**, exactly once, as one complete utterance. So a burst of interim ASR updates streams into the ASR panel without ever waking the agent mid-sentence.

## The two modes

| Mode | Segmentation | What the person does |
|---|---|---|
| **Conversation** (default) | `utterance` — one segment per spoken sentence | Talks in short turns; each turn is a `speech_input` the agent replies to. |
| **Dictation** (agent-invoked) | `timeout` / `trigger_word` — segment closes on a pause or trigger word | Speaks a longer free-form passage that arrives as one big `speech_input`. |

Dictation is entered by the **agent**, on request, via a `start_dictation` Command; it captures a single passage (`end_on_final_segment=True`) and reverts automatically. It changes *only* how long a segment grows — dictated speech still feeds the agent and still gets a reply. A UI status indicator plus a **Stop dictation** button give a manual hatch.

## The six modules

Each core concept is one module in `src/reachy_mini_interaction_wica/`, governed by one spec:

| Module | Role | Spec |
|---|---|---|
| `config.py` | One composed JSON (`wica`/`asr` engine sections + a `reachy` robot-backend section, voice included), each section delegated to its own loader | [configuration.md](configuration.md) |
| `asr_bridge.py` | Speech-in: owns the `ASREngine` fed the robot's echo-cancelled mic stream, maps segments to `speech_input` (final only) + the live view, hosts the dictation command | [asr-bridge.md](asr-bridge.md) |
| `tts_output.py` | Speech-out: the `say` output Command — the agent's voice, spoken through the robot speaker via the bridge | [tts-output.md](tts-output.md) |
| `robot_control.py` | Robot actions: WICA commands wrapping the bridge's `play_emotion` / `list_emotions` / head-tracking / motor-state verbs | [robot-control.md](robot-control.md) |
| `app.py` | App wiring + lifecycle: builds ASR + the robot (via `ReachyMiniApi`) behind WICA on one event loop, registers commands, `main()` | [app.md](app.md) |
| `app_ui.py` | The Gradio presentation layer: the `display_entry` hook, the page layout (`build_ui` — placing WICA's `wica.contrib.gradio` observability panels next to the app's own ASR panel) and the ASR live view its timer polls | [app.md](app.md) |

Plus two cross-cutting specs: [project.md](project.md) (tooling/layout) and [testing.md](testing.md) (the two-tier test strategy).

## Configuration in one file

The whole app is stood up from a single JSON file with sections `wica`, `asr`, and `reachy` — each section holding *exactly* the document that engine's own loader expects, `reachy` a verbatim `reachy-mini-bridge` config (backend, daemon lifecycle, the voice at `reachy.tts`, audio profile). Switching provider, voice, ASR backend, robot backend, or persona is a file edit, not a code change. Keys are referenced by env-var name, never stored; a missing key surfaces as that engine's own error, and the app still opens and says what to set. Full detail: [configuration.md](configuration.md).

## What it is (and isn't) — v1

- **It *is* a robot, not a plain voice assistant.** The agent can express emotions, turn on face-following, and manage the motors, in addition to talking (see [robot-control.md](robot-control.md)).
- **Echo handling: yes (on the robot); barge-in: no.** The robot's XVF3800 cancels the app's own TTS from the mic, so the agent doesn't hear itself — the app adds no echo processing of its own. But *talking over* the agent still isn't supported: WICA is one reasoning call at a time, and inputs arriving mid-step are dropped.
- **No camera / vision in v1.** The bridge can read a camera frame, but this app does not wire it — the agent hears and acts, it does not see (a natural post-v1 addition).
- **No manual movement/gaze in v1.** Only the autonomous head-tracking verb; explicit pose/gaze verbs are deferred in the bridge itself.
- **No streamed speech, no persistence** — `say` plays a complete utterance; the World starts empty each run.

## Where to start reading

- The whole product & UI: [app.md](app.md)
- The speech-in rule and dictation: [asr-bridge.md](asr-bridge.md)
- The voice: [tts-output.md](tts-output.md)
- The robot actions: [robot-control.md](robot-control.md)
- The config file: [configuration.md](configuration.md)
- Status of every spec: [_index.md](_index.md); build status: [../plans/_index.md](../plans/_index.md)
