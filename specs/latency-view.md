---
code:
  - src/wica_interaction/latency.py
  - src/wica_interaction/latency_view.py
  - src/wica_interaction/app_ui.py
  - src/wica_interaction/system.py
  - src/wica_interaction/embodiment.py
  - src/wica_interaction/asr_bridge.py
  - src/wica_interaction/gesture_input.py
  - src/wica_interaction/computer/embodiment.py
  - src/wica_interaction/computer/audio_processing.py
  - src/wica_interaction/reachy/embodiment.py
  - src/wica_interaction/reachy/users.py
tests:
  - tests/test_latency.py
  - tests/test_latency_view.py
  - tests/test_app_ui.py
  - tests/test_system.py
  - tests/test_asr_bridge.py
  - tests/test_gesture_input.py
  - tests/computer/test_embodiment.py
  - tests/reachy/test_embodiment.py
  - tests/reachy/test_users.py
---

# The latency view

**Status:** Implemented

## Purpose

A person who speaks or shows a sign to the agent waits for it to react. This spec defines how the app measures that wait, for every reaction, and how the page shows it. On a live run the page answers three questions:

1. **How fast are we?** Over the session, per kind of trigger.
2. **Where did the time go on this reaction?** Step by step.
3. **Why was that one slow?** A retry, a cold start, a configured wait, a queue.

The view is for watching a run live. Comparing configurations is the benchmark's job, so nothing here is saved to disk.

Before this spec only the agent's own share was measured (WICA's `ReactionTrace`, shown as text in the Reaction debug panel). The recognizer, the voice, the robot and the hand pipeline now publish their own stamps; this app collects them, joins them per reaction, and draws them.

## Core concepts / Decided

### The measure

**One reaction, one measure.** A reaction is one reasoning step of the agent (a WICA `ReactionTrace`). Its measure runs from an **origin**, the moment the person finished giving the input, to an **effect start**, the moment the reaction can first be perceived. The **total** is the time between the two.

**The origin depends on the trigger.** The trigger of a reaction is its parent trigger, as WICA defines it for a coalesced batch (`parent_trigger`).

| Trigger kind | How it is recognised | Origin | When the origin is unknown |
|---|---|---|---|
| `speech` | an `asr_engine.segment` span among the reaction span's ancestors | the end of the last speech frame: the segment span's start, when its `asr_engine.speech_end_source` is `vad` | no VAD, a gate that failed open, no speech detected: the span starts at `finalized`, the measure starts there, flagged `origin unknown` |
| `hand sign` | a `wica_interaction.perception` span among the ancestors, trigger `hand sign` | the first frame of the unbroken run that showed the sign | a `hand_gesture_<n>` write with no perception span: the measure starts at the write, flagged |
| `user event` (robot) | a perception span, trigger `user event` | for an arrival, the first sighting of the arriving user's track; otherwise the report's frame | a `user_event` or `personal_space` write with no perception span: from the write, flagged |
| `follow-up` | the parent trigger is a Command's completion (`agent:command:<call_id>`) or the `agent:next` note | the trigger's World write | — |
| `typed` | a `speech_input` write with no segment span above it | the World write | — |

Any other entry gives a trigger kind named after its key, measured from its write and flagged. What happens before the origin is not measured: the moment a sign was really shown before a frame caught it, the camera's own delay, the microphone's capture delay.

A follow-up's **parent** is the reaction that ran the completed Command (`CommandTrace.reaction_id`), or for a `<next>` note the reaction just before it: the note is for the one step that follows.

**The effect start is declared per Command, not hardcoded for speech.**

- By default a Command's effect starts when the Command starts.
- An embodiment can declare a later, truer moment: an event on a span nested under the Command's `wica.agent.command` span, with an `EffectStart` (below).
- The reaction's effect start is the **earliest** among its Commands. A reaction that speaks and plays an emotion ends at whichever is perceived first.
- A Command whose declared event is absent, the Command having ended (a cancelled utterance, a `say` with nothing sayable), has no effect start. It does not fall back to its own start: a voice that produced no sound was not heard.
- A reaction with no Command (a `<noop/>`, private text only, an error) has no effect start and no total.

Adding a measure to another Command later is one event in the library that runs it and one declaration in the embodiment. The collector and the page do not change.

### Kinds of time

Every step is one of three kinds, because each calls for a different action, plus what was perceived:

