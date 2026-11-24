# Latency

How long the person waited for the agent to react, and where that time went, reaction by reaction. The page shows it live; nothing is saved. To compare models or configurations, use the [benchmark](benchmarking.md) instead. The design is [specs/latency-view.md](../../specs/latency-view.md).

## What is measured

Each reaction is measured from the moment the person finished giving their input to the moment the reaction could first be perceived.

**The start** depends on what woke the agent:

| Trigger | Measured from |
|---|---|
| speech | the end of the last word the voice-activity detector heard. Without a VAD (`asr.vad` absent), or once it has failed open, the start is unknown: the measure starts when the final transcript was produced, and the reaction is flagged `origin unknown` |
| typed text | the moment it was sent |
| hand sign | the first camera frame that showed the sign, before the hold ([hand-signs.md](hand-signs.md)) |
| user event (robot) | for an arrival, the first frame that saw the person, before the robot confirmed them; otherwise the frame that was reported |
| follow-up | the end of the command, or the note, that woke the agent again |

What comes before that start is not measured: the camera's own delay, the microphone's capture delay, the moment a sign was really shown.

**The end** is the first effect of the reaction:

| Body | What the agent does | Ends at |
|---|---|---|
| computer | speaks | the first sound: the device started, plus the latency it reports. With echo cancellation on, the first audio queued on the app's open stream, plus that stream's reported latency |
| Reachy Mini | speaks | the first audio handed to the robot's speaker. The delay from there to the sound is not measured |
| Reachy Mini | plays an emotion | the robot beginning to move |
| either | any other command | the command starting |

A reaction that speaks and acts ends at whichever comes first. A reaction that does nothing has no total. A voice that never produced sound (a cancelled utterance) has no effect either: it was not heard.

## Reading the page

### At the top of the page

- **The glance line**: the last reaction's total, as one bar split into input (to the World write), agent (to the start of the command that took effect) and output (to its effect). Configured waits are lighter, and the input's and the agent's waits are given apart: the end of turn or a sign's hold is not the agent gathering its inputs. The median and 90th percentile of its kind of trigger sit beside it.
- **The timeline**: the last 30 seconds, one row per lane. *You* is the person speaking, *Hearing* the speech recognizer, *Seeing* the hand pipeline and the robot's users, *Agent* the model, *Voice* and *Actions* what the body did. A step still running grows, a command whose effect is not known yet included (shown *in progress*). A red mark on the *Agent* row is an input dropped because the agent was busy.

Each step is drawn as one of three kinds:

- **solid**: work, something computing. Changed by choosing another model or provider;
- **light**: a configured wait: the silence the voice detector waits for, the agent gathering inputs, the sign hold, a dictation's silence timer. Changed by a config edit;
- **hatched**: not measured, a share with no stamp.

The darkest bars are what was heard or moving.

### What a spoken turn's steps mean

In the waterfall each row reads `<lane> · <step> · <detail>`:

| Row | What happens |
|---|---|
| You · you speaking | from your first word to your last, as the voice detector heard them |
| Hearing · transcribing as you speak | a streaming recognizer (Deepgram) transcribes while you speak, from its first partial transcript to its final one. Drawn thinner: it overlaps the other rows and counts in no share |
| Hearing · waiting for end of turn | the wait after your last word until something decided you had finished: `silence 300 ms` (the voice detector, `min_silence_duration_ms`), or `the recognizer's end of turn` (Deepgram Flux decides on its own, often sooner) |
| Hearing · final transcript | producing the final transcript once the end of turn is decided. About zero when the recognizer decided, since its decision and its final arrive together; the whole transcription with a batch recognizer, which waits for the end of turn to start |
| Hearing · handed to the agent | the transcript written to the agent's World |
| Agent · gathering inputs | the agent waits 200 ms for anything arriving with it, a sign shown as you finish a sentence |
| Agent · building the prompt, model: first token, model: first words to say | the agent's prompt, then the model: its first token, then the first words it wrote inside `<say>` |
| Voice · waiting for the first sentence, synthesis, opening the audio device, device latency | the voice: waiting until the model has written a first sentence, turning it into audio, starting the sound device, and the latency the device reports |
| Voice · heard | from the first sound to the end of the utterance |

