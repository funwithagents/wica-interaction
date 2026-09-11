# Empty finals: split the ASR path from the typed-text guard

**Status:** Done

> **As built.** The app tests emit segments through `fake_asr.on_speech_segment` — the callback the
> real `ASREngine` awaits, which the bridge claimed at construction — instead of calling
> `bridge._on_segment` directly: the same code path through a public seam (what the analysis item
> suggested). Both the rewritten and the new test start and tear down via `_start`/`_shutdown`.
> `specs/app.md` stays `Updated` past this plan only because the UI-efficiency plan edits it next.

Resolves `specs/_analysis.md` item 3 in favor of the **spec** (the empty-final contract is real): a
closed-on-silence ASR segment must still reach the agent so it can react to the user saying nothing,
as `specs/asr-bridge.md` ("The segment → world rule") states and `test_empty_final_still_fires` pins.
Today the shared `submit_speech` funnel strips and drops empties, so an empty final is silently
discarded before it reaches the World. This plan splits the two input paths: the **typed-text** box
keeps its empty guard (an empty typed line has nothing to reply to), while the **ASR final** path
delivers every final — empty included. It also fixes the tautological
`test_closed_segment_reaches_agent_like_typed_text` flagged under "Bad patterns" by driving a real
`SpeechSegment` through the bridge instead of calling the typed-text funnel.

> **Sibling consistency.** The sibling `../vocal-interaction-wica` agreed (2026-09-11) to handle empty
> finals the same way — its bridge already fires on empty finals and its `specs/asr-bridge.md` open
> question 3 ("Silent dictation") is the matching decision point. The split described here is the
> shared shape; see `../vocal-interaction-wica/specs/_reachy-consistency-backlog.md` (item 5).

## Scope

- `src/reachy_mini_interaction_wica/app.py` — split the one `submit_speech` closure into a
  non-stripping `_deliver_speech(text)` (agent-running guard only) and a `submit_speech(text)` that
  strips + drops empties then delegates to `_deliver_speech`; wire the bridge's `on_final_segment` to
  `_deliver_speech` while the typed-text box (`AppSystem.submit_speech`) keeps `submit_speech`.
- `tests/test_app.py` — rewrite `test_closed_segment_reaches_agent_like_typed_text` to push a real
  `SpeechSegment` through the bridge's segment callback on the app loop; add a test that an **empty**
  final still wakes the agent; keep `test_empty_typed_text_is_ignored` covering the typed path (its
  timing weakness is addressed in the test-hygiene plan).
- `specs/app.md` — clarify in "Data flow" / "Typed-text input" that the empty guard is the typed-text
  path's alone, and that a closed ASR final (empty included) always reaches `speech_input`.

## Steps

1. **Split the funnel.** In `build_system`, replace the single `submit_speech` closure with two:

   ```python
   def _deliver_speech(text: str) -> None:
       # The ASR-final path: every closed segment reaches the agent, including an empty/whitespace
       # one (react-to-silence, per specs/asr-bridge.md). Guarded on a running agent — a segment
       # arriving before the agent is up, or after teardown, is dropped.
       if wica is not None and wica.is_running:
           wica.world.update("speech_input", text)


   def submit_speech(text: str) -> None:
       # The typed-text path: an empty typed line has nothing to reply to, so it is inert.
       text = text.strip()
       if text:
           _deliver_speech(text)
   ```

2. **Rewire the bridge callback.** Build the `AsrBridge` with `on_final_segment=_deliver_speech`
   (both the injected-engine branch and the real `ASREngine` branch), so a closed segment — empty or
   not — flows straight to `speech_input`. Keep `AppSystem.submit_speech = submit_speech`: the
   typed-text box (`app_ui.on_send`) is unchanged and still ignores empties.

3. **Confirm the serializer handles empty.** `_speech_input` already renders a non-`None` value as
   `The person said to you: "<value>"`; an empty string is a valid value (distinct from the `None`
   "hasn't said anything yet" case), so no change is needed there. Note in a comment that an empty
   final is delivered verbatim (not coerced to `None`).

4. **Fix the tautological app-level test.** Rewrite `test_closed_segment_reaches_agent_like_typed_text`
   to construct a `SpeechSegment(transcript="please reply", is_final=True, end_reason="utterance",
   utterances=[])` and drive it through the bridge on the app loop
   (`asyncio.run_coroutine_threadsafe(system.bridge._on_segment(seg), system.loop).result()`), then
   assert the agent's `say` reached the fake robot. This exercises the real
   engine-callback → `_on_segment` → `_deliver_speech` → World wiring the old test skipped by calling
   `submit_speech` directly.

5. **Add an empty-final app test.** `test_empty_final_segment_wakes_agent`: push a final segment with
   `transcript=""` through the bridge on the loop and assert a reasoning step ran (a prompt was
   captured / the scripted agent reacted). This is the app-level counterpart to the bridge's
   `test_empty_final_still_fires`, proving the empty final is no longer dropped at the funnel.

6. **Spec update.** In `specs/app.md`, adjust "Data flow" and "Typed-text input" so it is explicit
   that the empty guard belongs to the typed-text hatch only, and that every closed ASR final
   (including an empty/whitespace one) is written to `speech_input`. This aligns app.md with
   asr-bridge.md, which already documents the react-to-silence contract. app.md is `Implemented`; set
   it to `Updated` on the edit and back to `Implemented` once this plan is `Done`.

## Verification

- `test_closed_segment_reaches_agent_like_typed_text` (rewritten) and `test_empty_final_segment_wakes_agent`
  (new) pass, driving the real bridge path.
- `tests/test_asr_bridge.py::test_empty_final_still_fires` remains green (bridge behavior unchanged).
- `test_empty_typed_text_is_ignored` remains green (typed-text empties still inert).
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. Mark `Done` and flip
  `specs/app.md` back to `Implemented` only once all three pass.