- **`work`**: something computing. Changed by choosing another model or provider.
- **`wait`**: a delay the config sets: the VAD hangover, the coalescing window, the sign hold, a stage's pacing at `vision.fps`, a dictation's silence timer, the bridge's confirmation of an arrival. Changed by a config edit.
- **`not_measured`**: a share that exists and has no stamp, drawn hatched and never as nothing: the time before a final segment whose speech end is unknown, a Command that ended without its declared effect.
- **`effect`**: from a Command's effect start to its end, the time it was heard or moving. Not part of the total.

A step can also be **context**: drawn under the steps it overlaps, thinner and lighter, and counted in no share (the recognizer transcribing as the person speaks).

### The steps of a reaction

A reaction has a head (what led to the World write), a middle (the agent) and a tail (what reached the outside). Each step has a name, a lane, a start, an end (`None` while it runs) and a kind.

**Head, by trigger kind.** Every name here says what happens, and a step's *detail* says what decided it or what it leaves out.

| Trigger kind | Lane | Steps, in order |
|---|---|---|
| `speech` | `You`, `Hearing` | **you speaking** (`You`): the voice detector's start of speech (`asr_engine.speech_started_unix_ns`) → its end, when both are known. **transcribing as you speak** (context, below): the recognizer's first partial transcript → `finalized`. **waiting for end of turn** (*wait*): end of speech → `end_decided`, when both are known; its detail names who decided: `silence <min_silence_duration_ms> ms` (`asr_engine.end_decided_by` is `vad_gate`) or `the recognizer's end of turn` (`module`). **final transcript** (*work*): → `finalized`; its detail: `arrives with the recognizer's end of turn` when the recognizer decided (its final and its decision are one message, so this step is about zero and the recognizer's own work sits in the wait before it), `the whole transcription: no partial came before` when no partial preceded it (a batch recognizer), `what was left to transcribe after the end of turn` otherwise. When the span carries `asr_engine.segmentation.end_of_speech_timeout_s`, the last part of the final transcript, as long as that timer and no longer than the step, is a separate **dictation silence timer** step (*wait*). **handed to the agent** (*work*): `finalized` → the World write. With the end of speech unknown, a zero-length **before the final transcript** step (*not measured*) comes first |
| `hand sign` | `Seeing` | the perception span's steps (below), then **handed to the agent** (*work*): → the World write |
| `user event` | `Seeing` | **confirming the person** (*wait*, arrivals only): first sighting → the report's frame; then **handed to the agent** |
| `typed`, `follow-up` | — | none |

**Transcribing as you speak** is a context step: a streaming recognizer starts as the voice detector opens and sends partial transcripts while the person speaks, so recognition overlaps the speech. The ASR bridge reports when each stretch of speech's first partial was emitted (`on_speech`, [asr-bridge.md](asr-bridge.md)), and the collector joins it to the segment by its exact start of speech, the integer nanoseconds `asr-engine` puts in `asr_engine.speech_started_unix_ns`. It is drawn thinner and lighter, under the steps it overlaps, and counts in no share. A recognizer that sends no partial before the end of turn has none.

**Middle, from the `ReactionTrace`.** Lane `Agent`, all *work* but the gathering of inputs.

| Step | From → to |
|---|---|
| reaching the agent | the World write → the trigger reaching the loop |
| gathering inputs (*wait*) | WICA's coalescing window opening → closing |
| building the prompt | the window closed → the prompt ready |
| model: first token | the model started → the first streamed text or tool call |
| model: first words to say | the first token → the first words handed to the output Command, when the reaction opened one (WICA checks the first 80 characters for garbled text before) |
| model: whole response | the first token → the model ended |
| model | started → ended, when no content was streamed |

A reaction with several attempts has its *model* steps from the first attempt's start; the rejected attempts and the guards' time are flags, not steps.

**Tail, per Command.** Lane `Voice` for the output Command, `Actions` for any other.

- One step per event on the spans nested under the Command's span, in time order, each named after its event (the first occurrence of a name wins), from the previous moment (the Command's start, then each event) to the event. Only the events up to the effect start count. *work*.
- **device latency** (*work*), from the last event to the effect start, when a delay is added to the declared event.
- **heard** (the voice), **moving** (a declared action), **running** (an action with no declaration), kind *effect*, from the effect start to the Command's end.
- A Command with no declaration has its effect at its start, so its tail is the one effect step. One still running whose declared event has not come yet has an **in progress** step (*work*) from its last event, growing to now: the bridge places an emotion's motion on its span only when the call ends. One that ended without its declared event has a **never perceived** step (*not measured*) over its run.

