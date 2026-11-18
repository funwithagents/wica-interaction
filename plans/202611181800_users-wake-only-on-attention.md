# The users wake the agent only on whom it attends to

**Status:** Done

Implements `specs/reachy-embodiment.md` ("Users: `UsersInput`", as updated 2026-11-18) and the matching scenario 4 of `specs/e2e.md`: a `user_event` wakes the agent only when the tracked user (followed or pinned) leaves, or a user arrives while nobody is tracked; `personal_space` no longer wakes it. Leaves out any awareness of the robot's own moves and any distance gate on arrivals (open question 6).

## Scope

- `src/wica_interaction/reachy/users.py` — `UserChange.wakes`, `user_event_wakes`, the module's tracked-users set, the tracked departure's wording, `personal_space` registered without a trigger, the persona section reworded
- `tests/reachy/test_users.py` — which events wake (through a real World), the tracked departure's wording, `user_event_wakes` as a pure function; the personal space no longer waking
- `tests-e2e/scenarios.py` — scenario 4: the personal space delivered, no reaction awaited
- `docs/reference/agent.md`, `docs/guides/reachy-mini.md`, `README.md` — what wakes the agent, if they say otherwise

## Steps

1. `UserChange` gains `wakes: bool = False`; `change_line` words a waking `left` as `User <n>, whom your head was following, left your view.`
2. `user_event_wakes(old, new)`: true when *new* holds a change with `wakes` that *old* does not hold; the `user_event` entry uses it as its `trigger_condition_fn`.
3. `UsersInput` keeps `_tracked: set[int]`, refreshed in `_refresh` from `bridge.head_tracking` (`track_id`, `pinned_track_id` while active), keeping a previously tracked user no longer present until `_on_users` has judged their departure; reset on `start`.
4. `_on_users` and the start-up snapshot mark `wakes`: a `left` (reason `"left"`) of a user in `_tracked`; an `arrived` / `present` when `_tracked` minus that report's arrivals is empty. Departed users are then removed from `_tracked`.
5. `personal_space`'s entry: `triggers_llm_call=False`, no `trigger_condition_fn`; `personal_space_changed` stays the rule `_space` uses to annotate a change.
6. `users_prompt_section()`: the two wakes, the rest read not reacted to, the switch when the followed user leaves; the personal-space reaction removed.
7. Tests, then the e2e scenario and the docs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`. The e2e scenario 4 is run by hand when the sim is available. Then flip this plan to `Done` and both specs back to `Implemented`.