### What a hand sign's steps mean

| Row | What happens |
|---|---|
| Seeing · waiting for hand detection | the frame waits for the hand stage to pick it up: the stage runs at `vision.fps` (10 by default) and samples the newest frame on each turn, so a frame waits up to one period, or longer while the stage is still busy with the previous frame. A configured wait: a higher `vision.fps` shortens it, at a CPU cost |
| Seeing · hand detection | MediaPipe finding the hand and cropping it |
| Seeing · waiting for classification, classification | the same for the gesture classifier, on that hand's crop |
| Seeing · read by the app | the app's watcher reading the classifier's result (it looks every 10 ms) |
| Seeing · holding the sign | the sign must stay unbroken for `vision.hold_s` (0.3 s) before it counts, so a flicker never wakes the agent |

A step named after a library's event is shown in plain words; the hover text keeps its raw name (`synthesis [first_audio]`).

### On the Latency tab

- **The statistics**: per kind of trigger, the count, the median and the 90th percentile of the total, and the median of each phase. The first reaction of the run is cold and left out, and so are follow-ups, reactions whose start is unknown and reactions that failed. **Reset statistics** starts them over.
- **The table**: one row per reaction, top to bottom in the order they happened, scrolling under its header row, a follow-up right under the reaction it follows, indented. Its columns, in seconds: *input: wait* (the end of turn, a sign's hold, the stages' pacing), *input: work* (the final transcript, detection and classification), *agent: wait* (gathering inputs), *agent: work* (the prompt and the model), *output* (the voice or the action up to its effect), then the total and the flags. While the newest row is selected and the table is scrolled to the bottom, it follows each new reaction; select an older row and it stays put while you read. **Latest ↓** scrolls down and selects the newest again.
- **The waterfall** of the row you select, the newest by default: every step on one time axis, one row each, the start dashed and the effect in red. Under it, two signal rows on the same axis: *inputs*, your speech and every sign or user the app delivered, and *outputs*, every effect (the voice heard, the robot moving), whichever reaction they belong to. A bar can be checked by eye against them, and a reaction overlapping another shows. What woke nothing is not on them yet.

### The flags

| Flag | Meaning |
|---|---|
| `2 attempts` | the model was called again after a rejected response |
| `garbled` | a response was rejected for garbled text |
| `guards 80 ms` | the response guards took that long |
| `model error`, `model timeout`, `rejected`, `cancelled` | how the reaction ended |
| `uncached` | the model read nothing from its prompt cache |
| `first (cold)` | the run's first reaction that called the model |
| `input dropped` | an input arrived while this reaction ran and was dropped |
| `queued` | the voice waited behind another utterance |
| `origin unknown` | the start could not be stamped |
| `noop` | the agent chose not to react |

### The Vision panel

Next to the frame rates, the Vision panel shows how old the frame behind the newest classifier result is, and how long each stage took on it. It is useful with no reaction at all: it shows a pipeline falling behind.

## Exporting the traces

The page collects everything in the app's own process. To also send the traces elsewhere, set `WICA_INTERACTION_TRACES`:

| Value | Effect |
|---|---|
| `console` | prints each span as it ends |
| `otlp` | sends them over OTLP/HTTP, configured with the standard `OTEL_EXPORTER_OTLP_*` variables, to a collector or a tracing backend such as Jaeger. Needs the `otlp` extra ([install.md](../reference/install.md#extras)) |

An unknown value, or `otlp` without its extra, is a banner line, and the page's view still works. One spoken turn is one trace, from the recognizer's segment down to the voice.
