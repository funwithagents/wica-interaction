# Drop agent-facing head tracking; catch up with sibling changes

**Status:** Done

Implements the "Head tracking is not agent-facing" section added to [specs/robot-control.md](../specs/robot-control.md) (and the matching edits in [specs/app.md](../specs/app.md)): removes `start_head_tracking` / `stop_head_tracking` from `RobotControl` and from the agent's registered commands. Head tracking joins presence/breathing/wobbling as a bridge-managed mode the agent never touches, rather than staying an agent verb the persona has to be told not to over-manage. Once `reachy-mini-bridge` and `wica-framework` landed their own committed changes (the bridge's `motion` config consolidation + default-on tracking, wica's `PromptLog` → `ReactionLog` merge), this plan grew to catch this app up with both, since the head-tracking removal only fully verifies once the app builds and tests pass against the sibling packages' current state.

## Why

Observed: the model proactively started/stopped head tracking and reasoned about managing idle behavior (breathing, wobbling) that isn't even exposed to it, treating "looking alive" as its own job. The bridge's motion loop already keeps the robot alive between verbs on its own (presence, breathing, wobbling — see [reachy-mini-bridge motion.md](../../reachy-mini-bridge/specs/motion.md)), all on by default and none needing a caller action. The first fix considered was a persona correction (an explicit "you don't need to manage this" rule); the simpler, more consistent fix is to not expose the one knob (tracking) that made this look like the agent's responsibility in the first place — mirroring how presence/breathing/wobbling were never agent-facing to begin with. A short persona note was kept anyway, as cheap insurance against the model reaching for a tracking-like command out of habit.

While this was in flight, `reachy-mini-bridge` shipped the natural counterpart on its side: `motion.tracking` (default `true`), consolidating `wobbling` into the `motion` config block alongside `presence`/`breathing`/`tracking`, with tracking armed automatically once motors read `enabled` — exactly the dependency this plan's spec edits anticipated. Separately, `wica-framework` merged its prompt-history and reaction-instrumentation panels into one `ReactionLog`/`reaction_panel` (`PromptLog`/`prompt_panel` no longer exist), which broke this app's imports as an unrelated side effect of the path-dependency bump. Both are addressed here so the full verification gate (lint/format/type-check/tests) passes end to end.

## Scope

- `src/reachy_mini_interaction_wica/robot_control.py` — remove `start_head_tracking` / `stop_head_tracking`; update the module docstring and `set_motors_state`'s docstring.
- `src/reachy_mini_interaction_wica/app.py` — drop the two commands from `wire`'s registration loop; update the comment and the `PromptLog` → `ReactionLog` mention in the module docstring.
- `src/reachy_mini_interaction_wica/app_ui.py` — adopt `ReactionLog`/`reaction_panel` in place of `PromptLog`/`prompt_panel`; add a "Reaction" heading in the debug column.
- `tests/test_robot_control.py` — remove the tracking-only tests, trim the motor-precondition tests to `play_emotion` only; assert on `media.play_sound` instead of the now-removed `async_play_move` (the bridge's motion loop plays emotions as a queued move + sidecar sound, not a discrete "move" command — see [reachy-mini-bridge motion.md](../../reachy-mini-bridge/specs/motion.md) "Emotions through the loop").
- `tests/test_app.py` — rename `PromptLog`/`prompts` → `ReactionLog`/`reactions` throughout.
- `config.example.json` — add an explicit `reachy.motion` block (`presence`/`breathing`/`wobbling`/`tracking`, all `true`) documenting the bridge's motion posture, matching how the bridge documents its own `config.example.json`.
- `prompts/assistant.md` — remove the "Looking at the person" bullet; fold a short "you don't need to manage your body beyond these two commands" note into "How you move"; drop the stale head-tracking example from "One body: never collide, act in order".
- `specs/robot-control.md` — new "Head tracking is not agent-facing" section, trimmed command list/docstrings, open questions 3; status stays `Implemented`.
- `specs/app.md` — data flow, Commands/Persona bullets, action-rules example, "What the user sees" panel descriptions (Prompt → Reaction, two tabs), wiring section; status stays `Implemented`.
- `specs/_overview.md` — data flow diagram, module table, "what it is/isn't" bullets (editorial catch-up; not status-tracked).
- `AGENTS.md` — project-map row for `robot_control.py`.
- `specs/_index.md`, `plans/_index.md` — status/row updates.

## Steps

1. Remove the two commands from `RobotControl` and from `app.py`'s registration.
2. Update the persona text and specs as scoped above.
3. Adopt `ReactionLog`/`reaction_panel` in `app_ui.py`, and rename the matching test fixtures in `test_app.py`.
4. Add the explicit `motion` block to `config.example.json`.
5. Trim/fix the tests (tracking removal + the bridge's new emotion-recording shape); verify (below).

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all pass (72 passed). Persona wording is verified by reading, not by a test (as with the prior persona-rules plan).
