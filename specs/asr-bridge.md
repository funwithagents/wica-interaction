---
code:
  - src/wica_interaction/asr_bridge.py
tests:
  - tests/test_asr_bridge.py
---

# ASR bridge

**Status:** Updated

> **Updated (2026-09-17):** the bridge no longer knows the robot. `RobotMicSource` moves to the [Reachy Mini embodiment](reachy-embodiment.md); the engine's source comes from `embodiment.mic` ([embodiment.md](embodiment.md)) and the bridge takes a `mic_error` callable for its status line. The segment → World rule and dictation are unchanged. Code catches up in the [merge plan](../plans/202609172311_wica-interaction-merge.md).

## Purpose

The **speech-in** half of the app: it owns the live [`ASREngine`](../../asr-engine/specs/engine.md) — fed whatever audio source the embodiment provides (the robot's echo-cancelled stream, the computer's processed or raw mic) — turns its stream of speech *segments* into WICA World updates, and exposes the **dictation** control the agent can invoke. It is the one place that knows how ASR segmentation maps onto "a thing the user said", so the rest of the app (the WICA agent, the UI) never touches the ASR engine directly.

Two consumers read from it:

- **The WICA World** — a *final* segment becomes the `speech_input` the agent reasons over.
- **The Gradio UI** — the *live, growing* segment text and the current ASR status are displayed as they change ([app.md](app.md), "ASR panel").

## The segment → world rule (the core decision)

The ASR engine emits a `SpeechSegment` on **every** change: interim updates while the segment grows (`is_final=False`, `end_reason=None`) and one final emission when it closes (`is_final=True`). The bridge treats the two completely differently:

| Segment | What the bridge does |
|---|---|
| **Interim** (`is_final=False`) | Updates its **live display text** to `segment.transcript`. Does **not** touch the World — the agent is not triggered. |
| **Final** (`is_final=True`) | Calls the injected **`on_final_segment(transcript)`** callback (the app wires this to `world.update("speech_input", transcript)`, which triggers one reasoning step) and then **clears the live display text** back to `""`. |

So partial speech is *visible but inert* — it streams into the ASR panel character by character — and only a closed segment reaches the agent, exactly once, as one complete utterance. The World's `speech_input` entry changes once per closed segment, never on interims, so a burst of interim updates cannot wake the agent mid-utterance.

**Every** final fires the callback — including one whose transcript is empty or whitespace (e.g. a `timeout`-mode segment that closes on `initial_silence_timeout` with no speech). The bridge does not filter these: a closed-on-silence segment writes an empty `speech_input`, so the agent can react to the user saying *nothing* after dictation starts. What an empty utterance *means* is the consumer's decision ([app.md](app.md), "The input funnel"), and both personas tell the model.

The `on_final_segment` callback is **injected**, not the World itself, so the bridge is decoupled from WICA and can be unit-tested with a recording callback and a fake engine (no `Wica`, no model).

## The mic is the embodiment's

The `ASREngine` reads from an injected **`audio_source`** ([`AudioSource`](../../asr-engine/specs/audio-source.md): a synchronous `start() -> asyncio.Queue[bytes]` / `stop()` the engine drives). Which source — and whether there is one at all — is the embodiment's choice, not this module's ([embodiment.md](embodiment.md), `mic`):

- the Reachy Mini embodiment supplies a `RobotMicSource` pumping the robot's echo-cancelled 16 kHz stream ([reachy-embodiment.md](reachy-embodiment.md));
- the computer embodiment supplies the audio-processing coordinator's `source` when a feature is on ([audio-processing.md](audio-processing.md)), or `None`, in which case the engine opens its own `AudioCapture` on the ASR config's `audio.device`.

The app builds `ASREngine(cfg.asr_engine, audio_source=embodiment.mic)` and hands the engine to `AsrBridge`. The bridge never sees the source; what it gets is a way to report the source's **capture health** — `mic_error`, a callable returning the embodiment's `mic_error` (`None` while healthy) — because the engine can keep reporting `running` while its stream has died, and the ASR panel must show that.

## `AsrBridge`

```python
OnFinalSegment = Callable[[str], None]
MicError = Callable[[], str | None]


class AsrBridge:
    def __init__(
        self,
        engine: ASREngine,
        *,
        on_final_segment: OnFinalSegment,
        mic_error: MicError
        | None = None,  # the embodiment's capture health, for status()
    ) -> None: ...

    async def start(self) -> None: ...  # engine.start() in utterance mode
    async def stop(self) -> None: ...  # engine.stop()

    # Set as engine.on_speech_segment.
    async def _on_segment(self, segment: SpeechSegment) -> None: ...

    # Live view for the UI.
    @property
    def display_text(self) -> str: ...  # current interim transcript, "" after a final
    def status(
        self,
    ) -> dict: ...  # {"running", "dictating", "segmentation_mode", "capture_error"}

    # Dictation controls.
    async def start_dictation(self, segmentation_mode: str | None = None) -> str: ...
    async def stop_dictation(self) -> str: ...
```

