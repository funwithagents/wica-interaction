# The personal space says what just changed, and is answered

**Status:** Todo

Implements [specs/reachy-embodiment.md](../specs/reachy-embodiment.md) ("Users: `UsersInput`": "The personal space says what just changed"; "Persona"). In a session on the robot, every `personal_space` change woke the agent, and the agent answered each one with `noop`. Two causes:

- the woken step read the same state as any other step, so stepping out read as *nobody is there*, with nothing marking the change;
- the persona allowed *a light remark, or none*.

The value now carries the change for `SPACE_CHANGE_S` before a silent settling rewrite, and the persona asks for a brief spoken reaction to entering and to stepping out.

## Scope

- `src/wica_interaction/reachy/users.py`: `PersonalSpace.change` and `.left`; `SPACE_CHANGE_S`; the producer writes a change and settles it; `personal_space_line` words both; the persona section asks for a reaction.
- `tests/reachy/test_users.py`: the change written, then settled without waking; the wordings.
- `tests-e2e/scenarios.py`: the scenario's "inside" / "nobody" predicates read the new fields unchanged (`user`); nothing else.
- `docs/reference/agent.md`, `docs/guides/reachy-mini.md`: the just-changed wording.

## Steps

1. Add `change` and `left` to `PersonalSpace` and `SPACE_CHANGE_S = 2 s`.
2. In the refresh, when who is inside differs from the last value written, write it with `change` (`came_in`, or `stepped_out` with `left`) and stamp the time. Once `SPACE_CHANGE_S` has passed, write it settled. `personal_space_changed` is unchanged, so the settling rewrite wakes no one.
3. Word the two changes; make the persona's personal-space line ask for a brief spoken reaction to each.
4. Tests, docs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`; the robot's e2e job dispatched on the branch.