A step named after a library's event is named after where it **ends**: `first_audio` closes the time spent producing the first audio. The page therefore says each event in plain words, by what the step ending there did (`latency_view.EVENT_NAMES`), and keeps the raw name in the hover text; an event not listed shows under its own name, so nothing is hardcoded for a Command:

| Event | Library | Said as |
|---|---|---|
| `admitted` | tts-engine | waiting for the voice to be free |
| `first_text` | tts-engine, the bridge | waiting for the first words |
| `first_segment` | tts-engine | waiting for the first sentence |
| `first_audio` | tts-engine | synthesis |
| `last_audio` | tts-engine | synthesis, to the end |
| `playback_started` | tts-engine | opening the audio device |
| `first_push` | the bridge | sending to the robot |
| `queued` | the bridge | preparing the move |
| `motion_started` | the bridge | waiting for the motors |
| `trajectory_started` | the bridge | blending into the move |
| `sound_started` | the bridge | starting the sound |

### `EffectStart`: what an embodiment declares

```python
@dataclass(frozen=True)
class EffectStart:
    # A span nested under the Command's span, and the event on it that marks the effect start.
    span: str
    event: str
    # An attribute of that event, in seconds, added to its time.
    latency_attribute: str | None = None
    # A delay the body knows, in seconds, added to its time.
    latency_s: float | None = None
    # What this moment leaves out or adds, shown under the waterfall's title.
    note: str = ""
    # The moment in a few words, ending the waterfall's title.
    label: str = ""
```

The `Embodiment` protocol has one member for it, read once, at wiring, with the output Command's name ([embodiment.md](embodiment.md)):

```python
def effects(self) -> Mapping[str, EffectStart]: ...
```

| Body | Command | `span` / `event` | Added delay | `label` |
|---|---|---|---|---|
| Computer, the engine's own player | `say` | `tts_engine.utterance` / `playback_started` | the event's `tts_engine.output_latency_seconds` | the first sound |
| Computer, echo cancellation on | `say` | `tts_engine.utterance` / `first_audio` | the app's stream's reported output latency (`AudioProcessingCoordinator.output_latency`), when it gives one | the first sound |
| Reachy Mini | `say` | `reachy_mini_bridge.say` / `first_push` | none: the delay from the push to the sound is not measured, which its `note` says | the first audio sent to the robot |
| Reachy Mini | `play_emotion` | `reachy_mini_bridge.play_emotion` / `motion_started` | none | the robot moving |

The names are the published contract of `tts-engine` (`specs/utterance-timing.md` there) and `reachy-mini-bridge` (`specs/core/tracing.md` there), and the embodiments use the constants those packages export. Echo cancellation's declaration assumes the app's always-open stream adds no opening delay.

### Where the stamps come from

Two sources, joined per reaction.

**1. WICA's events**, subscribed before the agent starts: `on_agent_reaction_ended` (the middle), `on_agent_command_ended` (each Command's start and end), `on_agent_trigger` (the triggering entry, labelled for the table), `on_agent_trigger_dropped` (a flag and a timeline mark).

**2. OpenTelemetry spans.** The chain for one reaction is:

```
asr_engine.segment               (the recognizer: a root span)
or wica_interaction.perception   (this app: a root span)
└─ wica.world.update
   └─ wica.agent.reaction        (attribute wica.reaction_id)
      ├─ wica.agent.model
      └─ wica.agent.command      (attributes wica.call_id, wica.command.name)
         └─ tts_engine.utterance, reachy_mini_bridge.<verb>, …
```

- **The join is by span parentage**, never by time or order. A reaction's head is the nearest `asr_engine.segment` or `wica_interaction.perception` ancestor of its `wica.agent.reaction` span, and none past an earlier `wica.agent.reaction` or `wica.agent.command`: a Command's completion is written from inside that Command's span, so the reaction it wakes descends from the one before, whose input is not its own. A Command's steps come from the descendants of its `wica.agent.command` span, and the Commands of a reaction are its `command_call_ids` plus any command span started under its reaction span.
- The collector keeps every span from its start, so a running span's events are read while it runs, and the span it becomes at its end replaces it.

