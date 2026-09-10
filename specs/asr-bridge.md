---
code:
  - src/reachy_mini_interaction_wica/asr_bridge.py
tests:
  - tests/test_asr_bridge.py
---

# ASR bridge

**Status:** Updated

> **Implementation lag (why `Updated`).** The core segment→World rule and the dictation command below
> are already built and unchanged. What changed is the **mic source**: the design now feeds the
> `ASREngine` the robot's echo-cancelled stream (a `RobotMicSource` over `ReachyMiniApi.audio_input()`)
> instead of a local device. The current
> [asr_bridge.py](../src/reachy_mini_interaction_wica/asr_bridge.py) still relies on the engine opening
> a local mic. Closing that gap is part of the [bridge-integration plan](../plans/_index.md); this spec
> returns to `Implemented` when it lands.

## Purpose

The **speech-in** half of the app: it owns the live [`ASREngine`](../../asr-engine/specs/engine.md) —
fed the **robot's echo-cancelled microphone** rather than a local device — turns its stream of speech
*segments* into WICA World updates, and exposes the **dictation** control the agent can invoke. It is
the one place that knows how ASR segmentation maps onto "a thing the user said", so the rest of the
app (the WICA agent, the UI) never touches the ASR engine directly.

Two consumers read from it:

- **The WICA World** — a *final* segment becomes the `speech_input` the agent reasons over.
- **The Gradio UI** — the *live, growing* segment text and the current ASR status are displayed as
  they change (see [app.md](app.md), "ASR panel").

## The segment → world rule (the core decision)

The ASR engine emits a `SpeechSegment` on **every** change: interim updates while the segment grows
(`is_final=False`, `end_reason=None`) and one final emission when it closes (`is_final=True`). The
bridge treats the two completely differently:

| Segment | What the bridge does |
|---|---|
| **Interim** (`is_final=False`) | Updates its **live display text** to `segment.transcript`. Does **not** touch the World — the agent is not triggered. |
| **Final** (`is_final=True`) | Calls the injected **`on_final_segment(transcript)`** callback (the app wires this to `world.update("speech_input", transcript)`, which triggers one reasoning step) and then **clears the live display text** back to `""`. |

So partial speech is *visible but inert* — it streams into the ASR panel character by character — and
only a closed segment reaches the agent, exactly once, as one complete utterance. The World's
`speech_input` entry changes once per closed segment, never on interims, so a burst of interim updates
can't wake the agent mid-utterance.

**Every** final fires the callback — including one whose transcript is empty or whitespace (e.g. a
`timeout`-mode segment that closes on `initial_silence_timeout` with no speech). The bridge does not
filter these: a closed-on-silence segment writes an empty `speech_input`, so the agent can react to
the user saying *nothing* after dictation starts. There is no special-casing of the empty transcript.

The `on_final_segment` callback is **injected**, not the World itself, so the bridge is decoupled from
WICA and can be unit-tested with a recording callback and a fake engine (no `Wica`, no model).

## Mic source: the robot's echo-cancelled stream

The `ASREngine` reads from an injected **`audio_source`** (`source.start()` returns the
`asyncio.Queue[bytes]` the engine drains). Instead of opening a local device, the app builds a small
**`RobotMicSource`** adapter over the bridge's mic tap:

```python
class RobotMicSource:
    """Pumps ReachyMiniApi.audio_input() into the asyncio.Queue[bytes] the ASREngine drains."""

    def __init__(self, api: ReachyMiniApi) -> None: ...
    async def start(self) -> asyncio.Queue[bytes]: ...  # spawn the pump task, return the queue
    async def stop(self) -> None: ...                   # stop iterating, cancel the pump
```

- `api.audio_input()` is an **async iterator** yielding **int16 LE mono PCM `bytes`** at
  `api.mic_sample_rate` (**16 kHz**) — already the streaming-ASR contract (linear16 mono 16 kHz), so
  the pump is "iterate and `put_nowait`", no resampling or reformatting (see
  [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md), which explicitly frames the
  queue adapter as a few caller-side lines). This is the whole point of routing through the robot: the
  audio is already echo-cancelled and noise-suppressed by the XVF3800, so the app needs no processing.
- The ASR config's `audio.device` is therefore **irrelevant** (no local device is opened); the ASR
  module must be configured for **16 kHz mono linear16** to match `mic_sample_rate`.
- `RobotMicSource` shares the **one `ReachyMiniApi`** the app builds (the same instance behind `say`
  and the robot commands), so there is a single robot connection and one media session.

`RobotMicSource` lives in this speech-in module; the app injects it as
`ASREngine(cfg.asr_engine, audio_source=RobotMicSource(api))` and hands the engine to `AsrBridge`.

## `AsrBridge`

```python
OnFinalSegment = Callable[[str], None]


class AsrBridge:
    def __init__(self, engine: ASREngine, *, on_final_segment: OnFinalSegment) -> None: ...

    async def start(self) -> None:   # engine.start() in utterance mode
    async def stop(self) -> None:    # engine.stop()

    # Set as engine.on_speech_segment.
    async def _on_segment(self, segment: SpeechSegment) -> None: ...

    # Live view for the UI.
    @property
    def display_text(self) -> str: ...   # current interim transcript, "" after a final
    def status(self) -> dict: ...        # {"running", "dictating", "segmentation_mode"}

    # Dictation controls.
    async def start_dictation(self, segmentation_mode: str | None = None) -> str: ...
    async def stop_dictation(self) -> str: ...
```

