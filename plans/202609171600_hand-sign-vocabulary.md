# Hand-sign vocabulary, one entry per hand

**Status:** Done

Implements the updated behavior in `specs/gesture-input.md` ("The reduction: label → sign, per hand", "Change detection and the trigger rule", "In the prompt", "What the agent does with it") and the matching parts of `specs/app.md` (World entries, build step 8, `display_entry`, the Vision line). Replaces the fist-only `hand_gesture` input with a hardcoded HaGRID label → sign table (`stop`, `like`, `dislike`, `ok`, `mute`, `one` … `five`), delivered per hand into `hand_gesture_1` … `hand_gesture_<max_hands>`, triggering on any sign shown, without bypassing coalescing. Deliberately leaves out debouncing, left/right keying and a configurable table (spec open questions 1, 2, 5).

## Scope

- `src/reachy_mini_interaction_wica/gesture_input.py` — `SIGNS` table, `sign_shown` predicate (replaces `FIST` / `fist_raised`), per-slot reduction and change detection, `OnGesture = Callable[[int, str | None], None]`, `status()["signs"]` (replaces `"gesture"`), module docstring
- `src/reachy_mini_interaction_wica/app.py` — register one `hand_gesture_<n>` entry per `max_hands` (no `bypass_coalescing`), per-hand `serialize_fn`, `_deliver_gesture(hand, value)`, `AppSystem.gesture` comment
- `src/reachy_mini_interaction_wica/app_ui.py` — `display_entry` for `hand_gesture_<n>`, Vision line `signs:` segment
- `src/reachy_mini_interaction_wica/config.py` — docstring wording only (fist → hand signs)
- `prompts/assistant.md` — "Stop sign: a raised fist" becomes "Hand signs" (vocabulary + slot note, no interpretation) with the explicit `stop` rule kept
- `tests/test_gesture_input.py` — rewritten around signs and per-hand deliveries
- `tests/test_app.py`, `tests/test_app_ui.py` (wherever `hand_gesture` / `FIST` is asserted) — per-hand entries, trigger, rendering
- `tests-e2e/test_gesture_input_live.py` — each bundled photo delivers its sign for hand 1
- `AGENTS.md` (project map row for `gesture_input.py`), `README.md` (vision-modules bullet) — fist → hand signs
- `specs/gesture-input.md`, `specs/app.md`, `specs/_index.md` — back to `Implemented` once done

## Steps

1. **`gesture_input.py`.**
   - Replace `FIST` with `SIGNS: Mapping[str, str]` exactly per the spec table (17 labels; `call` absent) and `fist_raised` with `sign_shown(old, new) -> bool: new is not None and new != old`.
   - `OnGesture = Callable[[int, str | None], None]`; store `self._signs: list[str | None] = [None] * max_hands`.
   - In `_watch`, for each new result compute `values = [SIGNS.get(hands[i].label) if i < len(hands) and hands[i].label is not None else None for i in range(max_hands)]`; under the lock diff against `self._signs`, update it, collect `(i + 1, value)` changes; deliver them outside the lock in hand order. `_deliver(hand, value)` keeps the catch-and-log.
   - `status()`: `"signs": list(self._signs)` instead of `"gesture"`.
2. **`app.py`.**
   - `_hand_gesture_line(n)` returns a `serialize_fn` rendering `The person's hand {n} is showing the "{sign}" sign.` / `The person's hand {n} is not showing a sign.`
   - In `build_system`, register `hand_gesture_{n}` for `n in range(1, vision.max_hands + 1)` with `triggers_llm_call=True, trigger_condition_fn=sign_shown` (drop `bypass_coalescing`); `_deliver_gesture(hand, value)` writes `world.update(f"hand_gesture_{hand}", value)` guarded on `wica.is_running`.
3. **`app_ui.py`.**
   - `display_entry`: keys matching `hand_gesture_<digits>` → `✋ hand <n>: <sign>` / `✋ hand <n>: no sign`.
   - `tick_vision`: replace `**stop sign:** …` with `**signs:** #1 —, #2 stop` from `status["signs"]`.
4. **Persona.** Rewrite the section in `prompts/assistant.md` per the spec: you can see the person's hands; each `hand_gesture_<n>` state gives hand n's sign — `stop`, `like`, `dislike`, `ok`, `mute`, `one`, `two`, `three`, `four`, `five` — or none; numbers follow the hands in view, not left/right. Then the kept `stop` rule (cancel all running commands, start nothing new, confirm out loud next turn, not in the same turn, a `stop` going away is no request). No guidance on the other signs.
5. **Tests** (`tests/test_gesture_input.py`), reusing the scripted api/detector/classifier seams, per the spec's Testing list: single-hand `like` delivered once as `(1, "like")`; `None → like → None → like`; table coverage (`fist`/`stop_inverted` → `stop`, `rock` → `two`, `three2` → `three`, `palm` → `five`); direct `one → two`; same-sign relabel `peace → rock` delivers nothing; sub-threshold and `call` deliver nothing; two hands deliver per-hand changes and `(2, None)` alone when hand 2 relaxes; `max_hands=1` only ever delivers hand 1; `sign_shown` truth table; `status()["signs"]` length `max_hands`. Keep the fps, snapshot, camera-source and stop tests (adapt callbacks to two args).
6. **App/UI tests.** `hand_gesture_1`/`_2` registered with `max_hands=2`; `None → two` on `hand_gesture_2` triggers a step whose prompt carries both hand lines; `two → None` does not trigger; `display_entry` renders both forms; existing vision-off / no-robot / teardown tests keep passing.
7. **E2E.** Parametrize the live test over `hand_fist.jpg`/`hand_stop.jpg` → `stop`, `hand_palm.jpg` → `five`, `hand_one.jpg` → `one`, `hand_three2.jpg` → `three` (each: `deliveries == [(1, sign)]`), and `no_hand.jpg` → `[]`.
8. **Docs + statuses.** Update the AGENTS.md map row and README bullet; flip `specs/gesture-input.md` and `specs/app.md` to `Implemented` (file + index) and this plan to `Done`.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` all pass (including `tests/test_project_map.py`). Optionally `uv run pytest tests-e2e/test_gesture_input_live.py` with the models available. Then a manual run on the robot to watch the agent's unprompted reactions to signs. Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