### The perception span: this app's own inputs

The recognizer opens its own span. For what this app perceives itself, it opens one with `perception(trigger, origin_ns, steps)`:

| Kind | Name | Meaning |
|---|---|---|
| Tracer | `wica_interaction` | every span this app opens |
| Span | `wica_interaction.perception` | one delivered perception, from its origin to the end of its World write. Always a root span, whatever is current |
| Attribute | `wica_interaction.trigger` | the trigger kind: `hand sign`, `user event` |
| Span | `wica_interaction.step` | one step of the head, a child of the perception span, with explicit start and end times |
| Attribute | `wica_interaction.step.name`, `wica_interaction.step.kind` | the step's name; its kind |

- The producer opens it when it delivers, **backdated to the origin**, current during the World write, so the reaction descends from it.
- **Hand signs.** `HoldFilter` keeps with a candidate the `FrameStamp` of its first observation: the frame's `ts`, the result's `StageTiming`s, and when the watcher read it ([gesture-input.md](gesture-input.md)). When the hold elapses on a sign, `GestureInput` opens the span with, per stage, **waiting for \<stage\>** (*wait*, the stage's pacing) and the stage's work (`hand detection`, `classification`; another stage under its own name), then **read by the app** (*work*, the watcher reading the result) and **holding the sign** (*wait*, to the delivery). A stage's start a few microseconds before the previous stamp is the clock's resolution, floored at it. A release (`None`) wakes nobody and opens no span.
- **The robot's users.** `UsersInput` opens it around each `user_event` write ([reachy-embodiment.md](reachy-embodiment.md)).
- **Continuous work gets no span.** Frames, detections and stage results are stamps on values, read when a perception is delivered.

**Clocks.** Every stamp in a record is wall-clock UTC, the clock of WICA's records and of OpenTelemetry. `vision-modules` and the bridge stamp on `time.perf_counter()`; `wall_clock_ns` converts such a stamp by reading `time.perf_counter()` and the wall clock together, and the users input uses the bridge's own `tracing.wall_clock`. The hold filter's logic stays on `time.monotonic()`: it answers "has enough time passed?", not "when did this happen?".

### Installing the SDK

- `opentelemetry-sdk` is a base dependency. `install_tracing(collector)` installs a `TracerProvider` once per process when none is, before `Wica.init`, and adds one forwarding span processor whose target is the newest system's collector: a second system in the same process (the tests) takes the forwarding over, and the provider is never replaced. Another implementation already installed is a banner line, and the view stays empty.
- `WICA_INTERACTION_TRACES` adds an exporter on top, once per process: `console` prints each span as it ends, `otlp` sends them over OTLP/HTTP with the standard `OTEL_EXPORTER_OTLP_*` variables and needs the `otlp` extra. An unknown value, or `otlp` without the extra, is a `Latency: …` banner line and the app runs without the exporter.
- Collecting never breaks the app: a failure while collecting a span or building a record is logged and that item dropped.

### The record

The view is a pure function of records, one per reaction:

```python
StepKind = Literal["work", "wait", "not_measured", "effect"]
Lane = Literal["You", "Hearing", "Seeing", "Agent", "Voice", "Actions"]


@dataclass(frozen=True)
class Step:
    name: str
    lane: Lane
    started_at: datetime
    ended_at: datetime | None  # None while it runs
    kind: StepKind
    command: str | None = None  # the Command a tail step belongs to
    # What this step was, said plainly: who decided an end of turn, what a step leaves out.
    detail: str = ""
    # Drawn as context, overlapping the others, and never counted in a share.
    context: bool = False


@dataclass(frozen=True)
class ReactionRecord:
    reaction_id: int
    trigger: str
    label: str  # what was heard or seen, as the transcript labels it
    parent_reaction_id: int | None  # for a follow-up
    origin_at: datetime
    origin_measured: bool
    written_at: datetime  # the parent trigger's World write
    steps: tuple[Step, ...]
    effect_at: datetime | None
    effect_command: str | None
    effect_note: str
    effect_command_started_at: datetime | None
    flags: tuple[str, ...]
    outcome: ReactionOutcome
    complete: bool  # every Command of the reaction has ended
    counted: bool = False  # it enters the statistics
    origin_label: str = ""  # where the measure starts, in a few words
    effect_label: str = ""  # where it ends: the effect's label, or "<command> starting"

    @property
    def total(self) -> float | None: ...  # effect_at - origin_at

    @property
    def shares(self) -> Shares | None: ...
```

- **The shares** split a total into its phases, at the World write and at the effect Command's start: **input** (origin → write), **agent** (write → that Command's start), **output** (→ the effect). The input and the agent are each split again into their **wait** (the time of the *wait* steps inside them, context steps aside) and their **work** (the rest), so the end of turn or a sign's hold is told apart from the agent gathering its inputs. The five (`input_wait`, `input_work`, `agent_wait`, `agent_work`, `output`) sum to the total. Columns per stage (the voice detector, the recognizer, detection) were considered and left out: each trigger kind has its own stages, so most cells would be empty; the waterfall gives that detail.
- A record exists once its reaction ends and is **completed in place** as its Commands progress: the effect start appears while the voice still speaks. Once it is complete and nothing under its reaction still runs, it is kept as it is.
- A reaction that has not ended shows on the timeline only: its head, a **the agent so far** step from its span's start to now, and the tails of the Commands under it.
- The collector keeps the last 200 records and a bounded window of spans.

