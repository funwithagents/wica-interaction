---
code:
  - src/reachy_mini_interaction_wica/app.py
  - src/reachy_mini_interaction_wica/app_ui.py
tests:
  - tests/test_app.py
---

# App

**Status:** Implemented

> **Code layout.** The app is split in two modules: [app.py](../src/reachy_mini_interaction_wica/app.py) holds the **wiring + lifecycle** (`build_system`, the `AppSystem` snapshot, `main`), and [app_ui.py](../src/reachy_mini_interaction_wica/app_ui.py) holds the **Gradio presentation layer** (the instrumentation `PanelState`, the `build_ui` layout, and the live views — `tick_world` / `tick_asr` — the timers poll). `app` imports `PanelState` / `build_ui` from `app_ui`; `app_ui` depends on `app` only for the `AppSystem` type, so there is no import cycle.

## Purpose

The runnable **Reachy Mini interaction application**: a Gradio UI, modeled on WICA's [conversation demo](../../wica-framework/specs/conversation-demo.md), in which a person **speaks** to a WICA agent and the robot **speaks and acts** back. It wires speech-in ([asr-bridge.md](asr-bridge.md)) and the robot (via `reachy-mini-bridge`'s `ReachyMiniApi`, backing [tts-output.md](tts-output.md) and [robot-control.md](robot-control.md)) around a WICA agent, and keeps the demo's *observability* panels (Conversation / World / Prompt) plus a live **ASR panel**. Everything is stood up from one [config file](configuration.md).

This is the app the WICA demo only *simulated*: there, "speech in" was a text box and "speech out" was a fake word-by-word reveal. Here they are the robot's real microphone (ASR) and real audio on the robot speaker (TTS), and the agent can move the robot.

## Data flow

```
robot mic (echo-cancelled) ─▶ AsrBridge._on_segment
                      │  interim ─▶ ASR panel (live text)         [agent NOT triggered]
                      └─ final ──▶ world.update("speech_input") ──▶ WICA Agent reasons
typed text ─────────────────────▶ world.update("speech_input") ──▶  │  ├─ say(text) ─▶ ReachyMiniApi.say ─▶ robot speaker
                                                                     │  ├─ play_emotion / head-tracking / motors ─▶ ReachyMiniApi ─▶ robot
                                                                     └─ free text ─▶ output_sink (💭 thought)
```

