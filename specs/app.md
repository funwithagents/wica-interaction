---
code:
  - src/reachy_mini_interaction_wica/app.py
  - src/reachy_mini_interaction_wica/app_ui.py
  - prompts/assistant.md
tests:
  - tests/test_app.py
  - tests/test_app_ui.py
---

# App

**Status:** Implemented

> **Code layout.** The app is split in two modules: [app.py](../src/reachy_mini_interaction_wica/app.py) holds the **wiring + lifecycle** (`build_system`, the `AppSystem` snapshot, `wire`, `main`), and [app_ui.py](../src/reachy_mini_interaction_wica/app_ui.py) holds the **Gradio presentation layer** (the `display_entry` hook, the `build_ui` layout returning an `AppUi` handle, and the ASR live view `tick_asr` its timer polls). The three framework-observability panels — Conversation, World state, Prompt — are **not** implemented here: they are WICA's reusable `wica.contrib.gradio` components (the `wica[gradio]` extra — see [gradio-contrib.md](../../wica-framework/specs/gradio-contrib.md)), which `build_ui` places in its layout. `app` imports `build_ui` from `app_ui`; `app_ui` depends on `app` only for the `AppSystem` type, so there is no import cycle.

## Purpose

The runnable **Reachy Mini interaction application**: a Gradio UI, modeled on WICA's [conversation demo](../../wica-framework/specs/conversation-demo.md), in which a person **speaks** to a WICA agent and the robot **speaks and acts** back. It wires speech-in ([asr-bridge.md](asr-bridge.md)) and the robot (via `reachy-mini-bridge`'s `ReachyMiniApi`, backing [tts-output.md](tts-output.md) and [robot-control.md](robot-control.md)) around a WICA agent, and shows the same *observability* panels as the demo (Conversation / World / Prompt — reused from `wica.contrib.gradio`, not reimplemented) plus a live **ASR panel** of its own. Everything is stood up from one [config file](configuration.md).

This is the app the WICA demo only *simulated*: there, "speech in" was a text box and "speech out" was a fake word-by-word reveal. Here they are the robot's real microphone (ASR) and real audio on the robot speaker (TTS), and the agent can move the robot.

## Data flow

```
robot mic (echo-cancelled) ─▶ AsrBridge._on_segment
                      │  interim ─▶ ASR panel (live text)         [agent NOT triggered]
                      └─ final ──▶ world.update("speech_input") ──▶ WICA Agent reasons
typed text ─────────────────────▶ world.update("speech_input") ──▶  │  ├─ say(text) ─▶ ReachyMiniApi.say ─▶ robot speaker
                                                                     │  ├─ play_emotion / head-tracking / motors ─▶ ReachyMiniApi ─▶ robot
                                                                     └─ free text ─▶ output sink (💭 thought, the transcript's)
```