### Flags

| Flag | Condition |
|---|---|
| `N attempts` | `attempts > 1` |
| `garbled` | `garbled_attempts > 0` |
| `guards N ms` | `guard_duration` at or above 50 ms |
| `model error`, `model timeout`, `rejected`, `cancelled` | the reaction's outcome |
| `uncached` | the usage reports 0 cached tokens read (unknown is no flag) |
| `first (cold)` | the run's first reaction that called the model |
| `input dropped` | a trigger was dropped between the reaction's window closing and its end |
| `queued` | a `tts_engine.utterance` under one of its Commands was admitted 50 ms or more after it started |
| `origin unknown` | `origin_measured` is false |
| `noop` | the reaction chose not to react |

### Statistics

- **Per trigger kind, never pooled**: the origins differ, and a deliberate hold is not comparable to a recognizer finalising.
- For each kind: the count, the **median** and the **90th percentile** (nearest rank) of the total, and the median of each share.
- **Counted**: a complete record with a total, a measured origin and outcome `ok`. **Listed but not counted**: `first (cold)`, follow-ups, an unknown origin, a reaction with no effect.
- Over the whole session, or since the page's **Reset statistics** button; a record counted before a reset stays out. The statistics keep their own values, so they are not limited to the records kept.

### What the page shows

Four parts, drawn by pure functions from a `LatencySnapshot` to markup (`latency_view.py`). Bars are positioned `div`s with inline styles. Everything in a record that came from a person or the model is HTML-escaped.

First on the page, full width above the Conversation and Camera row, under a **Latency** heading:

1. **The glance line.** For the newest record with a total: the total, one bar split into input, agent and output, the waits lighter, the three phases in seconds with the input's and the agent's waits, and the median and 90th percentile of its trigger kind.
2. **The live timeline.** The last 30 seconds, one row per lane, named after who does what: `You` (the person speaking, from the recognizer's speech stamps, the first interim opening a stretch and the final ending it), `Hearing` (the recognizer), `Seeing` (the hand pipeline, the robot's users), `Agent`, `Voice`, `Actions`. A step still running is drawn up to now and grows. A dropped input is a red mark on the `Agent` row. A legend names the kinds.

On the page's **Latency** tab, next to the **Interaction** tab that holds everything else:

