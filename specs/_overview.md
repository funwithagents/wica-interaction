# Overview — WICA Interaction

*A global map of the project: what it is, how the pieces fit, and where to read next. This is an orientation document, not a governing spec — it declares no `code:`/`tests:` and the drift-guard tests skip it (the leading `_`). The authoritative design lives in the per-concept specs it links.*

## What this is

One **interaction application** on the **WICA** agentic framework, in which a person **speaks** to an AI agent and the agent **speaks — and, given a body, acts — back**. The same program runs in different **embodiments**, chosen by the command you launch:

- **`wica-interaction-computer`** — the agent on the machine it runs on: a local microphone, the speakers (a [`tts-engine`](../../tts-engine/AGENTS.md) voice), optionally a webcam. A plain voice assistant; formerly the sibling `vocal-interaction-wica` repo.
- **`wica-interaction-reachy`** — the agent in a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini): it speaks out of the robot's speaker, listens through the robot's echo-cancelled mic, sees through the robot's camera, plays emotions and manages its motors, all through [`reachy-mini-bridge`](../../reachy-mini-bridge/specs/_overview.md)'s `ReachyMiniBridge`.

Everything WICA-side is shared — the World and its `speech_input` entry, the dictation commands, the hand-sign entries, the transcript and observability panels, the lifecycle. What varies is captured by one small protocol, the **[Embodiment](embodiment.md)**: where audio comes in and goes out, what the agent can act with, what it sees through. A future body (a Nao) is one more subpackage implementing it.

