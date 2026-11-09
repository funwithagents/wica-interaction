---
code:
  - tests/conftest.py
  - tests/fakes.py
  - tests-e2e/conftest.py
  - tests-e2e/support.py
tests:
---

# Testing

**Status:** Implemented

## Purpose

WICA Interaction's testing strategy — the two-tier structure and what a good test looks like. It's a **cross-cutting practice**, not a runtime concept: nothing here ships in the library. It exists as a spec so the decisions have one honest home that stays in sync with the setup, rather than living half in [project.md](project.md) (the tooling choices) and half in [AGENTS.md](../AGENTS.md) (the operational how-to). The concrete shell commands to run each tier live in [AGENTS.md](../AGENTS.md) "Testing".

## Two tiers, physically separated

Tests split into two directories, and the split is structural — a directory boundary, not a marker or an opt-out flag:

| Tier | Directory | Network | Deterministic | Runs by default |
|---|---|---|---|---|
| Unit / integration | `tests/` | never | yes | **yes** |
| Live / e2e | `tests-e2e/` | real service | no | **no** |

- **`tests/` is the normal dev loop.** Fast, deterministic, no real network, no credentials, no audio device, no camera, no robot. `pyproject.toml`'s `testpaths = ["tests"]` points the default `uv run pytest` here, so this is what runs on every change and what any contributor or CI can run with zero credentials.
- **`tests-e2e/` is opt-in.** It calls a real external service — network, credentials, non-deterministic output — so it is deliberately *not* collected by the default run. Because `testpaths` already excludes it, no pytest marker or `--run-e2e` flag is needed: the physical separation is the whole mechanism. Run it explicitly (`uv run pytest tests-e2e`).

## Layout mirrors the package

The `tests/` tier mirrors `src/wica_interaction/`: `test_<module>.py` for each shared module (`test_config.py`, `test_asr_bridge.py`, `test_gesture_input.py`, `test_system.py`, `test_app_ui.py`), plus `test_project_map.py` (the drift guard) and `fakes.py` (below); `tests/computer/` and `tests/reachy/` mirror the embodiment subpackages (`test_embodiment.py`, `test_config.py`, and the embodiment's own modules — `test_audio_processing.py`, `test_robot_control.py`). The subfolders are packages (`__init__.py`) so same-named files in two folders do not collide under pytest's default import mode. `tests-e2e/` is organized around live scenarios rather than modules.

**Fakes are shared, not duplicated.** `tests/fakes.py` holds the doubles more than one test module needs: `FakeASREngine` (the slice of `ASREngine` the bridge and the lifecycle touch) with `asr_factory(engine)`, the `asr_engine_factory` seam that hands it to `build_system` and records the `audio_source` the lifecycle built it over, `FakeEmbodiment` (the whole [Embodiment](embodiment.md) protocol, recording what the lifecycle asked of it — [embodiment.md](embodiment.md), "Testing"), and a scripted-provider `WicaConfig` builder. Embodiment-specific doubles (`FakeTTSEngine`, `FakeReachyMiniBridge`, a fake `OpenCVSource`) live next to the tests that use them, under their subfolder.

**The lifecycle is tested once, over the fake embodiment.** `tests/test_system.py` drives `build_system` / `wire` / `_start` / `_shutdown` with a `FakeEmbodiment` and WICA's scripted `fake` provider, so it knows nothing about either real body; each embodiment's `tests/<embodiment>/test_embodiment.py` pins that the real implementation honours each protocol member against its own fakes. No test builds a real body.

## What a good test asserts

- **Functional, not tautological.** Exercise what a feature actually does — inputs → outputs, state changes, side effects — not that it runs or matches its own signature. A test that would pass against a broken implementation (asserting a constant, that an object isn't `None`, that a mock was called) isn't worth writing.
- **Drive the public API like a real caller.** Prefer exercising the public surface the way a consumer would over reaching into internals; assert on the observable result.
- **In the e2e tier, assert on behavior, not exact output.** Real service responses vary run to run, so a live test asserts a robust property ("a non-empty result came back", "the side effect happened"), never a specific string.

## Test isolation

If the package holds process-global or singleton state, both tiers carry an identical autouse fixture (in each tier's `conftest.py`) that resets it before and after every test, so no state — or background timers/threads — leaks across tests. The fixture is duplicated rather than shared because `tests-e2e/` isn't a package that imports from `tests/`, and it's only a few lines.

Resources a test *creates* are its own to release. In particular, every lifecycle test that calls `build_system` ends — in a `finally` — with `_shutdown(system)`, the same teardown `run_app` runs, so no loop thread or media session outlives the test that opened it.

## Live tier: skip without credentials

A live test needs real credentials, and it must **skip — never fail** — when they're absent, so you exercise only the services you hold keys for and a contributor (or CI) with none is never broken. `tests-e2e/support.require_env(NAME)` implements this: it returns the env var or calls `pytest.skip(...)` when it's unset. Credentials come from the environment, never committed. Today the tier holds one scenario, the gesture input over the real vision models ([gesture-input.md](gesture-input.md)), skipped when the models are unavailable; the whole-loop live tests the embodiment specs defer (a robot actually speaking and moving; a real mic + speakers not self-triggering) are its intended next occupants.

## Tooling

- **`pytest`** is the runner; **`ruff`** lints/formats; **`pyright`** (`standard` mode) type-checks. All three are the gate after any change — lint (`ruff check`), format (`ruff format`), type check, and tests must pass before work is considered done ([AGENTS.md](../AGENTS.md), "Verification").
- **`pyright` covers test code too:** its `include` is `src`, `tests`, and `tests-e2e`, so tests are type-checked alongside the library rather than being a blind spot.

## Open questions

1. **The live tier in CI.** The static gate and the fast tier run on GitHub's hosted runners ([ci.md](ci.md)); the live tier's jobs come with the end-to-end work.
