---
code:
  - src/reachy_mini_interaction_wica/tts_output.py
tests:
  - tests/test_tts_output.py
---

# TTS output

**Status:** Implemented

## Purpose

The **speech-out** half of the app: it exposes a single `say(text)` coroutine that is registered as WICA's **output Command** — the agent's user-facing voice — and speaks it **through the robot** via `reachy-mini-bridge`'s `ReachyMiniApi.say`. Everything the agent chooses to say to the user is spoken here; the model's free text (the `output_sink`) becomes private *thinking* shown apart from the voice, exactly as in the WICA conversation demo (see [app.md](app.md), "Conversation panel").

Playing through the robot is not a style choice: on Reachy Mini, audio reaches the speaker only via the daemon pipeline, and the on-board XVF3800 **echo cancellation** works only if playback goes through it (see [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md)). So the app does **not** own a `TTSEngine` or a speaker; the bridge owns the output path behind its pluggable `SpeechSynthesizer` (the app supplies the default `tts-engine`-backed one from the `tts` config — see [configuration.md](configuration.md)), and `say` is a thin, non-raising wrapper over `api.say`.

## Why an output Command, not the output sink

WICA delivers each reasoning step two ways: the model's free text goes to the `output_sink`, and an **output Command** (configured via `Wica.init(output_command=say)`) becomes the actual voice. Wiring `say` to the robot's speech makes speaking a first-class, observable, cancellable action:

- The words the user *hears* are precisely the `say` calls, not incidental free text.
- Because `say` is a Command, it is tracked as a World entry (`running` while it speaks, terminal when done) and can be cancelled — so the World/Conversation view shows a `say` in flight, and the agent could `cancel_command` it. (Cancelling the `say` task is also how the bridge stops speech in v1 — see [reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/api.md).)
- The model's free text is freed up to be private reasoning (the `💭 output sink`), separating "what the robot is thinking" from "what it says out loud".

## `say`

```python
class VoiceOutput:
    def __init__(self, api: ReachyMiniApi) -> None: ...

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the person, out of the robot's speaker.

        This is your voice: whatever you want the person to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
```

- Built from the **one shared `ReachyMiniApi`** the app constructs (with the configured synthesizer and robot backend); the same instance backs [robot-control.md](robot-control.md), so there is a single robot connection and one media session. `VoiceOutput` holds the api and calls `api.say`.
- `say` **must be non-raising**, the contract WICA Commands want: the agent always gets a readable string back, never an exception. `api.say` returns `None` and *raises* on failure (empty text, a synthesis error, a media error), so `VoiceOutput.say` guards it — an empty/whitespace string returns `"TTS error: text must not be empty"` without calling the robot, and any exception from `api.say` is mapped to `"TTS error: <message>"`. Success returns `"OK"`.
- `say` **awaits** `api.say`, which completes when the utterance has finished playing on the robot, so the Command stays `running` in the World for the real duration of the speech.

`VoiceOutput.say` is a **bound method**, so registering it as the output Command preserves its name, signature, and docstring — the docstring above is what the model sees as the voice's description.

## Testing

Drive `VoiceOutput` against a fake `ReachyMiniApi` (or the bridge's own `fake` backend — no audio hardware, no cloud TTS): a `say`/synth double that records calls and can be made to raise.

- `say("hello")` calls `api.say` once with `"hello"` and returns `"OK"`.
- `say("")` returns the empty-text error string and does **not** call `api.say`.
- An exception from `api.say` is mapped to `"TTS error: <message>"`, not raised.

There is **no TTS-only live test**: exercising real synthesis + robot playback for `say` in isolation isn't worth its own credential/hardware dependency. A live test covering the *whole* speech interaction (mic → agent → robot speaker) may be added to the opt-in `tests-e2e/` tier later, against the `sim` backend; see [testing.md](testing.md).

## Open questions

None currently. A future streaming/word-by-word reveal of the spoken text into the UI (as the demo *simulates*) is out of scope; here `say` is real audio and the transcript shows the full utterance when the Command runs. True barge-in (a `stop_talking` flush) is deferred in the bridge itself.
