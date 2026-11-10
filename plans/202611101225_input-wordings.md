# Input wordings: what was perceived, and a dictation that is over

**Status:** Done

Implements [specs/app.md](../specs/app.md) ("Inputs are transient", "What the prompt says"), [specs/asr-bridge.md](../specs/asr-bridge.md) (a passage that closed a dictation is marked) and [specs/gesture-input.md](../specs/gesture-input.md) ("In the prompt", the persona section). The prompt's input lines say what the app perceived, never more than the input knows: speech is something heard, not the person's words; a sign is a gesture detected, not something the person is doing; and a dictated passage says the dictation is over and the passage complete. Prompted by the framework's benchmark, where an input wording contradicted another input, and by the end-to-end tier, where the model met a dictated note with `noop` and later said it was not finished.

## Scope

- `src/wica_interaction/asr_bridge.py` — `DictatedPassage`, a `str` subclass; a final whose `end_reason` is not `utterance` is passed as one.
- `src/wica_interaction/system.py` — the `speech_input` wordings (heard; a complete dictated passage) and the `hand_gesture_<n>` wording (detected).
- `src/wica_interaction/gesture_input.py` — the "Hand signs" persona section: a camera detecting gestures, not whose hand.
- `prompts/computer.md`, `prompts/reachy.md` — the empty-passage sentence no longer quotes the old wording.
- `tests/test_asr_bridge.py`, `tests/test_system.py`, `tests/test_gesture_input.py` — the marking, the wordings, a dictated passage reaching the prompt as complete.
- `specs/app.md`, `specs/asr-bridge.md`, `specs/gesture-input.md`, `docs/reference/agent.md`, `docs/guides/dictation.md`, `docs/guides/hand-signs.md` — the design and the documentation.

## Steps

1. Mark the dictated passage in the bridge, by the segment's `end_reason`.
2. Render each input from what was perceived; a `DictatedPassage` as complete.
3. Reword the persona section; update the personas' empty-passage sentence.
4. Tests, specs, docs.

## Verification

- `uv run pytest`: the marking (`trigger_word` and the timeouts marked, a conversational final not), the three wordings, and a `trigger_word` segment reaching the prompt as a complete passage.
- Lint, format, types.
