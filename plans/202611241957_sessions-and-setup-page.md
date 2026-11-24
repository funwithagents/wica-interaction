# Sessions that start and stop again, the Setup and Secrets tabs

**Status:** In progress

Implements [specs/session.md](../specs/session.md) (all of it) and the session, page and entry-point parts of [specs/app.md](../specs/app.md) ("What the user sees", "Wiring and lifecycle", "Degraded start"), with the command-line flags of [specs/project.md](../specs/project.md) ("Entry points"). It delivers the page's four tabs, Start and Stop, the config locked while a session runs, and autostart. It builds on [202611241956_config-on-app-setup.md](202611241956_config-on-app-setup.md) (the folder, the sections, the loaders), which must be `Done` first. It leaves out: one app for both embodiments, preparing the optional items at Start, a browser test of the page.

## Scope

- `src/wica_interaction/session.py` — new: `Status`, `Running`, `Session`
- `src/wica_interaction/system.py` — `open_session`, `close_session` (was `_shutdown`, now joining and closing the loop), the loop's thread kept on `AppSystem`; `run_app` with the session, the lock, autostart and `--host` / `--port` / `--start` / `--no-start`; `_serve`, `_degraded_system` and the `build_ui` import removed
- `src/wica_interaction/app_ui.py` — `build_page(model, session, *, open_on)`: the tabs, the session bar, the session's surfaces in a `gr.render`; `build_ui` removed, its layout kept as the render's body; `session_status_text`, `session_buttons`
- `tests/test_session.py` — new; `tests/test_system.py`, `tests/test_app_ui.py`, `tests/fakes.py` — follow
- `tests-e2e/conftest.py` — `close_session` in place of `_shutdown`
- `README.md`, `docs/getting-started.md`, `docs/index.md`, `docs/reference/configuration.md`, `docs/reference/install.md`, `docs/guides/computer.md`, `docs/guides/reachy-mini.md`, `docs/guides/personas.md`, `docs/guides/troubleshooting.md` — the Setup and Secrets tabs, Start and Stop, autostart
- `AGENTS.md` — the project map (`session.py`; `system.py` and `app_ui.py` reworded)
- `specs/testing.md` — the lifecycle functions' new names (editorial)
- `specs/_index.md`, `plans/_index.md` — statuses

## Steps

1. **A session leaves nothing behind.** `build_system` keeps its loop's thread (not a daemon it forgets); `close_session(system)` is `_shutdown` plus: stop the loop, join the thread (bounded by the teardown timeout), close the loop. Test first, over the fakes: after `close_session`, the loop is closed and `threading.enumerate()` is what it was before `build_system`. Fix what the test finds in this repo's own code (the webcam thread, the gesture stages, the latency collector's forwarding); what it finds in a sibling is reported there and noted in the pull request.
2. **`open_session(used_config, folder, *, load, embodiment, …)`** in `system.py`: load, construct, `build_system`, `TranscriptLog` and `ReactionLog` over `entry_display(system)` (moved out of the page), the latency collector's `describe` hook (moved with them), `wire`, `_start`; on any raise after `build_system` returned, `close_session` then re-raise. Returns `Running`. The test seams `build_system` already takes (`asr_engine_factory`, `gesture_models`) pass through.
3. **`Session`** as the spec: a lock around the status changes, the build and the teardown outside it; `start(model)` reading `saved_config`, refusing on `blockers`, building from `used_config` and `model.path`; `stop()`; `locked()`; `reasons`; `version`. `tests/test_session.py` as the spec's "Testing", the ten-cycle test included (one typed line answered through the fake voice per cycle; threads compared before and after).
4. **The page.** `build_page(model, session, open_on=…)`:
   - `gr.Blocks(title=model.app.title)`, the heading, `gr.Tabs(selected=open_on)` with Interaction, Latency, Setup, Secrets (ids `interaction`, `latency`, `setup`, `secrets`);
   - the session bar on Interaction: status line, Start, Stop, the reasons, the unsaved-changes notice (wired to `setup.draft.change` and `setup.saved.change`); Start and Stop grey both buttons while they run, then refresh; a 1 s timer refreshes the bar and a `gr.State` holding `session.version`;
   - `gr.render(inputs=version)` for the session's surfaces. The Interaction tab's body and the Latency tab's body are two renders over the same version, each drawing today's layout from `session.current`, or the one-line "No session is running" note. The latency detail's "read only while its tab is open" keeps working across the two renders (the open-tab state lives outside them);
   - `setup = setup_panel(model)` and `secrets_panel(model, setup.draft)` in their tabs.
   Check in a browser that a render's timers stop when it is redrawn (no tick against a stopped session's objects), and guard the tick functions on the session they were drawn for.
5. **`run_app`** as [specs/app.md](../specs/app.md) step 0: the flags, `Session(open_session bound to this embodiment)`, `AppModel(..., locked=session.locked)`, `ensure_persona`, `starts_at_launch` → `session.start(model)` (reasons logged at warning), `build_page(...).launch(server_name=…, server_port=…)` inside `try` / `finally: session.stop()`. Remove `_serve` and `_degraded_system`: a config that does not load is a reason on the session bar.
6. **The banner's words.** "Some parts could not start" stays for what building found; its advice changes from "set the missing variables and restart" to "see the Setup tab, then Stop and Start".
7. **Tests.** `tests/test_system.py`: `open_session`'s failure paths (a raise in the presenters, in `wire`, in `_start` each exits the body and closes the loop); `run_app` with the launch stubbed (a new folder; `--no-start` builds nothing; a blocker at launch leaves the page up with its reason; a session started at launch stopped when serving returns or raises). `tests/test_app_ui.py`: the bar's text and button states per status and with reasons; `build_page` builds over a stopped and a running session.
8. **By hand, both apps.** A new folder with no key: the page opens on Interaction with the missing key as the reason; set it on the Setup tab's key line; Start; talk; Stop; change the voice on the Setup tab (its extra installed from the page, its model prepared), Save refused while running and accepted once stopped; Start again; ten Start/Stop cycles on the simulator watching for a leftover `sim_daemon`, an unreleased microphone or camera, a port still bound. `--no-start`, then the checkbox off and a relaunch.
9. **Docs.** Getting started becomes: install, launch, set the key and Start from the page. The configuration reference keeps the fields and says each is edited on the Setup tab or in `config.json`; the install reference says extras can be added from the page; troubleshooting gains an entry per kind of blocker (a problem in the config, a missing extra, a missing key, a file not found) and one for a first start that downloads models.
10. **Statuses.** [specs/session.md](../specs/session.md), [specs/app.md](../specs/app.md), [specs/project.md](../specs/project.md) → `Implemented`; this plan → `Done`.

## Verification

- `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` (the ten-cycle test in the fast tier).
- `uv run pytest tests-e2e -rs` with the keys and the audio cable at hand.
- The by-hand run of step 8 on the computer and on the robot's app against the simulator; on the real robot before the next release.
- Before merging: the siblings' sources on `rev = "main"`, as in the first plan.