- The constructor wires `engine.on_speech_segment = self._on_segment`. The engine is built from
  `AppConfig.asr_engine` and the `RobotMicSource` (see above) by the app and injected here.
- `start()` starts the engine in its default **`utterance`** mode (the engine always begins there;
  the app does **not** auto-start dictation — normal conversation is one segment per utterance). The
  ASR config's `auto_start` / `auto_start_dictation` signals are not honored: this app always starts
  the engine itself and stays in conversation (utterance) mode until the agent asks otherwise.
- `display_text` and `status()` are cheap reads the Gradio timer polls; `_on_segment` only mutates
  in-memory state and calls the callback, so it never blocks the ASR loop.

## Dictation — the agent's command

Dictation lets the user speak a **longer, free-form passage** that shouldn't be chopped into a turn
per sentence. It is entered by the agent, on request (e.g. the user says "let me dictate something"),
via a WICA Command the app registers:

```python
async def start_dictation(segmentation_mode: str | None = None) -> str:
    """Switch speech capture to dictation mode for one longer passage.

    Use when the user wants to dictate free-form text rather than converse in short
    turns. Speech keeps flowing to you as usual, but the current utterance is only
    delivered once the user pauses (or says the trigger word), instead of after every
    sentence. Dictation ends automatically after that one passage.
    """
```

Semantics (a thin wrapper over the ASR engine's dictation — see
[asr-engine engine.md](../../asr-engine/specs/engine.md), "Dictation"):

- Calls `engine.start_dictation(end_on_final_segment=True, segmentation_mode=segmentation_mode)`. The
  hard-wired **`end_on_final_segment=True`** is the "reset on segment end" behaviour: the mode swaps
  to `dictation_default_segmentation_mode` (typically `timeout` or `trigger_word`) for exactly one
  passage, then the engine reverts to `utterance` once that passage's final segment closes. The agent
  need not remember to turn it off.
- **Dictation does not change where speech goes.** Segments still flow through `_on_segment`: interims
  still stream into the ASR panel, and the closed segment still becomes `speech_input` for the agent.
  The *only* difference is segmentation — how long the app waits before closing a segment — so a
  dictated passage arrives as one big `speech_input` instead of several small ones. The agent is
  still fed and still replies (this is not a "capture verbatim, stay silent" mode).
- Returns a short status string the model can read back — `"Dictation started (<mode> mode)."`, where
  `<mode>` is read back from `engine.segmentation_mode` after the call (so it reflects the resolved
  default, not a guess).
- **Never raises.** The engine raises `ValueError` on misuse (not running, already dictating, unknown
  mode); the command catches it and returns a readable string (`"Could not start dictation: <msg>"`)
  instead. This is the same non-raising Command contract `say` follows (see
  [tts-output.md](tts-output.md)): the agent — and the UI stop button below — always get a string
  back, never an exception.

`stop_dictation()` (`engine.stop_dictation()`, revert to `utterance`) is exposed for the **UI stop
button** — a manual hatch to leave dictation without waiting for the passage to close. It is a valid
agent command too, but with `end_on_final_segment=True` the common path needs no explicit stop. Like
`start_dictation`, it catches the engine's `ValueError` (no dictation in progress) and returns a
readable string rather than raising.

## Status for the UI

`status()` composes the engine's own accessors — `engine.status()["running"]`, `engine.dictating`,
`engine.segmentation_mode` — into one dict the ASR panel renders, so a user can see at a glance
whether ASR is live and whether it is currently in dictation mode (see [app.md](app.md)).

## Testing

Drive `AsrBridge` against a fake `ASREngine` (no audio, no network) that lets a test push
`SpeechSegment`s through `_on_segment`, and `RobotMicSource` against a fake `ReachyMiniApi` whose
`audio_input()` yields a few PCM chunks:

- An interim segment updates `display_text` and does **not** call `on_final_segment`.
- A final segment calls `on_final_segment` once with its `transcript`, then `display_text` is `""`.
- A sequence interim→interim→final yields exactly one `on_final_segment` call, with the final text.
- `start_dictation()` calls the engine with `end_on_final_segment=True` and the resolved mode;
  `status()` reflects `dictating` / `segmentation_mode` from the (fake) engine.
- `RobotMicSource.start()` returns a queue that receives the api's mic chunks in order; `stop()`
  cancels the pump.

## Open questions

1. **Barge-in while the robot is speaking.** *Echo* is handled on the robot — the XVF3800 keeps ASR
   from hearing the app's own TTS (see [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md)),
   so this app adds none of its own. What remains deferred is *barge-in*: acting on speech that arrives
   mid-`say`, which the agent's one-call-at-a-time model drops (see [app.md](app.md)).
2. **Choosing the dictation segmentation mode.** Whether the agent should be able to pass
   `segmentation_mode` (e.g. pick `trigger_word` vs `timeout`) or always use the configured default
   is left open; the signature allows it, the system prompt can constrain it.
