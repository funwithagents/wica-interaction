# Prompt-seeded emotion names

**Status:** Done

Implements the settled behavior in [specs/robot-control.md](../specs/robot-control.md) ("The agent knows the emotion names from the start") and [specs/app.md](../specs/app.md) (build step 4, "Emotion names"). The app fetches the robot's emotion library **once at startup** and folds the names into the system prompt and into `play_emotion`'s tool description, so the model knows the valid names before the first turn; the `list_emotions` command is **removed** from the agent-facing surface. It deliberately leaves out any intent → emotion mapping (robot-control open question 2) and any enum-typed `name` argument (a plain string, validated by the bridge, stays).

## Scope

- `src/reachy_mini_interaction_wica/robot_control.py` — drop `list_emotions`; `RobotControl(api, *, emotions=None)` takes the startup snapshot; new `commands()` yields the Commands to register, with `play_emotion`'s description naming the valid names when known; `play_emotion`'s failure string names them too (no more "call list_emotions"); module-level `emotions_prompt_section(names)` renders the persona addendum.
- `src/reachy_mini_interaction_wica/app.py` — `build_system` fetches `api.list_emotions()` on the loop after the robot is entered (a failure degrades to a `Robot: …` banner line, never a crash), resolves the persona itself with WICA's public `resolve_system_prompt`, appends the section, and hands `Wica.init` an inline-prompt variant of the config (`dataclasses.replace`); `AppSystem` carries `emotions`; `wire` registers `RobotControl(api, emotions=…).commands()`.
- `prompts/assistant.md` — the Emotions bullet points at the seeded list instead of `list_emotions`.
- `specs/robot-control.md`, `specs/app.md`, `specs/_overview.md`, `AGENTS.md` — the command set, the build sequence, the resolved open questions, the project-map row.
- `tests/test_robot_control.py` — replace the `list_emotions` test with the description/failure-string behavior, with and without a snapshot.
- `tests/test_app.py` — the fake api grows `list_emotions` (and a raise switch); tests for the seeded system prompt + description, the fetch-failure degrade, and the no-robot case; the bad-prompt-file degrade test keeps passing through the app's own read.
- `tests/test_app_ui.py` — the two hand-built `AppSystem`s gain the new field.

## Steps

1. **Robot control.** Remove `list_emotions`. Add the `emotions` keyword (a sequence of names, or `None` when unknown) and `commands()` returning `(Command(play_emotion, description=…), get_motors_state, set_motors_state)` — the description override only when names are known, else the docstring. `play_emotion`'s unknown-name reply appends `Valid names: …` when known. Add `emotions_prompt_section(names)`.
2. **App build.** After the robot enters: await `robot.list_emotions()` on the loop into `emotions: tuple[str, ...] | None`, appending `Robot: emotions unavailable: …` on failure. `_seeded_wica_config(config.wica, emotions)` resolves the persona via `resolve_system_prompt(config.wica.agent)` (called inside the existing `ConfigError` degrade), appends the section when there are names, and returns `replace(config.wica, agent=replace(agent, system_prompt=…, system_prompt_file=None))` for `Wica.init`. Store `emotions` on `AppSystem` (also in `_degraded_system`).
3. **App wire.** `for command in RobotControl(system.api, emotions=system.emotions).commands(): wica.register_command(command)`.
4. **Persona.** Reword the Emotions bullet.
5. **Specs + map.** Update robot-control.md (command set, the new section, testing, open question 1), app.md (commands list, persona, build step, wire step, degrade paragraph, open question 2), _overview.md and AGENTS.md rows. Set robot-control.md to `Updated` while the code lags, back to `Implemented` when this plan is `Done`.
6. **Tests.** As scoped above.

## Verification

- `uv run pytest tests/test_robot_control.py tests/test_app.py tests/test_app_ui.py tests/test_project_map.py` — the new tests assert: the agent's `system_prompt` contains the fake's names and the persona; `play_emotion`'s bound tool description names them; no `list_emotions` Command is registered; a failing fetch lands in `missing` as a `Robot:` line with the agent still up and `play_emotion` still registered; a robot-less build has no names in the prompt.
- `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all clean. Then mark this plan `Done` (here and in [_index.md](_index.md)) and flip robot-control.md back to `Implemented`.