3. **The summary and the table.** The statistics as a table per trigger kind, a **Reset statistics** button, then one row per record, top to bottom in the order they happened: number, trigger, label, `input: wait`, `input: work`, `agent: wait`, `agent: work`, `output`, the total, the flags. A follow-up sits right under its parent, indented with `↳`, a follow-up of a follow-up further. The table is the app's own markup (`latency_view.table`) in a `gr.HTML` with browser code of its own, not a `gr.Dataframe`: a Dataframe draws only the rows in view, measured from its size, so rows added while that size stays put did not show until the frame was resized. The table is redrawn whole on each change, scrolls inside a fixed height (360 px) under its pinned header row, and its browser code (`js_on_load`) keeps the reader's place across redraws. It **follows the newest row** while the newest is the one selected and the table is scrolled to the bottom; a click on a row selects it, through the server function `select_reaction`; the **Latest ↓** button scrolls down and selects the newest. With none selected, the newest record's waterfall is shown and followed.
4. **The waterfall** of the selected record, titled with both ends of its measure — `reaction 9 · hand sign · 1.77 s from the first frame showing the sign to the first audio sent to the robot (say)`, the origin's label by trigger kind (`your last word`, `the message sent`, `the first frame showing the sign`, `the first frame that saw the person`, `the end of <command>`, `the agent's note to go on`; `the final transcript` or `its delivery to the agent` when the origin is unknown) — with the effect's `note` and an unknown origin each on a line of their own under it: every step on one time axis, one row each, grouped by lane and labelled `<lane> · <plain name> · <detail>`, the origin dashed and the effect start in red with its note, then two thin signal rows on the same axis, whichever reaction each bar belongs to: **inputs** (the person speaking, every delivered perception) and **outputs** (every Command's effect: the voice heard, the robot moving, an action running), each bar in its lane's colour. The inputs row lacks what woke nothing (a sign held and released, a user the robot does not attend to): the app does not record those yet.

- **The kinds are told apart by pattern as well as colour**: work solid, a wait light, not measured hatched (drawn at a minimum width when it has no length), an effect darkest.
- **Refresh.** `LatencyPanel.tick`, on a timer of its own at 0.2 s, returns each part, or `gr.skip()` when it is what was last sent: an idle page sends nothing, and an idle timeline renders constant markup. The summary, the table and the waterfall are read only while the Latency tab is open.
- **With no agent** the glance line says so in one line and the other parts stay empty.

### The Vision panel's readout

Under the stage rates, the Vision panel shows, for the newest classifier result, how old its frame is and the milliseconds each stage took on it (`GestureInput.status()`'s `frame_age_s` and `stage_ms`). This is useful with no reaction at all: it shows a pipeline falling behind.

### Where the code lives

- **`latency.py`**: `EffectStart`, `Step`, `ReactionRecord`, `Shares`, `LatencySnapshot`; `wall_clock_ns` and `perception`; `install_tracing`; `LatencyCollector` (a span processor's target, subscribed to WICA's events, building records on `snapshot()`); `Statistics`. It imports WICA, OpenTelemetry and the recognizer's published names, and nothing else of this app.
- **`latency_view.py`**: the pure functions from snapshots to markup and table rows. No Gradio, no clock.
- **`app_ui.py`**: `LatencyPanel`, the components, the timer, the row selection and the reset button; the collector's labels from the page's display hook.
- **`system.py`**: the collector and `install_tracing` before `Wica.init`, `attach` after it; the embodiment's `effects()` handed over in `wire`; `AppSystem.latency`.
- **`asr_bridge.py`**: `on_speech`, the person's speech for the `You` row ([asr-bridge.md](asr-bridge.md)).
- **`gesture_input.py`**, **`reachy/users.py`**: the perception span when they deliver.
- **`computer/embodiment.py`**, **`reachy/embodiment.py`**: `effects()`; **`computer/audio_processing.py`**: the stream's output latency.

## Open questions

**Choices made with this spec, accepted at review (2026-11-23)**, kept as the record of what was chosen:

1. **`effects()` on the `Embodiment` protocol**, keyed by Command name, rather than a declaration on each Command object.
2. **The environment variable's name** (`WICA_INTERACTION_TRACES`) and `otlp` as an extra of its own.
3. **Echo cancellation on: the effect is `first_audio` plus the stream's reported latency**, with no stamp of the app's own.
4. **A release of a sign opens no span**, so it never shows on the `Seeing` row.
5. **The thresholds**: 50 ms for `queued` and for showing the guards, 200 records kept, a 30 s timeline.

Deferrals:

- **The robot's output latency.** `reachy-mini-bridge` does not measure the delay between a push and the sound. The robot's voice ends at `first_push` until it does; the declaration then gains a `latency_attribute`.
- **The capture delay.** The recognizer's speech stamps are late by the device buffer and the chunking, so a speech total is slightly short. `asr-engine` lists it as an open question.
- **The origin of a `personal_space` change** on the robot: it has no perception span yet, so a reaction it wakes is measured from its write and flagged.
- **A dispatch step** between a tool call's dispatch and its Command's start: WICA records no dispatch time apart from the start.
- **The same records in the end-to-end tier's report**, which records no latency today.
- **Grouping and indentation.** Statistics per trigger kind and follow-ups indented under their parent are first tries, to revisit after use.
- **A recording of a session** (the records, with audio and camera) to replay afterwards.