- **Speech in.** A closed ASR segment (over the robot's echo-cancelled mic stream) is written to the World's `speech_input` entry, which triggers one reasoning step. **Every** closed segment is written verbatim — including an empty/whitespace one (a passage that closed on silence), so the agent can react to the user saying nothing (see [asr-bridge.md](asr-bridge.md), "The segment → world rule"). Interim (partial) speech only updates the ASR panel — it never wakes the agent.
- **Reason.** The WICA Agent renders the World into a prompt, runs inference, and acts.
- **Speech out.** The agent speaks by calling the `say` **output Command**, which plays real TTS audio through the robot; its free text for the step becomes private reasoning delivered to the **output sink** — which is the transcript presenter's `output_sink` (`TranscriptLog`), so it shows in the Conversation as a 💭 item.
- **Act.** The agent can call the robot commands (`play_emotion`, `start_head_tracking` / `stop_head_tracking`, `set_motors_state` / `get_motors_state`) — see [robot-control.md](robot-control.md).
- **Dictation.** On request the agent calls `start_dictation` to capture a longer passage as one `speech_input` (see [asr-bridge.md](asr-bridge.md)); it does not change the data flow, only how long a segment grows before it closes.

## An expressive robot, not just a voice

Unlike the plain-assistant framing of the earlier vocal app, this app **is** the social robot: the agent talks *and* acts. What the World and the command set hold:

- **World entries:** `speech_input` (the triggering input) and the automatic `agent:command:*` entries WICA tracks for in-flight Commands (e.g. a running `say` or `play_emotion`). No mirrored robot state in v1 (motor state is read on demand via the command, not kept in the World).
- **Commands:** `say` (the TTS output Command — the voice, [tts-output.md](tts-output.md)), `start_dictation` / `stop_dictation` ([asr-bridge.md](asr-bridge.md)), the robot actions `play_emotion` / `list_emotions` / `start_head_tracking` / `stop_head_tracking` / `set_motors_state` / `get_motors_state` ([robot-control.md](robot-control.md)), plus WICA's auto-registered `noop` / `cancel_command`.
- **Persona:** whatever the config's `system_prompt_file` says — a robot that knows it speaks via `say`, can express emotions and follow a face, and can switch to dictation when asked. The committed persona, [prompts/assistant.md](../prompts/assistant.md), must also carry the three action rules below.

### The persona's action rules

WICA dispatches every Command of one response at once and lets them run concurrently, with no ordering and no exclusivity between them (see [agent.md](../../wica-framework/specs/agent.md), "Concurrency and long-running Commands in v1"); its runtime primer tells the model that much, generically. This app exposes Commands that are **not** independent — they share one robot body and one speaker — and only the persona can teach that, so [prompts/assistant.md](../prompts/assistant.md) states:

1. **No colliding Commands in one turn.** Two Commands that would collide on the robot must not be issued in the same response: `say` with `play_emotion` (the emotion's own sidecar sound plays over the speech and its motion fights the talking head — the case that motivated the rule, but not the only one), two emotions, a move with a motor-state change, a `cancel_command` with the action replacing what it cancels. The persona tells the model to issue the first, then the next in a later step once the first is observed complete, and that Commands touching different things (motors on + head tracking) may go together. This is a prompt rule, not a guard: the app does not serialize `say` and `play_emotion` (the bridge deliberately lets verbs compose — see [reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/api.md), "Cancellation"), so a model that ignores the rule still gets the overlap. A lock inside `VoiceOutput`/`RobotControl` is the deferred enforcement (open question 4).
2. **Order matters.** Dependent actions (enable motors → move; stop one thing → start the next; speak → then the emotion that goes with it) are issued in that order across turns, never all at once.
3. **Spoken feedback for every request.** Acting is not answering: when the person asks the robot to start, stop, change, or cancel something, the agent does it and then says out loud what it did once it observes it done — a request never ends in a silent action (cancelling a running `say` on request, then confirming in the next step, is the canonical example). This rides WICA's completion re-trigger: the Command's terminal entry wakes a step in which the confirmation is spoken.

These rules are persona text, verified by reading, not by a test: the app's tests drive the scripted `fake` provider and never exercise the model's compliance.

## What the user sees

Four panels. Three are WICA's reusable observability components from `wica.contrib.gradio` — each a **presenter** built over the `Wica` (it subscribes to the instrumentation Events in its constructor) plus a **panel** function called inside the `gr.Blocks` that creates the components and owns the `gr.Timer` keeping them live; the fourth, the ASR panel, is the app's own. They split into two tiers: **Conversation** and the **ASR panel** are the app's *main* surface (a voice UX needs the transcript and the live speech pipeline); **World state** and **Prompt** are *debug* surfaces, hidden by default behind a **"Show debug panels"** toggle so the framework internals are one click away without crowding the voice UI. The toggle is view-only state — it shows/hides already-instrumented panels and changes nothing about what runs.

Main surface:

1. **Conversation** — `conversation_panel(TranscriptLog(wica, display_entry))`. The reasoning trace: each closed `speech_input` shows on the input side, rendered by the app's `display_entry` hook as `🗣️ "text"`; each reasoning step is a collapsible **💬 reaction** group whose outputs are labelled by channel — **🗣️ say** (what the user heard; the hook titles the `say` execution by channel and shows the spoken text as the body), **💭 output sink** (the model's private free text), 🦾 other commands like `play_emotion` / `start_dictation`, and **🚫 noop**. Every Command item shows its **execution state live** — a spinner while it runs, then ✅ complete / ❌ failed / ⏹ cancelled with its result and duration — so a `say` is visibly *in flight* for the real duration of the speech. Only *final* segments appear here as user speech; partial speech lives in the ASR panel, not the transcript.
2. **ASR panel.** The speech pipeline made visible, plus the typed-text hatch:
   - the **live segment text** (`AsrBridge.display_text`) as it grows, cleared when the segment closes and becomes `speech_input`;
   - the **ASR status** (`AsrBridge.status()`): whether ASR is running and whether it is currently in **dictation mode** (and which segmentation mode);
   - a **"Stop dictation"** button, enabled while dictating, calling `AsrBridge.stop_dictation()`;
   - a **typed-text box** (always visible) that injects its text straight into `speech_input` as a synthetic final segment — the same World update a closed ASR segment makes — so the agent + robot half can be driven with **no microphone and no ASR key** (see "Typed-text input").

Debug surface (behind the toggle, off by default):

3. **World state (live)** — `world_state_panel(wica.world)`. The current World rendered as a table (key, value, last-changed) — `speech_input` and any running command entry — so a `say` or `play_emotion` that is "running" is visible while it runs. With no agent (see "No credentials, no robot") there is no World; the panel's place holds a one-line note instead.
4. **Prompt** — `prompt_panel(PromptLog(wica, display_entry))`. The exact prompt sent to the model, read-only, with a dropdown of every step's prompt (labelled by time + trigger, the trigger rendered through the same `display_entry` hook so it reads like the transcript) that auto-snaps to the newest.

**The `display_entry` hook** is the one place the app injects persona knowledge into the generic panels: `speech_input` → `🗣️ "<text>"`; a `say` Command execution → title `🗣️ say`, body the spoken `text`; anything else → `None` (the contrib's generic default: `⚡ key = value`, `🦾 name(args)`). It is the person-facing counterpart of the `speech_input` `serialize_fn` in `app.py` (the same per-entry knowledge, rendered for a human instead of the model), and the *same* hook is passed to both presenters so transcript items and prompt labels agree. It lives in `app_ui.py` (presentation), which is what `build_ui` and the tests reach for.

**Refresh cost.** Each contrib panel owns its own `gr.Timer` (0.2 s) so the surfaces never share one round-trip, and each pushes only what changed: the Conversation panel bumps a version only when the transcript's change signature moves (new items, or a Command item edited in place as it runs) so `gr.Chatbot`'s autoscroll never fights a reader who scrolled up; the Prompt panel pushes only on a new step; the World panel re-renders its (tiny) table whole every tick, so a fleeting command row is never dropped. The ASR panel's timer is the app's own (it reads the bridge). The debug toggle is **visibility only** — a hidden World panel keeps ticking; the app no longer pauses or change-gates that timer itself, since the panel owns it (pausing it would mean reimplementing the panel; the contrib defers a shared/injectable timer — see its open questions — and that is where such an optimization belongs).

## Typed-text input

A text box in the ASR panel, **always visible**, is the app's keyboard hatch into the agent. Submitting it writes the text to `speech_input` exactly as a closed ASR segment would — the *same* World update, so it flows through the identical reason → say/act → sink path and shows in the Conversation transcript as ordinary user speech. It is a first-class way in for anyone without a working mic, and — because it needs neither the ASR engine nor an ASR key — it is the path by which the app stays useful when only the WICA (and robot) side is available. It does **not** go through `AsrBridge` (no segment, no interim display); it calls `world.update("speech_input", text)` directly. One difference from a closed segment: an **empty/whitespace typed line is ignored** — it has nothing to reply to. That guard belongs to the typed-text hatch alone; it never gates ASR finals, which reach `speech_input` even when empty. It shares the one-call-at-a-time limit with spoken input: text submitted while a step is in flight is dropped, same as a segment would be.

## Wiring and lifecycle

`main()` composes the app in the demo's fixed order — **build → UI → wire → start → launch → close** — which is exactly what WICA's output-wiring setters exist to allow (`Wica.init` takes **no application callables**; the output sink and the output Command are wired afterwards through `wica.set_output_sink(...)` / `wica.set_output_command(...)` — see [wica.md](../../wica-framework/specs/wica.md), "Output wiring is delegated"). Nothing is bound late and nothing is built early to be back-patched: every object is built after the ones it needs, and everything is wired before `start()`.

`main()` takes a **required** `--config` path. There is no default — a bare run exits with argparse's usage message rather than silently loading anything — since the committed `config.example.json` is a template to copy and adapt (which robot backend, which providers), not a config anyone should run unmodified (copy the example and pass it explicitly). It ships pointed at a headful `sim` daemon (`reachy.backend = "sim"`, `reachy.daemon = {"spawn": "auto", "headless": false}`) so a fresh clone can watch the robot move in the MuJoCo viewer without touching real hardware; point `reachy.backend` at `"real"` for the physical robot.

**1. Build — `build_system(config)` → `AppSystem`.** Gradio-free; builds and *enters* the system but wires no output and starts nothing:

1. `load_config(path)` → `AppConfig(wica, asr_engine, reachy)` (see [configuration.md](configuration.md)).
2. Create the app-owned loop and start its daemon thread (see "One event loop").
3. Build the **one `ReachyMiniApi`**: `ReachyMiniApi(cfg.reachy)` — the bridge builds the voice from `cfg.reachy.tts` at construction, degrading to no voice on a build failure (recorded as `api.synthesizer_error`; see "No credentials, no robot") — and **enter it on the loop** (`run_coroutine_threadsafe(api.__aenter__(), loop)`) so the daemon (when configured), the robot connection, and the media session all come up and its async verbs bind to that loop. Construction never connects; only `__aenter__` does. This single instance backs `say`, the mic stream, and the robot commands.
4. Build `VoiceOutput(api)` (the `say` output Command) — built, not yet set on the agent.
5. `Wica.init(cfg.wica, loop=loop)`, then `wica.world.register("speech_input", str, serialize_fn=…, triggers_llm_call=True)`. No sink, no output Command, no Events subscribed here.
6. Build one `mic = RobotMicSource(api)`, then `ASREngine(cfg.asr_engine, audio_source=mic)` and `AsrBridge(engine, on_final_segment=…, mic=mic)`, whose callback writes every final verbatim to `speech_input` — the same World update the typed-text box makes, minus the typed path's empty-input guard. The bridge is built **after** WICA so its callback resolves against a live agent with no placeholder.

The returned `AppSystem` is a frozen snapshot: `loop`, `api`, `wica`, `bridge`, `voice`, `missing` (the banner lines), and the typed-text `submit_speech` hatch. Any component that failed to build is `None` with its reason in `missing`.

**2. UI — `build_ui(system)` → `AppUi(blocks, transcript)`.** The UI **owns its presenters**: it constructs `TranscriptLog(system.wica, display_entry)` and `PromptLog(system.wica, display_entry)` — each subscribes to the Wica's Events in its constructor, so the Wica must exist first, which is why the UI is built after the system — then lays out the page (the three contrib panels, the ASR panel, the typed-text hatch, the banner, the debug toggle) and returns the `gr.Blocks` plus the transcript on a small handle, so the app can wire the transcript's sink. Input widgets only touch `system.submit_speech` / the bridge; they never wire anything.

**3. Wire — `wire(system, transcript)`.** Gradio-free (it takes the presenter, not the page — the tests call it with a `TranscriptLog` they build themselves, playing the UI's role). On the agent: `wica.set_output_sink(transcript.output_sink)` (the model's free text → the 💭 item); `wica.set_output_command(voice.say)` when the voice exists (the user-facing channel; setting it recomposes the system prompt to name the Command, which is why it must precede `start()`); and `register_command` for `start_dictation` / `stop_dictation` (when the bridge exists) and the `RobotControl` actions (when the robot exists). A no-op without an agent — there is nothing to wire into.

**4. Start, launch, close — `_serve(system, blocks)`.** `wica.start()`, then schedule `asr_bridge.start()` onto the loop with `run_coroutine_threadsafe`, then `blocks.launch()`. Starting and launching both sit **inside** the `try` whose `finally` runs teardown: `build_system` has already entered the robot's media session, so a raise in `wica.start()` / `asr_bridge.start()` still exits it and stops the loop. Teardown (`_shutdown`) runs on the Gradio thread once `launch()` returns (never from a sink, Command, or Event subscriber — those run on the loop). It closes WICA, then — only if `asr_bridge.status()["running"]` (a bridge can be built but never started) — schedules `asr_bridge.stop()` onto the loop, then `__aexit__`s the api on the loop, then stops the loop — the bridge before the api, so the mic pump is cancelled while the media session is still open. Each step runs **in isolation**, and each loop hop is bounded by a timeout (5 s): a step that raises or hangs is logged and the remaining steps still run, so one failure cannot strand the robot session or the loop thread.

The ordering rules this sequence obeys, all from the contrib's contract: presenters after `Wica.init` (they subscribe in their constructor); sink, output Command and Commands wired **before `start()`**; the order *among* those three calls is free, as is the order between registering World entries and building presenters; `world.update()` works only while running, so start before `launch()`; one `display_entry` hook for both presenters.

**One event loop — the app owns it; WICA and the robot adopt it.** A single asyncio loop runs the Agent, the `ReachyMiniApi` (its `say`, mic tap, and verbs are all async), and the ASR engine. This is not optional: the `ASREngine` binds `asyncio.get_running_loop()` when it starts, the mic tap streams on that loop, and `say`/robot verbs run there as Commands. The concrete arrangement is forced by one fact — **`Wica` exposes no public accessor for the loop it creates** — so the only way the app can also run the engines and the robot on that loop is to *own* it and inject it:

- `main()` creates the loop (`asyncio.new_event_loop()`) and runs it in its own daemon thread (`threading.Thread(target=loop.run_forever, daemon=True)`), then passes it as `Wica.init(…, loop=loop)`. WICA then *adopts* the injected loop and leaves the thread to the app (with an injected loop, `wica.start()` does not spawn a loop thread of its own).
- The `ReachyMiniApi` async context and the engine coroutines are scheduled onto that loop with `asyncio.run_coroutine_threadsafe(coro, loop)` from the main/Gradio thread: `api.__aenter__()` and `asr_bridge.start()` at startup, `asr_bridge.stop_dictation()` from the Stop button, `asr_bridge.stop()` / `api.__aexit__()` at teardown. `say` and the robot commands need no such hop — WICA invokes them on the loop.
- The ASR callback runs on that loop and calls `world.update("speech_input", …)`, which is thread-safe and, being already on the loop, has no cross-loop hazard.

## No credentials, no robot

Like the WICA demo, the app **opens even with keys unset (or no robot reachable)** and says what to fix, rather than crashing. `build_system` degrades along three independent axes, each captured as one line in the UI banner, and nothing contacts a model, ASR, TTS provider, or robot without its prerequisite in place:

- **Agent key missing or agent config unusable** (`Wica.init` raises `MissingEnvError`; any other `ConfigError`, e.g. a missing/unreadable `system_prompt_file`; or `ImportError` for a missing provider extra) — `Agent: …`. Nothing reasons; ASR capture is not started either (there is no consumer for speech). Everything else still builds. The UI's presenters are built over `wica=None` — the contrib's explore-only mode: they subscribe to nothing and render empty — the World panel's place shows a note (there is no World), and `wire` is a no-op.
- **TTS key missing, or no `reachy.tts` block at all** — `TTS: …`. The bridge builds the voice from `cfg.reachy.tts` at construction; a build failure (typically the key unset) does not raise — it degrades to no voice, and after a successful construct + enter the app reads `api.synthesizer_error` and appends `TTS: {error}`, or, when the config carries no `tts` block at all, `TTS: no reachy.tts block configured (say is unavailable)`. Either way the robot is still up: the mic, the ASR bridge, and the robot action commands are unaffected; `say` is set as the output Command and returns `"TTS error: …"` when called (the bridge's `say` raises `BridgeError` without a synthesizer). This check is skipped when a test injects `api=` (the fake carries its own `say`).
- **Robot unreachable** (`__aenter__` fails — bringing up a managed daemon, connecting the robot, or opening the media session; construction itself never connects) — `Robot: …`. The enter is awaited without an app-side timeout, so a `sim` daemon the bridge spawns but that never becomes ready fails only after the bridge's `reachy.daemon.startup_timeout` (45 s by default) — the UI opens after that wait, with the banner entry. There is no `ReachyMiniApi`, so the `say` voice and the robot action commands are **not wired** and no ASR bridge is built (its mic is the robot's). Typed text still drives the agent, so reasoning is visible; the agent simply has nothing to speak or move through.

ASR's own key (`ASR: …`) degrades only speech-in — typed text remains the way in. For offline development, `reachy.backend = "fake"` gives a robot with no hardware; `reachy.backend = "sim"` with `reachy.daemon.spawn = "auto"` spawns (or borrows) a local MuJoCo daemon — `daemon.headless = false` opens its viewer (the committed `config.example.json`'s default), `true` runs it windowless.

## Testing

Fast, deterministic, no browser/mic/network/robot (see [testing.md](testing.md)). The wiring tests stand the real app up through the same Gradio-free steps `main()` uses — `build_system(config)` (with an injected fake `ReachyMiniApi` and fake `ASREngine`), then `wire(system, transcript)` with a `TranscriptLog` (and, where a test reads prompts, a `PromptLog`) the test builds itself over `system.wica`, exactly what `build_ui` would create, then `_start` — against WICA's scripted `fake` provider, and assert on the robot side effects (`say` reaching the speaker, an action reaching the robot) and on the presenters' `snapshot()`s (the transcript's input items, the 💭 sink item proving the sink is wired, a step's prompt captured). The degrade and teardown paths are asserted the same way. `build_ui` itself is not driven (no browser); the presentation module's tests cover the `display_entry` hook and the ASR status line. The three contrib panels are tested in WICA's own suite, not here.

## Out of scope (v1)

- **Barge-in:** *talking over* the agent is not handled — WICA is one reasoning call at a time, and inputs arriving mid-step are dropped (see [asr-bridge.md](asr-bridge.md)). (The mic hearing the robot's own TTS *is* handled — on the robot, by the XVF3800.)
- **Streamed speech:** `say` plays a complete utterance; no word-by-word UI reveal or token streaming.
- **Vision / camera and manual movement/gaze:** not wired (see [robot-control.md](robot-control.md) and [_overview.md](_overview.md)).
- **Persistence:** the World starts empty each run.

## Open questions

1. **Robot state in the UI.** Whether to surface the robot's connection / motor state in the ASR (or a new) panel — the app holds the `ReachyMiniApi` handle, so it is easy — is a nice-to-have, not wired.
2. **Seeding emotion names into the prompt.** Whether `main()` calls `api.list_emotions()` at startup to fold the names into the system prompt — deferred; v1 relies on the `list_emotions` command (see [robot-control.md](robot-control.md) open question 1).
3. **Pausing the hidden World panel.** Adopting `world_state_panel` gave up the app's earlier pause-while-hidden / change-gated World timer. If three always-on timers ever prove costly, the fix is the contrib's deferred injectable `timer=` (or an `active` flag), not a local reimplementation over `world_html`. Deferred until measured.
4. **Enforcing the no-collision rule.** The persona rule above is advisory. If the model keeps issuing `say` + `play_emotion` together, the enforcement is a shared `asyncio.Lock` held by `VoiceOutput.say` and `RobotControl.play_emotion` (the two speaker/head-spanning verbs), so the second waits for the first in issue order, both still showing `running` in the World. Deferred until observed to be needed.