The building blocks are **sibling path dependencies** consumed **in-process** as libraries (no MCP servers), selected by extras ([project.md](project.md)): [**WICA**](../../wica-framework/INTEGRATING.md) (the agent), the [**ASR engine**](../../asr-engine/AGENTS.md) (speech in), the [**TTS engine**](../../tts-engine/AGENTS.md) (the computer's voice, and the voice the bridge builds for the robot), [**`vision-modules`**](../../vision-modules/README.md) (the hand pipeline), and [**`reachy-mini-bridge`**](../../reachy-mini-bridge/specs/_overview.md) (the robot). Compared with WICA's [conversation demo](../../wica-framework/specs/conversation-demo.md) (a text box faking speech), speech in is a live mic, speech out real audio, the robot really moves — while the same observability panels (WICA's `wica.contrib.gradio`) let you watch the framework work.

## The core idea: state, not a transcript

WICA's model is that you don't thread a conversation — you maintain **World state**, and every reasoning step renders the relevant state into the prompt. This app has a deliberately tiny World: a **`speech_input`** entry (the thing the person just said), one **`hand_gesture_<n>`** entry per hand when vision is on (the sign that hand just showed, if any), plus the command entries WICA tracks automatically. The two inputs are one-shot: each is observed by the step it triggers, then cleared by a short TTL and omitted from every later prompt ([app.md](app.md), "Inputs are transient"). Speaking is a **Command** (`say`), and so is every robot action — so the voice and the moves are real, observable, cancellable actions, and the model's free text becomes visible *private thinking*.

## End-to-end data flow

```
 embodiment.camera ─▶ GestureInput (vision-modules: hand stage → gesture classifier)
                        └─ (hand, sign/None) on change ─▶ world.update("hand_gesture_<n>") ──▶ WICA Agent  [triggered when a sign is shown]
 embodiment.mic ─▶ AsrBridge (asr-engine)
                        │  interim segment ─▶ ASR panel (live partial text)   [agent NOT triggered]
                        └─ final segment ───▶ world.update("speech_input") ──▶ WICA Agent
                                                                                ├─ say(text) ─▶ embodiment.voice ─▶ speakers / robot speaker
                                                                                ├─ embodiment.commands() ─▶ e.g. play_emotion ─▶ robot
                                                                                └─ free text ─▶ output sink (💭 thought, in the transcript)
```

The load-bearing rule: **partial speech is visible but inert; only a *closed* segment reaches the agent**, exactly once, as one complete utterance. Vision follows the same shape: the classifier runs continuously, but only a *change* reaches the World and only a sign being *shown* wakes the agent — which decides from context what the sign means, except `stop` ([gesture-input.md](gesture-input.md)).

**Echo, per embodiment.** The mic must not hear the agent's own voice. On the robot the XVF3800 does it in firmware, which is why the robot embodiment plays and listens through the bridge ([reachy-embodiment.md](reachy-embodiment.md)). On the computer, an opt-in WebRTC coordinator subtracts the TTS playback from the mic ([audio-processing.md](audio-processing.md)).

## The two modes

| Mode | Segmentation | What the person does |
|---|---|---|
| **Conversation** (default) | `utterance` — one segment per spoken sentence | Talks in short turns; each turn is a `speech_input` the agent replies to. |
| **Dictation** (agent-invoked) | `timeout` / `trigger_word` — segment closes on a pause or trigger word | Speaks a longer free-form passage that arrives as one big `speech_input`. |

Dictation is entered by the **agent**, on request, via a `start_dictation` Command; it captures a single passage and reverts automatically. A UI status indicator plus a **Stop dictation** button give a manual hatch.

## The modules

Shared concepts are top-level modules of `src/wica_interaction/`; each embodiment is a subpackage holding only its own code:

| Module | Role | Spec |
|---|---|---|
| `config.py` | The shared sections (`wica`/`asr`/`vision` → `AppConfig`), each delegated to its owner's loader; the file-reading and unknown-section rules every embodiment's loader reuses | [configuration.md](configuration.md) |
| `embodiment.py` | The `Embodiment` protocol | [embodiment.md](embodiment.md) |
| `asr_bridge.py` | Speech-in: the `ASREngine` over the embodiment's mic, segments → `speech_input` (final only) + the live view, the dictation command | [asr-bridge.md](asr-bridge.md) |
| `gesture_input.py` | Vision-in: the embodiment's camera through the hand + gesture pipeline → one `hand_gesture_<n>` sign per hand, delivered on change; the "Hand signs" persona section | [gesture-input.md](gesture-input.md) |
| `system.py` | The lifecycle: `build_system(config, embodiment)`, `wire`, start/teardown, `run_app` | [app.md](app.md) |
| `app_ui.py` | The Gradio presentation layer: the `display_entry` hook, the page (WICA's contrib panels next to the app's ASR panel + Vision line) | [app.md](app.md) |
| `computer/` | `ComputerEmbodiment` (TTS voice, processed or raw mic, webcam), its config, `audio_processing.py`, the `wica-interaction-computer` entry point | [computer-embodiment.md](computer-embodiment.md), [audio-processing.md](audio-processing.md) |
| `reachy/` | `ReachyEmbodiment` (one `ReachyMiniBridge` behind `say`, the mic and the camera), `robot_control.py`, its config, the `wica-interaction-reachy` entry point | [reachy-embodiment.md](reachy-embodiment.md) |

Plus two cross-cutting specs: [project.md](project.md) (tooling/layout/extras) and [testing.md](testing.md) (the two-tier test strategy).

## Configuration in one file

Each embodiment is stood up from a single JSON file: the shared sections `wica`, `asr`, `vision` plus the embodiment's own — `tts`, `audio_processing`, `camera` for the computer; `reachy` (a verbatim bridge config, voice included) for the robot. Each engine section holds *exactly* the document that engine's own loader expects. Switching provider, voice, ASR backend, robot backend, camera or persona is a file edit, not a code change. Keys are referenced by env-var name, never stored; a missing key surfaces as that engine's own error, and the app still opens and says what to set. One template per embodiment is committed (`config.computer.example.json`, `config.reachy.example.json`). Full detail: [configuration.md](configuration.md).

## What it is (and isn't) — v1

- **One app, several bodies.** The agent's persona, actions and senses follow the embodiment; the framework side is written once ([embodiment.md](embodiment.md)).
- **Echo handling: yes; barge-in: no.** Each embodiment keeps the mic from hearing the agent's own voice; *talking over* the agent still isn't supported — WICA is one reasoning call at a time, and inputs arriving mid-step are dropped.
- **It sees hand signs, nothing else.** A small per-hand vocabulary; the frame itself is not shown to the agent, and only `stop` comes with an instruction.
- **No manual movement/gaze on the robot in v1.** The bridge drives head tracking, presence and breathing automatically; they are not agent-facing.
- **No streamed speech, no persistence** — `say` plays a complete utterance; the World starts empty each run.

## Where to start reading

- The whole product & UI: [app.md](app.md)
- The seam between the app and a body: [embodiment.md](embodiment.md), then [computer-embodiment.md](computer-embodiment.md) / [reachy-embodiment.md](reachy-embodiment.md)
- The speech-in rule and dictation: [asr-bridge.md](asr-bridge.md)
- The hand signs: [gesture-input.md](gesture-input.md)
- The config file: [configuration.md](configuration.md)
- Status of every spec: [_index.md](_index.md); build status: [../plans/_index.md](../plans/_index.md)