- The constructor wires `engine.on_speech_segment = self._on_segment`. The engine is built from `AppConfig.asr_engine` and the embodiment's mic by the app and injected here.
- `start()` starts the engine in its default **`utterance`** mode (the engine always begins there; the app does **not** auto-start dictation — normal conversation is one segment per utterance). The ASR config's `auto_start` / `auto_start_dictation` signals are not honored: this app always starts the engine itself and stays in conversation mode until the agent asks otherwise.
- `display_text` and `status()` are cheap reads the Gradio timer polls; `_on_segment` only mutates in-memory state and calls the callback, so it never blocks the ASR loop.

## Dictation — the agent's command

Dictation lets the user speak a **longer, free-form passage** that shouldn't be chopped into a turn per sentence. It is entered by the agent, on request (e.g. "let me dictate something"), via a WICA Command the app registers:

```python
async def start_dictation(segmentation_mode: str | None = None) -> str:
    """Switch speech capture to dictation mode for one longer passage.

    Use when the user wants to dictate free-form text rather than converse in short
    turns. Speech keeps flowing to you as usual, but the current utterance is only
    delivered once the user pauses (or says the trigger word), instead of after every
    sentence. Dictation ends automatically after that one passage.
    """
```

Semantics (a thin wrapper over the ASR engine's dictation — [asr-engine engine.md](../../asr-engine/specs/engine.md), "Dictation"):

- Calls `engine.start_dictation(end_on_final_segment=True, segmentation_mode=segmentation_mode)`. The hard-wired **`end_on_final_segment=True`** is the "reset on segment end" behaviour: the mode swaps to `dictation_default_segmentation_mode` (typically `timeout` or `trigger_word`) for exactly one passage, then the engine reverts to `utterance` once that passage's final segment closes. The agent need not remember to turn it off.
- **Dictation does not change where speech goes.** Segments still flow through `_on_segment`: interims still stream into the ASR panel, and the closed segment still becomes `speech_input`. The *only* difference is segmentation — how long the app waits before closing a segment — so a dictated passage arrives as one big `speech_input`. The agent is still fed and still replies.
- Returns a short status string the model can read back — `"Dictation started (<mode> mode)."`, `<mode>` read back from `engine.segmentation_mode` after the call.
- **Never raises.** The engine raises `ValueError` on misuse (not running, already dictating, unknown mode); the command catches it and returns `"Could not start dictation: <msg>"`. The same non-raising Command contract `say` follows ([embodiment.md](embodiment.md)).

`stop_dictation()` (`engine.stop_dictation()`, revert to `utterance`) backs the **UI stop button** — a manual hatch to leave dictation without waiting for the passage to close. It is a valid agent command too, but with `end_on_final_segment=True` the common path needs no explicit stop. It catches the engine's `ValueError` (no dictation in progress) and returns a readable string.

## Status for the UI

`status()` composes the engine's own accessors — `engine.status()["running"]`, `engine.dictating`, `engine.segmentation_mode` — plus `capture_error` (the `mic_error()` result, or `None` when the bridge was given no callable) into one dict the ASR panel renders, so a user sees at a glance whether ASR is live, whether it is in dictation mode, and whether capture has stalled even though the engine still says `running` ([app.md](app.md)).

## Testing

Drive `AsrBridge` against a fake `ASREngine` (no audio, no network) that lets a test push `SpeechSegment`s through `_on_segment`:

- An interim segment updates `display_text` and does **not** call `on_final_segment`.
- A final segment calls `on_final_segment` once with its `transcript`, then `display_text` is `""`; an empty final still fires.
- A sequence interim→interim→final yields exactly one `on_final_segment` call, with the final text.
- `start_dictation()` calls the engine with `end_on_final_segment=True` and the resolved mode; `status()` reflects `dictating` / `segmentation_mode` from the (fake) engine.
- `status()["capture_error"]` is whatever the injected `mic_error` returns at that moment, and `None` without one.

The mic sources themselves are tested with their embodiments ([reachy-embodiment.md](reachy-embodiment.md), [audio-processing.md](audio-processing.md)).

## Open questions

1. **Barge-in while the agent is speaking.** *Echo* is handled per embodiment — the XVF3800 on the robot, the audio-processing coordinator on the computer — so this module adds none. What remains deferred is *barge-in*: acting on speech that arrives mid-`say`, which the agent's one-call-at-a-time model drops ([app.md](app.md)).
2. **Choosing the dictation segmentation mode.** Whether the agent should be able to pass `segmentation_mode` (e.g. pick `trigger_word` vs `timeout`) or always use the configured default is left open; the signature allows it, the system prompt can constrain it.
