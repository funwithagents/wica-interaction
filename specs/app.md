---
code:
  - src/wica_interaction/system.py
  - src/wica_interaction/app_ui.py
  - prompts/computer.md
  - prompts/reachy.md
tests:
  - tests/test_system.py
  - tests/test_app_ui.py
---

# App

**Status:** Implemented

> **Code layout.** [system.py](../src/wica_interaction/system.py) holds the **wiring + lifecycle** (`build_system`, the `AppSystem` snapshot, `wire`, `_start`, `_shutdown`, `run_app`); [app_ui.py](../src/wica_interaction/app_ui.py) the **Gradio presentation layer** (the `display_entry` hook, `build_ui` returning an `AppUi` handle, the ASR/Vision live views its timer polls); each embodiment's `app.py` is a `main()` of a few lines that loads its config, constructs its embodiment, and calls `run_app`. The three framework-observability panels — Conversation, World state, Reaction — are WICA's reusable `wica.contrib.gradio` components (the `wica[gradio]` extra — [gradio-contrib.md](../../wica-framework/specs/gradio-contrib.md)), which `build_ui` places. `system` imports `build_ui` from `app_ui`; `app_ui` depends on `system` only for the `AppSystem` type, so there is no import cycle.

## Purpose

The runnable **interaction application**: a Gradio UI, modeled on WICA's [conversation demo](../../wica-framework/specs/conversation-demo.md), in which a person **speaks** to a WICA agent and the agent **speaks (and, embodied in a robot, acts) back**. It wires speech-in ([asr-bridge.md](asr-bridge.md)), vision-in ([gesture-input.md](gesture-input.md)) and an **embodiment** ([embodiment.md](embodiment.md) — the computer's mic/speakers/webcam, or the Reachy Mini) around a WICA agent, and shows the demo's *observability* panels (Conversation / World / Reaction, reused from `wica.contrib.gradio`) plus a live **ASR panel** of its own. Everything is stood up from one [config file](configuration.md).

This is the app the WICA demo only *simulated*: there, "speech in" was a text box and "speech out" a fake word-by-word reveal. Here they are a real microphone and real audio — and, on the robot, the agent can move.

## Data flow

```
embodiment.mic ─▶ ASREngine ─▶ AsrBridge._on_segment
                      │  interim ─▶ ASR panel (live text)         [agent NOT triggered]
                      └─ final ──▶ world.update("speech_input") ──▶ WICA Agent reasons
typed text ─▶ strip, drop empty ─▶ world.update("speech_input") ──▶  │  ├─ say(text) ─▶ embodiment.voice ─▶ speakers / robot
embodiment.camera ─▶ GestureInput ─ (hand, sign/None) on change ─▶ world.update("hand_gesture_<n>") ─┤  (triggered when a sign is shown)
                                                                     │  ├─ embodiment.commands() ─▶ e.g. play_emotion ─▶ robot
                                                                     └─ free text ─▶ output sink (💭 thought, the transcript's)
```

- **Speech in.** A closed ASR segment is written to the World's `speech_input` entry, which triggers one reasoning step. **Every** closed segment is written verbatim — including an empty/whitespace one (a passage that closed on silence), so the agent can react to the person saying nothing ([asr-bridge.md](asr-bridge.md)). Interim speech only updates the ASR panel. Typed text makes the same World update, minus empty lines (see "The input funnel").
- **Vision in.** The embodiment's camera runs through `vision-modules`' hand + gesture pipeline; `GestureInput` reduces each published hand to a sign (`stop`, `like`, `dislike`, `ok`, `one` … `five`) or `None`, holds it for `vision.hold_s` (0.5 s by default) so a flicker never counts, and writes that hand's `hand_gesture_<n>` entry **on change only**; the entries' trigger predicate wakes the agent whenever a hand shows a sign, riding the normal coalescing window ([gesture-input.md](gesture-input.md)).
- **Reason.** The WICA Agent renders the World into a prompt, runs inference, and acts.
- **Speech out.** The agent speaks by calling the `say` **output Command** — the embodiment's `voice` — which plays real audio; its free text for the step becomes private reasoning delivered to the **output sink**, the transcript presenter's, so it shows in the Conversation as a 💭 item.
- **Act.** The agent can call whatever Commands the embodiment registers — on the robot `play_emotion` / `set_motors_state` / `get_motors_state` ([reachy-embodiment.md](reachy-embodiment.md)); on the computer none.
- **Dictation.** On request the agent calls `start_dictation` to capture a longer passage as one `speech_input` ([asr-bridge.md](asr-bridge.md)).

