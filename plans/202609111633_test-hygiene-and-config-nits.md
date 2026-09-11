# Test hygiene & config nits

**Status:** Done

> **As built.** Every `build_system` test now starts via `_start` and ends with `_shutdown` in a
> `finally`. The empty-input test's positive control asserts on the transcript's user turns (not just
> `said`, which a scripted reply to an erroneous empty trigger would also satisfy). `_prompt_captured`
> reads the public Prompt-selector update; the ASR-degrade test gained that assertion too (it used to
> assert only "no exception"). For step 4 the existing `test_invalid_reachy_backend_raises` already
> covered the runtime `ValueError`, so its `match` was tightened to the full allowed set (proving the
> `get_args(Backend)` derivation) rather than adding a duplicate; `tests/test_app.py`'s `_config` is
> typed with `Backend`. `test_main_requires_a_config_path` pins step 5 (exit code 2, `--config` in
> stderr, nothing built). The `uv lock` refresh also picked up new upstream Windows wheels for the
> already-locked `gstreamer-*` 1.28.7 packages (uploaded 2026-09-11) — index churn, no version change.

Cleans up the remaining `specs/_analysis.md` items that don't belong to a behavior plan: the
test-isolation leaks and the flaky timing assertion under "Bad patterns", and four "Nits" — the
private-state assertions, the `ReachyConfig.backend` `Literal`, requiring `--config` (per the decision
to never silently target a real robot), and honestly documenting the empty `tests-e2e/` tier. It also
folds in two housekeeping fixes the sibling `../vocal-interaction-wica` already shipped (its
`202609111500_spec-consistency-code-fixes.md`) but that this repo still lags: declaring the
directly-imported `langchain-core` dependency, and dropping the `uv init` `hello()` scaffold from
`__init__.py`. Depends on the lifecycle plan (`202609111629`) for a robust `_shutdown`, since the leak
fix routes every `build_system` test through it. Pure hygiene + safety; no runtime feature changes.

## Scope