- **Speech in.** A closed ASR segment (over the robot's echo-cancelled mic stream) is written to the World's `speech_input` entry, which triggers one reasoning step. Interim (partial) speech only updates the ASR panel — it never wakes the agent.
- **Reason.** The WICA Agent renders the World into a prompt, runs inference, and acts.
- **Speech out.** The agent speaks by calling the `say` **output Command**, which plays real TTS audio through the robot; its free text for the step becomes private reasoning delivered to the `output_sink`.
- **Act.** The agent can call the robot commands (`play_emotion`, `start_head_tracking` / `stop_head_tracking`, `set_motors_state` / `get_motors_state`) — see [robot-control.md](robot-control.md).
- **Dictation.** On request the agent calls `start_dictation` to capture a longer passage as one `speech_input` (see [asr-bridge.md](asr-bridge.md)); it does not change the data flow, only how long a segment grows before it closes.

## An expressive robot, not just a voice

Unlike the plain-assistant framing of the earlier vocal app, this app **is** the social robot: the agent talks *and* acts. What the World and the command set hold:

- **World entries:** `speech_input` (the triggering input) and the automatic `agent:command:*` entries WICA tracks for in-flight Commands (e.g. a running `say` or `play_emotion`). No mirrored robot state in v1 (motor state is read on demand via the command, not kept in the World).
- **Commands:** `say` (the TTS output Command — the voice, [tts-output.md](tts-output.md)), `start_dictation` / `stop_dictation` ([asr-bridge.md](asr-bridge.md)), the robot actions `play_emotion` / `list_emotions` / `start_head_tracking` / `stop_head_tracking` / `set_motors_state` / `get_motors_state` ([robot-control.md](robot-control.md)), plus WICA's auto-registered `noop` / `cancel_command`.
- **Persona:** whatever the config's `system_prompt_file` says — a robot that knows it speaks via `say`, can express emotions and follow a face, and can switch to dictation when asked.

## What the user sees

Four panels, reusing the demo's layout and instrumentation (`on_agent_trigger` / `on_agent_prompt` / `on_agent_command` Events feed the panels; a `gr.Timer` polls live state). They split into two tiers: **Conversation** and the **ASR panel** are the app's *main* surface (a voice UX needs the transcript and the live speech pipeline); **World state** and **Prompt** are *debug* surfaces, hidden by default behind a **"Show debug panels"** toggle so the framework internals are one click away without crowding the voice UI. The toggle is view-only state — it shows/hides already-instrumented panels and changes nothing about what runs.

Main surface:

1. **Conversation.** The reasoning trace: each closed `speech_input` shows on the input side; each reasoning step is a collapsible reaction whose outputs are labelled by channel — **🗣️ say** (what the user heard, via the output Command), **💭 output sink** (the model's private free text), 🦾 other commands like `play_emotion` / `start_dictation`, and **🚫 noop**. Only *final* segments appear here as user speech; partial speech lives in the ASR panel, not the transcript.
2. **ASR panel.** The speech pipeline made visible, plus the typed-text hatch:
   - the **live segment text** (`AsrBridge.display_text`) as it grows, cleared when the segment closes and becomes `speech_input`;
   - the **ASR status** (`AsrBridge.status()`): whether ASR is running and whether it is currently in **dictation mode** (and which segmentation mode);
   - a **"Stop dictation"** button, enabled while dictating, calling `AsrBridge.stop_dictation()`;
   - a **typed-text box** (always visible) that injects its text straight into `speech_input` as a synthetic final segment — the same World update a closed ASR segment makes — so the agent + robot half can be driven with **no microphone and no ASR key** (see "Typed-text input").

Debug surface (behind the toggle, off by default):

3. **World state (live).** The current World rendered as a table (key, value, last-changed) — `speech_input` and any running command entry — so a `say` or `play_emotion` that is "running" is visible while it runs.
4. **Prompt.** The exact prompt sent to the model, read-only, with a dropdown of every step's prompt (labelled by time + trigger) that auto-snaps to the newest.

## Typed-text input

A text box in the ASR panel, **always visible**, is the app's keyboard hatch into the agent. Submitting it writes the text to `speech_input` exactly as a closed ASR segment would — the *same* World update, so it flows through the identical reason → say/act → sink path and shows in the Conversation transcript as ordinary user speech. It is a first-class way in for anyone without a working mic, and — because it needs neither the ASR engine nor an ASR key — it is the path by which the app stays useful when only the WICA (and robot) side is available. It does **not** go through `AsrBridge` (no segment, no interim display); it calls `world.update("speech_input", text)` directly. It shares the one-call-at-a-time limit with spoken input: text submitted while a step is in flight is dropped, same as a segment would be.

## Wiring and lifecycle

`main()` builds the system from a `--config` path:

1. `load_config(path)` → `AppConfig(wica, asr_engine, tts_engine, reachy)` (see [configuration.md](configuration.md)).
2. Create the app-owned loop and start its daemon thread (see "One event loop").
3. Build the **one `ReachyMiniApi`**: `ReachyMiniApi(cfg.reachy.backend, synthesizer=TTSEngineSynthesizer(cfg.tts_engine))`, and **enter it on the loop** (`run_coroutine_threadsafe(api.__aenter__(), loop)`) so its media session opens and its async verbs bind to that loop. This single instance backs `say`, the mic stream, and the robot commands.
4. Build `VoiceOutput(api)` (the `say` output Command) and `RobotControl(api)` (the action commands).
5. `Wica.init(cfg.wica, output_sink=…, output_command=voice.say, loop=loop)`; subscribe the panels to the instrumentation Events; `wica.world.register("speech_input", str, serialize_fn=…, triggers_llm_call=True)`.
6. Build `ASREngine(cfg.asr_engine, audio_source=RobotMicSource(api))` and `AsrBridge(engine, on_final_segment=…)`, whose callback writes `speech_input` — the same funnel the typed-text box calls. Register `start_dictation` / `stop_dictation` and the `RobotControl` commands. The bridge is built **after** WICA so its callback resolves against a live agent with no placeholder.
7. `wica.start()`, then schedule `asr_bridge.start()` onto the loop with `run_coroutine_threadsafe`, then launch the Gradio UI. Teardown `__aexit__`s the api on the loop.

**One event loop — the app owns it; WICA and the robot adopt it.** A single asyncio loop runs the Agent, the `ReachyMiniApi` (its `say`, mic tap, and verbs are all async), and the ASR engine. This is not optional: the `ASREngine` binds `asyncio.get_running_loop()` when it starts, the mic tap streams on that loop, and `say`/robot verbs run there as Commands. The concrete arrangement is forced by one fact — **`Wica` exposes no public accessor for the loop it creates** — so the only way the app can also run the engines and the robot on that loop is to *own* it and inject it:

- `main()` creates the loop (`asyncio.new_event_loop()`) and runs it in its own daemon thread (`threading.Thread(target=loop.run_forever, daemon=True)`), then passes it as `Wica.init(…, loop=loop)`. WICA then *adopts* the injected loop and leaves the thread to the app (with an injected loop, `wica.start()` does not spawn a loop thread of its own).
- The `ReachyMiniApi` async context and the engine coroutines are scheduled onto that loop with `asyncio.run_coroutine_threadsafe(coro, loop)` from the main/Gradio thread: `api.__aenter__()` and `asr_bridge.start()` at startup, `asr_bridge.stop_dictation()` from the Stop button, `asr_bridge.stop()` / `api.__aexit__()` at teardown. `say` and the robot commands need no such hop — WICA invokes them on the loop.
- The ASR callback runs on that loop and calls `world.update("speech_input", …)`, which is thread-safe and, being already on the loop, has no cross-loop hazard.

## No credentials, no robot

Like the WICA demo, the app **opens even with keys unset (or no robot reachable)** and says what to fix, rather than crashing: `Wica.init` raises `MissingEnvError` for an unset agent key; the ASR module and the bridge's synthesizer raise when they resolve their key; and connecting a `real` robot fails if no daemon is reachable. `main()` catches these at build, still launches the UI, and shows what is missing — nothing contacts a model, ASR, TTS provider, or robot without the prerequisites being in place. Typed text still reaches the agent whenever WICA is up, so the reason → say/act path is exercisable as far as each present prerequisite allows (e.g. WICA up but no robot: reasoning is visible even if `say`/actions report the robot missing). For offline development, `reachy.backend = "fake"` (or `"sim"`) gives a robot with no hardware.

## Out of scope (v1)

- **Barge-in:** *talking over* the agent is not handled — WICA is one reasoning call at a time, and inputs arriving mid-step are dropped (see [asr-bridge.md](asr-bridge.md)). (The mic hearing the robot's own TTS *is* handled — on the robot, by the XVF3800.)
- **Streamed speech:** `say` plays a complete utterance; no word-by-word UI reveal or token streaming.
- **Vision / camera and manual movement/gaze:** not wired (see [robot-control.md](robot-control.md) and [_overview.md](_overview.md)).
- **Persistence:** the World starts empty each run.

## Open questions

1. **Robot state in the UI.** Whether to surface the robot's connection / motor state in the ASR (or a new) panel — the app holds the `ReachyMiniApi` handle, so it is easy — is a nice-to-have, not wired.
2. **Seeding emotion names into the prompt.** Whether `main()` calls `api.list_emotions()` at startup to fold the names into the system prompt — deferred; v1 relies on the `list_emotions` command (see [robot-control.md](robot-control.md) open question 1).