## World, Commands, persona

- **World entries:** `speech_input` (the triggering input), `hand_gesture_1` … `hand_gesture_<max_hands>` (each hand's sign, ambient in every prompt, registered only when vision is on), and the automatic `agent:command:*` entries WICA tracks for in-flight Commands. No mirrored embodiment state.
- **Commands:** `say` (the embodiment's voice), `start_dictation` / `stop_dictation` ([asr-bridge.md](asr-bridge.md)), the embodiment's own action Commands, plus WICA's auto-registered `noop` / `cancel_command`.
- **Persona:** whatever the config's `system_prompt_file` says — one file per embodiment ([prompts/computer.md](../prompts/computer.md), [prompts/reachy.md](../prompts/reachy.md)), each knowing it speaks via `say`, may choose not to react, and can switch to dictation when asked (and that an empty dictated passage means the person paused). The robot persona adds how it moves and the action rules its colliding Commands need ([reachy-embodiment.md](reachy-embodiment.md), "Persona"). Two sections are **appended at startup**, not written in the file: the embodiment's `prompt_addendum` (the robot's emotion names) and, when vision is on, the "Hand signs" section from [gesture-input.md](gesture-input.md) — so a persona never describes hands it cannot see or emotions it cannot play.

## What the user sees

Four panels. Three are WICA's reusable observability components from `wica.contrib.gradio` — each a **presenter** built over the `Wica` (subscribing to the instrumentation Events in its constructor) plus a **panel** function called inside the `gr.Blocks` that creates the components and owns the `gr.Timer` keeping them live; the fourth, the ASR panel, is the app's own. **Conversation** and the **ASR panel** are the *main* surface; **World state** and **Reaction** are *debug* surfaces, hidden by default behind a **"Show debug panels"** toggle — view-only state. The page is named after the body it was launched for — title `WICA Interaction — <display_name>`, heading "Talk to a WICA agent embodied in a `<display_name>`" — from `AppSystem.display_name`, which survives a failed `enter()` (the banner says why the body is absent). Only a config that never loaded has no body to name: the page is then plainly `WICA Interaction` / "Talk to a WICA agent".

Main surface:

1. **Conversation** — `conversation_panel(TranscriptLog(wica, display_entry))`. Each closed `speech_input` shows on the input side as `🗣️ "text"`; each reasoning step is a collapsible **💬 reaction** group whose outputs are labelled by channel — **🗣️ say** (what the person heard; the hook titles the `say` execution by channel and shows the spoken text as the body), **💭 output sink** (private free text), 🦾 other commands, **🚫 noop**. Every Command item shows its **execution state live** — a spinner while it runs, then ✅ complete / ❌ failed / ⏹ cancelled with result and duration — so a `say` is visibly *in flight* for the real duration of the speech.
2. **ASR panel.** The speech pipeline made visible, plus the typed-text hatch: the **live segment text** (`AsrBridge.display_text`); the **ASR status** (`AsrBridge.status()`: running, dictating and which mode, and a `⚠️ capture:` tail when the embodiment reports a `mic_error`); a **"Stop dictation"** button; the **typed-text box** (always visible); and the **Vision line** (`tick_vision`, same timer) under its own heading: whether the gesture input runs, every published hand's raw top label and score in slot order under the configured cap, every hand's last delivered sign, each node's achieved rate next to its configured one, and a `⚠️ camera:` / `⚠️ error:` tail when the camera source fails or a stage errored — `_Vision off._` without a `GestureInput`. When vision is on, the same block carries the hand demo's **snapshot controls** (a folder box, **Save detector snapshot** / **Save classifier snapshot**, a status line) — [gesture-input.md](gesture-input.md), "`GestureInput`".

Debug surface (behind the toggle):

3. **World state (live)** — `world_state_panel(wica.world)`. With no agent there is no World; the panel's place holds a one-line note.
4. **Reaction** — `reaction_panel(ReactionLog(wica, display_entry))`: every reasoning step, auto-snapping to the newest, with **Prompt** and **Instrumentation** tabs.

**The `display_entry` hook** is the one place the app injects persona knowledge into the generic panels: `speech_input` → `🗣️ "<text>"`; `hand_gesture_<n>` → `✋ hand <n>: <sign>` / `✋ hand <n>: no sign`; a `say` execution → title `🗣️ say`, body the spoken `text`; anything else → `None` (the contrib's generic default). It is the person-facing counterpart of the `speech_input` `serialize_fn` in `system.py`, and the *same* hook goes to both presenters so transcript items and reaction labels agree. Nothing in it is embodiment-specific.

**Refresh cost.** Each contrib panel owns its own `gr.Timer` (0.2 s) and pushes only what changed (the transcript on a change signature, the Reaction panel on a new step, the World table whole each tick). The ASR panel's timer is the app's own (it reads the bridge and the gesture input). The debug toggle is **visibility only** — a hidden World panel keeps ticking; a shared/injectable timer is the contrib's deferral, not this app's.

**The banner.** When `system.missing` is non-empty the page opens with a banner listing its lines and a generic explanation: the app still opens, typed text drives the agent wherever its own key is set, and what each line means for the embodiment at hand (no body ⇒ nothing to speak or move through; a body with no voice ⇒ `say` stays wired and reports a TTS error — [embodiment.md](embodiment.md)). The shared page carries no body-specific wording: an offline-development hint is part of the embodiment's own error message (the robot appends `reachy.backend = "fake"` / `"sim"` to a failed connection — [reachy-embodiment.md](reachy-embodiment.md)), which the banner shows verbatim.

## Typed-text input

A text box in the ASR panel, **always visible**, is the app's keyboard hatch into the agent. Submitting it writes the text to `speech_input` exactly as a closed ASR segment would — the *same* World update, so it flows through the identical reason → say/act → sink path and shows in the transcript as ordinary speech. It needs neither the ASR engine nor an ASR key, so it is the path by which the app stays useful with only the agent key set. It does **not** go through `AsrBridge`; it calls the funnel directly. One difference from a closed segment: an **empty/whitespace typed line is ignored** — that guard belongs to the typed path alone and never gates ASR finals. It shares the one-call-at-a-time limit with spoken input.

## The input funnel: `_deliver_speech` / `submit_speech`

Both ways in write `speech_input` through one guarded World write, built by `build_system` as two closures over the agent — the only code that writes `speech_input`, so the paths cannot drift:

- **`_deliver_speech(text)`** — the ASR path, wired as the bridge's `on_final_segment`: writes the transcript **verbatim**, empty included (the prompt renders `""` as `The person said to you: ""`, distinct from the unset `None`). Guarded on a running agent — `world.update` raises while stopped, so input before `start()` or after teardown, or with no agent, is dropped, not raised.
- **`submit_speech(text)`** — the typed path, exposed as `AppSystem.submit_speech`: strips, drops empty, then `_deliver_speech`.

`speech_input` is registered with `triggers_llm_call=True` and no trigger condition, so repeating the same text triggers again.

## Wiring and lifecycle

The composition follows the demo's **build → UI → wire → start → launch → close**, the order WICA's output-wiring setters exist for (`Wica.init` takes no application callables; `set_output_sink` / `set_output_command` come afterwards, before `start()` — [wica.md](../../wica-framework/specs/wica.md)). Nothing is bound late and nothing back-patched: every object is built after the ones it needs, and everything is wired before `start()`.

**0. The entry point — `run_app(description, load)`.** Each embodiment's `main()` calls `run_app` with its argparse description and a `load(path) -> tuple[AppConfig, Embodiment]` that runs its own `load_config` and constructs its embodiment (no I/O in the constructor). `run_app` takes a **required** `--config` (no default: the committed example files are templates to copy and adapt), calls `load`, degrades a `ValueError` into `_degraded_system(message)` (a do-nothing system carrying only the banner line), else `build_system(app_config, embodiment)`; then `_serve(system)`, which owns everything after the build (step 4).

**1. Build — `build_system(config: AppConfig, embodiment: Embodiment, *, asr_engine_factory=…, gesture_models=None) -> AppSystem`.** Gradio-free; builds and *enters* the system but wires no output and starts nothing:

1. Create the app-owned loop and start its daemon thread (see "One event loop").
2. **Enter the embodiment**: `run_coroutine_threadsafe(embodiment.enter(), loop).result()`. **Any** exception degrades to *no embodiment* — `missing` gets `<display_name>: <exc>`, and the embodiment is `None` from here on (typed text still drives the agent; nothing that needs a body is built) — its `display_name` is kept on the `AppSystem` all the same, for the page. After a successful enter, `embodiment.missing` (the per-component degrade lines gathered inside — the voice's key unset, a dependency absent, the emotion library unreadable) is appended to `missing`.
3. **Persona.** Resolve the persona **in the app** with WICA's public `resolve_system_prompt(cfg.wica.agent)` (an unreadable `system_prompt_file` raises `ConfigError` here — an `Agent: …` degrade), then append `embodiment.prompt_addendum` (when non-empty) and, when vision is enabled (and there is a body), `hand_signs_prompt_section()` from [gesture_input.py](../src/wica_interaction/gesture_input.py); derive the config WICA gets: `replace(cfg.wica, agent=replace(cfg.wica.agent, system_prompt=<persona + sections>, system_prompt_file=None))`. WICA's `resolve_system_prompt` prefers the inline prompt, so `Wica.init` never reads the file; the seeded text lands in the cached prefix.
4. `Wica.init(<the derived config>, loop=loop)`, then `wica.world.register("speech_input", str, serialize_fn=…, triggers_llm_call=True)`. `MissingEnvError`, any other `ConfigError`, and `ImportError` (a missing provider extra) each degrade to an `Agent: …` line. No sink, no output Command, no Events subscribed here.
5. Define the input funnel (above) as closures over the now-final agent.
6. **Speech in**, only with an embodiment: `asr_engine_factory(cfg.asr_engine, embodiment.mic)` — by default `ASREngine(cfg, audio_source=mic)` — then `AsrBridge(engine, on_final_segment=_deliver_speech, mic_error=lambda: embodiment.mic_error, trigger_words=cfg.asr_engine.segmentation.trigger_words)` (the words `start_dictation`'s reply names — [asr-bridge.md](asr-bridge.md)). The seam is a **factory**, not a prebuilt engine, so a test's fake is built exactly where the real one is — never without a body — and records the source it was handed: the hand-off that decides whether the robot listens through its own echo-cancelled mic is asserted, not assumed. A `ValueError` (the ASR key unset, a bad module) or an `ImportError` (the provider's extra not installed) degrades to `ASR: …`. Built **after** WICA so the callback resolves against a live agent with no placeholder.
7. **Vision in**, only with `vision.enabled`, an embodiment and an agent: for each `n` in `1 … max_hands`, register `hand_gesture_<n>` (`serialize_fn=…`, `triggers_llm_call=True`, `trigger_condition_fn=sign_shown`), then `GestureInput(embodiment.camera(fps=cfg.vision.fps), on_gesture=…, threshold=…, fps=…, max_hands=…, hold_s=…, device=…, models=gesture_models)`, whose callback writes `hand_gesture_<hand>` guarded on a running agent. Construction loads no model and opens no camera; a failure is still caught into `Vision: …`. (The persona was seeded in step 3, before this could fail: a construction failure — a programming or config error, since nothing is loaded here — leaves the "Hand signs" section in the prompt with the `Vision: …` banner line as the signal, the same as a model that fails to load after start.)

The returned `AppSystem` is a frozen snapshot: `loop`, `display_name`, `embodiment` (or `None`), `wica`, `bridge`, `gesture`, `missing`, and `submit_speech`. Any component that failed to build is `None` with its reason in `missing`.

**2. UI — `build_ui(system)` → `AppUi(blocks, transcript)`.** The UI **owns its presenters** — `TranscriptLog(system.wica, display_entry)` and `ReactionLog(system.wica, display_entry)`, each subscribing in its constructor, hence built after the system — lays out the page (title from `system.display_name`, the three contrib panels, the ASR panel with the Vision line, the typed-text hatch, the banner, the debug toggle) and returns the `gr.Blocks` plus the transcript on a small handle. Input widgets only touch `system.submit_speech` / the bridge; they never wire anything.

**3. Wire — `wire(system, transcript)`.** Gradio-free (it takes the presenter, so the tests call it with a `TranscriptLog` they build themselves). On the agent: `wica.set_output_sink(transcript.output_sink)`; `wica.set_output_command(embodiment.voice)` when the embodiment exists and its `voice` is not `None` (setting it recomposes the system prompt to name the Command, hence before `start()`); `register_command` for `start_dictation` / `stop_dictation` (when the bridge exists) and for each of `embodiment.commands()`. A no-op without an agent.

**4. Serve — `_serve(system)`: UI, wire, start, launch, close.** It runs steps 2 and 3, then `_start` and `blocks.launch()`, all four **inside** the one `try` whose `finally` runs `_shutdown`: `build_system` has already entered the embodiment (a robot session, a daemon the bridge spawned), so a raise *anywhere* after the build — the page failing to build, `wire` rejecting a Command name, `wica.start()` / `asr_bridge.start()` — must still exit it and stop the loop. `_start`: `wica.start()`, then `asr_bridge.start()` scheduled onto the loop and awaited (only with an agent — there is nothing else to consume speech), then `gesture.start()` (synchronous — the vision pipeline is threads; the camera source hops to the loop itself if it needs to); then `blocks.launch()`. `_shutdown` runs on the Gradio thread once `launch()` returns (never from a sink, Command or Event subscriber — those run on the loop): `wica.close()`, then `gesture.stop()` (a synchronous join, **before** the embodiment exits — the camera source may poll it on the loop), then — only if `asr_bridge.status()["running"]` — `asr_bridge.stop()` on the loop (so the mic pump is cancelled while its stream is still valid), then `embodiment.exit()` on the loop, then `loop.stop`. Each step runs **in isolation**, each loop hop bounded by a 5 s timeout: a step that raises or hangs is logged and the rest still run, so one failure cannot strand a media session or the loop thread.

The ordering rules this obeys, all from the contrib's contract: presenters after `Wica.init`; sink, output Command and Commands wired **before `start()`**; `world.update()` works only while running, so start before `launch()`; one `display_entry` hook for both presenters.

**One event loop — the app owns it; WICA and the embodiment adopt it.** A single asyncio loop runs the Agent, the ASR engine, and the embodiment's async verbs (`say`, the robot api, the mic pump). This is not optional: the `ASREngine` binds `asyncio.get_running_loop()` when it starts, and `say` and the action Commands run there as Commands. **`Wica` exposes no public accessor for the loop it creates**, so the app *owns* it — `asyncio.new_event_loop()` run in a daemon thread — and injects it as `Wica.init(…, loop=loop)`; WICA adopts it and spawns no loop thread of its own. Coroutines are scheduled onto it with `run_coroutine_threadsafe` from the main/Gradio thread: `embodiment.enter()` and `asr_bridge.start()` at startup, `stop_dictation()` from the Stop button, `asr_bridge.stop()` / `embodiment.exit()` at teardown. The ASR callback runs on that loop and calls `world.update`, thread-safe and with no cross-loop hazard.

## Degraded start

Like the WICA demo, the app **opens even when something could not be built** and says what to fix, rather than crashing. Every failure lands as one line in `AppSystem.missing`, shown in the banner; the surviving components still work (typed text reaches the agent whenever the agent built). Nothing contacts a model, ASR or TTS provider, or a robot, without its prerequisite in place.

| Failure | Raised as | Caught in | Effect |
|---|---|---|---|
| Config file missing / invalid / unknown section / bad block | `ValueError` from the embodiment's `load_config` ([configuration.md](configuration.md)) | `run_app` | A do-nothing system carrying only the message; the UI opens with the banner and no agent. |
| The body unavailable (the robot cannot be connected) | any exception from `embodiment.enter()` | `build_system` | `embodiment = None`, banner `<display_name>: …`; no voice, no action Commands, no ASR bridge, no gesture input. |
| A component of the body (voice key unset, provider extra missing, any other voice build failure, audio-processing dependency absent, emotions unreadable) | recorded in `embodiment.missing` by `enter()` | merged by `build_system` | That member degrades (`say` stays wired and reports the TTS error, `prompt_addendum` empty, `mic` raw); the rest of the body is up. |
| Agent key env var unset | WICA `MissingEnvError` at `Wica.init` | `build_system` | `wica = None`; banner names the env var. The presenters are built over `None` (empty, nothing subscribed); the World panel's place shows a note; `wire` is a no-op; ASR capture is not started. |
| Agent prompt file unreadable, or any other WICA config error | WICA `ConfigError` (from the app's own `resolve_system_prompt` call, or `Wica.init`) | `build_system` | `wica = None`; banner carries WICA's message. |
| Configured agent provider's extra not installed | `ImportError` at `Wica.init` | `build_system` | `wica = None`; banner says what to install. |
| ASR key env var unset / module misconfigured / provider extra missing | `ValueError` / `ImportError` at `ASREngine(...)` | `build_system` | `bridge = None`; the ASR panel shows "stopped"; typed text remains the way in. |
| Vision misconfigured at build | any exception constructing `GestureInput` | `build_system` | `gesture = None`, banner `Vision: …`. A model that fails to load *after* start shows on the Vision line's `⚠️ error:` tail instead. |

## Testing

Fast, deterministic, no browser/mic/camera/network/robot ([testing.md](testing.md)). The lifecycle tests in `tests/test_system.py` stand the real app up through the same Gradio-free steps `run_app` uses — `build_system(config, FakeEmbodiment(...), asr_engine_factory=asr_factory(FakeASREngine(...)))`, then `wire(system, transcript)` with a `TranscriptLog` (and, where a test reads prompts, a `ReactionLog`) the test builds itself, then `_start` — against WICA's scripted `fake` provider, and assert on the embodiment's recorded side effects (`say` reaching its voice, a registered command routing back to it), on the presenters' `snapshot()`s (the transcript's input items, the 💭 sink item proving the sink is wired, a step's prompt carrying the addendum and the hand-signs section), and on the degrade and teardown paths ([embodiment.md](embodiment.md), "Testing") — `_serve` included, with `build_ui` stubbed: a raise while building the page, while wiring, or while starting each still exits the body and stops the loop. `build_ui` itself is not driven (no browser); `tests/test_app_ui.py` covers `display_entry`, the ASR status line, the Vision line and the page's name (a live body, a body that failed to enter, no config) over a system with a fake embodiment. The three contrib panels are tested in WICA's own suite.

## Out of scope (v1)

- **Barge-in:** *talking over* the agent is not handled — WICA drops a trigger only while a **model call** is in flight; `say` runs as a detached Command, so speech that closes while `say` is *playing* starts a new step with the running `say` visible in the World, and nothing cancels the first. (The mic hearing the app's *own* voice *is* handled, per embodiment.)
- **Streamed speech:** `say` plays a complete utterance; no word-by-word UI reveal.
- **Persistence:** the World starts empty each run.

## Open questions

1. **Pausing the hidden World panel.** If three always-on timers ever prove costly, the fix is the contrib's deferred injectable `timer=`, not a local reimplementation. Deferred until measured.
2. **Embodiment state in the UI.** A panel owned by the embodiment ([embodiment.md](embodiment.md), open question 1).
