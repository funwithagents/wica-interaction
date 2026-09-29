# Catch up with the bridge's `ReachyMiniBridge` rename and its `start()` / `stop()` lifecycle

**Status:** Done

Implements the re-worded behavior in `specs/reachy-embodiment.md` ("`ReachyEmbodiment`" — enter step 1 and `exit()`; "`RobotCameraFeed`"; "Testing") and `specs/embodiment.md` (the `enter()` / `exit()` rows of the member table). One sibling change of 2026-09-29 motivates it: `reachy-mini-bridge` renamed `ReachyMiniApi` to `ReachyMiniBridge` (module `api.py` → `bridge.py`, spec `specs/core/api.md` → `specs/core/bridge.md`) and made its lifecycle the explicit async pair `start()` / `stop()` plus a `running` property, `async with` staying as sugar over the pair — the pair being the form meant for "a host with lifecycle hooks of its own", which this app's `Embodiment.enter` / `exit` are. Today `reachy_mini_bridge.api` no longer imports, so the reachy subpackage and its tests fail to load. No behaviour changes: the embodiment drives the pair instead of the dunders, and every `api` becomes a `bridge`. Deliberately leaves out any use of `bridge.running` in the app's own health reporting (nothing in the lifecycle asks the body whether it is up between `enter` and `exit`).

## Scope

- `src/wica_interaction/reachy/embodiment.py` — import `ReachyMiniBridge` from the package front door (`from reachy_mini_bridge import CameraFrame, ReachyMiniBridge`); the `api` test seam and every `api` name become `bridge`; `enter()` awaits `bridge.start()`, `exit()` awaits `bridge.stop()`; docstrings say the pair is what a host with its own hooks drives
- `src/wica_interaction/reachy/robot_control.py` — the `TYPE_CHECKING` import from the front door; `RobotControl(bridge, …)`; docstrings
- `tests/reachy/fakes.py` — `FakeReachyMiniApi` → `FakeReachyMiniBridge` with `start()` / `stop()` / `running` in place of the dunders; `raise_on_enter` → `raise_on_start`; `entered` / `exited` → `started` / `stopped`
- `tests/reachy/test_embodiment.py` — the renames; the enter test also asserts `running`, the exit test `not running`
- `tests/reachy/test_robot_control.py` — a real `ReachyMiniBridge("fake")` driven through `start()` / `stop()`
- `specs/reachy-embodiment.md`, `specs/embodiment.md` — the lifecycle wording (`start()` / `stop()`, the pair versus the sugar, a failed `start()` already unwound), the `bridge.md` link, every `api` → `bridge`
- `specs/_index.md`, `specs/_overview.md`, `specs/gesture-input.md`, `specs/testing.md`, `README.md`, `AGENTS.md` — the name in prose (`ReachyMiniBridge`, `bridge.camera`, `FakeReachyMiniBridge`)

## Steps

1. **Code.** Rename `api` → `bridge` in the two reachy modules, import from the front door, and swap `__aenter__()` / `__aexit__(None, None, None)` for `start()` / `stop()` in `enter()` / `exit()`.
2. **Tests.** Give the fake the same `start()` / `stop()` / `running` shape and rename its flags; drive the real `fake` bridge through the pair in the robot-control tests.
3. **Specs and docs** per Scope; the historical plan rows in `plans/_index.md` keep the old name (they describe what was built then).
4. **Verification** (below); `reachy-embodiment.md` and `embodiment.md` stay `Implemented` at the end (the code matches them again); this plan `Done` here and in `_index.md`.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright` and `uv run pytest` all pass — 250 tests (2026-09-29): `tests/reachy/` loads again against the renamed bridge, `test_enter_brings_the_robot_up_and_wires_every_member` sees the fake `running`, `test_exit_stops_the_bridge` sees it stopped, and the robot-control tests run over a really started `ReachyMiniBridge("fake")`. `tests/test_project_map.py` still passes (no file moved). Not run here: a `sim` / `real` session (the bridge's own tests cover the pair on those backends).