- `tests/test_app.py`:
  - end every `build_system` test via `_shutdown(system)` in a `finally` (replacing the bare
    `wica.close()` and the hand-rolled `api.__aexit__`), so no loop thread or robot media session
    leaks across the suite (the testing spec's isolation goal);
  - replace `test_empty_typed_text_is_ignored`'s `time.sleep(0.2)` absence-check with a positive
    control + ordering assertion;
  - stop reaching into `system.panels._prompts`; assert on the public surface instead.
- `src/reachy_mini_interaction_wica/config.py` — type `ReachyConfig.backend` as
  `Literal["real", "sim", "fake"]` and derive `_REACHY_BACKENDS` from it.
- `src/reachy_mini_interaction_wica/app.py` — remove `_DEFAULT_CONFIG`; make `--config` required and
  exit with a clear message when it is absent (no silent real-robot default).
- `src/reachy_mini_interaction_wica/__init__.py` — drop the `uv init` `hello()` scaffold, leaving a
  one-line module docstring (mirrors the sibling's step 6).
- `pyproject.toml` (+ `uv.lock`) — declare `langchain-core` in `[project].dependencies` (imported
  directly in `app_ui.py`, today only transitive via `wica`); refresh the lock (mirrors the sibling's
  step 5, minus `numpy` — this repo dropped local audio-processing, so it imports no `numpy`).
- `tests/test_config.py` — a test that an unknown backend still raises the clear `ValueError`
  (the `Literal` is compile-time; runtime validation stays).
- `specs/configuration.md` — note the `Literal` typing; `specs/app.md` — note `--config` is required.
- `specs/testing.md` — record that `tests-e2e/` is scaffolding-only today (no live scenarios yet).

## Steps

1. **Route tests through `_shutdown`.** In `tests/test_app.py`, import is already present; change each
   `build_system`-based test to `try: … finally: _shutdown(system)`. Remove the per-test
   `system.wica.close()` and the hand-rolled `asyncio.run_coroutine_threadsafe(api.__aexit__…)` in
   `test_missing_tts_key_degrades_only_the_voice` — `_shutdown` now does both (agent close, bridge
   stop, api exit, loop stop) in isolation. Keep `test_shutdown_stops_asr_before_the_robot_exits`
   as-is (it asserts the teardown order directly). This makes the shared fake `log` unnecessary for
   the other tests but leaves it for the ordering test.

2. **Positive control instead of a sleep.** Rewrite `test_empty_typed_text_is_ignored` so it submits
   the empties **then** a real turn through the same funnel, waits (`_wait_until`) for the real turn's
   `say` to reach the fake robot, and asserts `fake_api.said == ["<real reply>"]` — i.e. the empties
   produced nothing *before* the real reply landed. This proves the ignore behavior by ordering
   against a real event rather than sleeping on an absence, so it is neither flaky nor slow. (Use a
   two-step script: the empties must not consume a scripted turn — submit them first, confirm no
   `say`, then submit the real text; keep the script minimal.)

3. **Public-surface assertions.** Replace `assert _wait_until(lambda: len(system.panels._prompts) > 0)`
   in `test_robot_connect_failure_degrades_but_agent_still_reasons` (and the TTS-degrade test if it
   lands here after the UI plan) with a wait on the public view: call `system.panels.tick_conversation()`
   and assert the returned prompt-selector update carries at least one choice (a step's prompt was
   captured). This drives the same observable the UI shows instead of a private list. (`_on_segment`
   in the empty-final plan is left as-is — the bridge spec's Testing section documents it as the way
   to feed a segment, so it is not "private state" in the same sense.)

4. **`Literal` backend.** In `config.py`, `from typing import Literal`; declare
   `Backend = Literal["real", "sim", "fake"]`, type `ReachyConfig.backend: Backend = "real"`, and set
   `_REACHY_BACKENDS = ("real", "sim", "fake")` (or `get_args(Backend)`) so the runtime check and the
   type stay in one place. Callers that branch on the backend (banner/wiring) now type-check against
   the closed set. Keep the runtime `ValueError` for an unknown backend from a JSON file (the
   `Literal` cannot guard external input).

5. **Require `--config`.** In `app.py`, delete `_DEFAULT_CONFIG` and its use; make the argparse
   argument `required=True` (or, to keep a friendly message, default `None` and, when absent, print a
   one-line "pass --config <path>" message and exit non-zero before building anything). A bare
   `uv run reachy-mini-interaction-wica` no longer silently targets a real robot with the example
   voice id. Leave `config.example.json` as the documented sample to copy.

6. **Declare `langchain-core` (sibling parity).** `app_ui.py` imports `langchain_core.messages`
   directly but `pyproject.toml` only gets it transitively through `wica`. Add `"langchain-core"` to
   `[project].dependencies`, then `uv lock` + `uv sync --dev`. Commit `pyproject.toml` and `uv.lock`
   together. Do **not** add `numpy` (the sibling declares it for its local audio-processing; this repo
   dropped that in the bridge-integration and imports no `numpy` — confirm with
   `grep -rn "import numpy" src/`).

7. **Drop the `hello()` scaffold (sibling parity).** `src/reachy_mini_interaction_wica/__init__.py`
   still holds the `uv init` placeholder `hello()`; nothing imports it. Replace the file content with
   a one-line module docstring, e.g. `"""Reachy Mini Interaction WICA — talk to a WICA-driven Reachy
   Mini. See specs/_overview.md."""`. `tests/test_project_map.py` exempts `__init__.py` from the module
   map, so nothing else changes.

8. **Spec updates.** `specs/configuration.md`: note `ReachyConfig.backend` is a
   `Literal["real","sim","fake"]` (still validated at load from JSON). `specs/app.md` "Wiring and
   lifecycle": `main()` requires `--config` and exits with a message otherwise. `specs/testing.md`:
   add to "Live tier" (or its open question) that `tests-e2e/` currently holds only the shared
   scaffolding (`conftest.py`, `support.py`) and no live scenarios yet — the tier is wired and ready,
   the TTS-output/robot-control live tests are still deferred. Set each edited `Implemented` spec to
   `Updated` on the edit and back to `Implemented` once this plan is `Done`.

## Verification

- The full `tests/test_app.py` suite passes with every `build_system` test tearing down via
  `_shutdown`; running `uv run pytest -p no:cacheprovider` twice shows no thread/session-leak warnings
  and stable timing (the rewritten empty-input test no longer sleeps).
- `tests/test_config.py` still rejects an unknown `reachy.backend` with the clear `ValueError`;
  `pyright` now narrows backend branches against the `Literal`.
- Running the entry point with no `--config` exits with the guidance message (manually or via a small
  test that invokes `main`-argument parsing), never constructing a `real` robot.
- `git status` shows `uv.lock` changed together with `pyproject.toml` (the `langchain-core` add) and
  nothing unexpected; `grep -rn "import numpy" src/` returns nothing (numpy is correctly not added).
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. Mark `Done` and flip the edited
  specs back to `Implemented` only once all pass.
