# People awareness on the Reachy Mini

**Status:** Done

Implements the settled behaviour in [specs/embodiment.md](../specs/embodiment.md) ("`inputs()` is the mirror of `commands()`"), [specs/app.md](../specs/app.md) (build step 5b, `_start` / `_shutdown`, the one standing entry, `entry_display`), [specs/reachy-embodiment.md](../specs/reachy-embodiment.md) ("People: `PeopleInput`", the tracking Commands, the persona, the template) and [specs/e2e.md](../specs/e2e.md) (scenarios 4 and 5). The robot tells the agent who arrives, leaves, comes very close and is present, and the agent starts, pins and stops head tracking. It builds on reachy-mini-bridge `d2516aa` (`bridge.users`, pinned tracking), already pinned on this branch. It deliberately leaves out the People line on the page (reachy-embodiment open question 7), recognition (8) and any arrival gate (6).

## Scope

- `src/wica_interaction/embodiment.py` — `Deliver`, `InputEntry`, the `Input` protocol, `Embodiment.inputs()`.
- `src/wica_interaction/system.py` — build step 5b (register each input's entries, a guarded per-input `deliver`, a banner line on a refused entry), `AppSystem.inputs` / `input_displays`, the inputs started in `_start` and stopped in `_shutdown` right after the agent.
- `src/wica_interaction/app_ui.py` — `entry_display(system)`, the embodiment's `display_fn`s in front of `display_entry`, handed to both presenters.
- `src/wica_interaction/computer/embodiment.py` — `inputs()` returning `()`.
- `src/wica_interaction/reachy/people.py` — **new**: `PeopleInput` (the two entries, the start-up snapshot, the two watchers, the standing refresh, event joining, the personal-space cooldown), the wordings, `people_prompt_section()`.
- `src/wica_interaction/reachy/robot_control.py` — `tracking=` and the `start_tracking` / `stop_tracking` Commands.
- `src/wica_interaction/reachy/embodiment.py` — `enter()` step 5 (detection on, `PeopleInput` built, a `People: …` degrade), `inputs()`, `commands()` with tracking, the people section in `prompt_addendum`.
- `prompts/reachy.md` — following people is the agent's, no longer "on its own".
- `config.reachy.example.json` — tracking off at start (detection stays on).
- `tests/fakes.py` — `ScriptedInput`; `FakeEmbodiment(inputs=…)`.
- `tests/test_system.py`, `tests/test_app_ui.py` — the lifecycle of an embodiment's inputs; `entry_display`.
- `tests/reachy/test_people.py` — **new**: `PeopleInput` over the real bridge on `fake` with a scripted `FaceDetector`; the wordings.
- `tests/reachy/test_robot_control.py`, `tests/reachy/test_embodiment.py`, `tests/reachy/fakes.py`, `tests/reachy/test_config.py` — the tracking Commands; enter with a detector; the template's tracking off.
- `tests-e2e/` — `person.py` (the stage's portrait, hand and portrait independent), `scenarios.py` (`people`, `a_move_keeps_the_person`), `test_reachy.py`, `conftest.py` (the input keys, `entry_display`), `configs/reachy.json` (detection on, tracking off).
- `.github/workflows/ci.yml` — `props:face` among the robot job's required capabilities.
- Docs: `docs/reference/agent.md` (the entries, the Commands, the persona section, the transcript), `docs/reference/configuration.md` (the template's `motion.tracking`), `docs/guides/reachy-mini.md` (face detection and tracking, the people), `docs/guides/personas.md`, `docs/guides/limits.md`; `AGENTS.md` (the project map's `reachy/people.py`).

## Steps

1. **The protocol.** Add `Deliver`, `InputEntry` (frozen dataclass) and `Input` to `embodiment.py`, and `inputs()` to `Embodiment`. The computer returns `()`. `FakeEmbodiment` takes `inputs=` (default `()`); `ScriptedInput` holds its entries, records `start` / `stop` and keeps the `deliver` it was handed, and can be told to raise on start.
2. **The lifecycle.** In `build_system`, after `speech_input`, only with a body and an agent: register each input's entries (a `ValueError` unregisters what that input already registered, adds `<display_name>: <why>` to `missing` and drops it), build its `deliver` (its own keys only, guarded on `wica.is_running`), collect the `display_fn`s. `AppSystem` gains `inputs: tuple[tuple[Input, Deliver], ...]` and `input_displays: Mapping[str, Callable[[Any], str]]` (empty in `_degraded_system`). `_start` starts each after the gesture input (a raise logged); `_shutdown` stops each right after `wica.close()`, through `_run_on_loop`.
3. **The transcript.** `entry_display(system)` returns a hook trying `system.input_displays[entry.key]` (an `EntryDisplay` of its string) before `display_entry`; `build_ui` hands it to both presenters.
4. **The people.** `reachy/people.py`: the wordings as pure functions (`where`, `about`, the event lines, the standing text from a `UsersReport` and a `HeadTrackingReport`); `PeopleInput` with the constants `PERSON_EVENT_TTL`, `EVENT_JOIN_S`, `PERSONAL_SPACE_COOLDOWN_S`, `STANDING_REFRESH_S`; `start` snapshots, delivers, then spawns the users watcher, the tracking watcher and the refresher; `stop` cancels and awaits them; `people_prompt_section()`.
5. **The Commands.** `RobotControl(…, tracking=False)`; `start_tracking(person=None)` / `stop_tracking()` over `start_head_tracking(track_id=…)` / `stop_head_tracking()`, non-raising with the spec's replies.
6. **The embodiment.** `enter()` step 5 when `face_detection.detector` is set: `set_face_detection(True)` when the config left it off (logged), `PeopleInput(bridge)`; any failure → `People: …`. `inputs()`, `commands()` (tracking when the people are up), `prompt_addendum` (+ the people section).
7. **Persona and template.** `prompts/reachy.md`'s movement paragraph; `config.reachy.example.json` `motion.tracking.enabled: false`.
8. **Fast tests.** `tests/test_system.py`: a `ScriptedInput` delivery reaches the prompt and wakes the agent per its entry; an undeclared key dropped; before start / after teardown dropped; a duplicate key a banner line; a raising start leaves the app up; no body or no agent ⇒ nothing registered or started; the teardown order with the input's stop. `tests/test_app_ui.py`: `entry_display`. `tests/reachy/test_people.py`: the spec's list over the `fake` bridge with a scripted detector. `tests/reachy/test_robot_control.py`: the tracking pair. `tests/reachy/test_embodiment.py`: enter with a detector (detection on, the input, the five Commands, the section), and without.
9. **The end-to-end tier.** The stage's portrait (`arrive`, `step_to`, `leave`) beside its hand; the two scenarios; the input keys; the robot config; the CI's required capabilities.
10. **Docs and map.** The pages in Scope; AGENTS.md's project map row for `reachy/people.py`.
11. **Statuses.** Once verified: this plan `Done`; embodiment, app, reachy-embodiment back to `Implemented`; e2e `Implemented` once its scenarios have run green in CI (else left `Updated`, the reason in the PR).

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` (the fast tier, the new tests included, `test_project_map.py` and `test_docs.py` covering the map, the frontmatter and the docs). The end-to-end tier's robot job, dispatched on the branch (`gh workflow run ci.yml --ref people-awareness`), runs scenarios 4 and 5 in the headless sim; the computer's job checks nothing regressed there.
