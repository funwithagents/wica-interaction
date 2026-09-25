# Transient inputs: a TTL and an omitted `None` on speech and hand signs

**Status:** Done

Implements the settled behavior in `specs/app.md` ("Inputs are transient") and `specs/gesture-input.md` ("In the prompt: a one-shot event, then nothing"). Makes `speech_input` and every `hand_gesture_<n>` entry a one-shot observation: registered with a World TTL (2 s for speech, 1 s for a sign) so the World clears the value shortly after the step it triggered has observed it, and serialized to an empty `Content` when `None`, which WICA (since its "omit an entry whose serializer returns `[]`" change) turns into no entry in the prompt at all. Deliberately leaves out any refresh of a held sign (deliveries stay change-only) and any config knob for the two durations (module constants for now).

## Scope

- `src/wica_interaction/system.py` — `SPEECH_INPUT_TTL` / `HAND_GESTURE_TTL` constants; `_speech_input` and `_hand_gesture` return `[]` for `None` (the "hasn't said anything yet" / "not showing a sign" lines are gone); both registrations pass `ttl=`
- `src/wica_interaction/gesture_input.py` — the "Hand signs" persona text describes a sign state as what the person just did, gone a moment later, never promising a "showing none" state; `sign_shown`'s docstring notes the TTL
- `tests/test_system.py` — serializer tests (a value renders its line, `None` renders `[]` whether never set or expired); an utterance is cleared and unrendered after its TTL with no extra step; the vision test asserts the sign-less hand is omitted, the sign expires with no step and the World renders no hand entry while the gesture input still holds the sign
- `tests/test_gesture_input.py` — the persona-section test pins the one-shot wording
- `specs/app.md`, `specs/gesture-input.md`, `specs/_overview.md`, `specs/_index.md` — the design above

## Steps

1. Add the two `timedelta` constants next to the serializers in `system.py`, with the rationale (the coalescing window observes the input; later steps must not).
2. Make both serializers return `[]` for `None`; register `speech_input` and each hand entry with the matching `ttl=`.
3. Reword `_HAND_SIGNS_TEXT` in `gesture_input.py` for one-shot states.
4. Update the tests (above) and run them; then the specs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all pass (240 tests). The new tests: `test_speech_serializer_renders_an_utterance_and_omits_none`, `test_hand_serializer_renders_a_sign_and_omits_none`, `test_an_utterance_is_gone_from_the_world_and_the_prompt_after_its_ttl`, `test_a_sign_wakes_the_agent_and_is_gone_from_the_prompt_after_its_ttl`, and the extended `test_hand_signs_prompt_section_names_the_whole_vocabulary_and_the_stop_rule`.
