# Lifecycle & teardown hardening

**Status:** Done

> **As built — two deviations from the steps below.** (a) Step 4: `can_reason`/`can_listen` were
> **dropped** rather than used — pyright cannot narrow `system.wica`/`system.bridge` through a
> property, so gating on them would have needed a redundant `is not None` beside every use (the
> analysis item offered "use them or drop them"). (b) Step 3: the `try: _start; launch finally:
> _shutdown` block is extracted as `_serve(system)` (called by `main()`), so
> `test_start_failure_still_tears_down_robot` drives the real code path instead of a copy of it. An
> extra `test_shutdown_bounds_a_hung_step` pins the per-step timeout.

Implements the settled lifecycle in `specs/app.md` ("Wiring and lifecycle", "No credentials, no
robot"). Fixes the three teardown/degrade correctness bugs from `specs/_analysis.md` (items 1, 2, 5)
so a startup failure, a bad `system_prompt_file`, or a hanging shutdown step can no longer leak the
robot session or the loop thread — and removes the `_shutdown`-vs-`AsrBridge` internal coupling and
the dead `can_listen`/`can_reason` surface flagged under "Bad patterns". It does **not** touch the
ASR mic pump (see the mic-source plan) or the input funnel semantics (see the empty-final plan).

## Scope

- `src/reachy_mini_interaction_wica/app.py` — fold `_start` + `.launch()` inside the `try/finally`
  that runs `_shutdown` (bug 1); catch `ConfigError` (not just `MissingEnvError`) around `Wica.init`
  (bug 2); give `_shutdown` per-step timeouts + failure isolation, and gate the bridge stop on
  `bridge.status()["running"]` so teardown no longer depends on `ASREngine.stop()` internals (bug 5 +
  the "`_shutdown` depends on a sibling's internals" pattern); have `_start`/`_shutdown` use the
  `can_reason`/`can_listen` properties rather than raw `is not None`.
- `tests/test_app.py` — tests for: a raising `_start` still tears the robot down; a bad
  `system_prompt_file` degrades to a banner instead of crashing; a failing early `_shutdown` step
  still runs the later ones; a never-started bridge is skipped at teardown (its engine's `stop` is not
  called).
- `specs/app.md` — tighten the "Wiring and lifecycle" teardown wording to state the isolation +
  timeout guarantee, the running-guard on the bridge stop, and that `_start`/launch are inside the
  teardown `try`.

> **Sibling consistency.** `../vocal-interaction-wica` already ships this teardown shape as `_stop`
> (bounded bridge stop guarded on `bridge.status()["running"]`, then `wica.close()`, then loop stop,
> const `_STOP_TIMEOUT_S = 5.0`). Keep this repo's guard style and 5.0 timeout identical; the only
> deliberate difference is that this repo's `_shutdown` does more (it also `__aexit__`s the robot api)
> and keeps its name. Bug 1 (the `_start`-outside-`try` fix) and bug 2 (the `ConfigError` catch) apply
> to both repos — see `../vocal-interaction-wica/specs/_reachy-consistency-backlog.md`.

## Steps

1. **Bug 2 — degrade on a bad prompt file.** In `app.py`, import `ConfigError` from `wica.config`
   alongside `MissingEnvError`. In `build_system`, keep the `except MissingEnvError` arm first (its
   env-var-specific message) and add an `except ConfigError as exc: missing.append(f"Agent: {exc}")`
   arm **after** it (order matters — `MissingEnvError` subclasses `ConfigError`), before the existing
   `except ImportError`. Now a missing/unreadable `system_prompt_file` resolved inside `Wica.init`
   lands in the banner, matching the "opens even with keys unset and says what to fix" contract.

2. **Bug 5 — isolated, time-bounded `_shutdown`, running-guarded bridge stop.** Add a module constant
   `_SHUTDOWN_TIMEOUT_S = 5.0` (same value as the sibling's `_STOP_TIMEOUT_S`) and a small helper that
   schedules one coroutine on the loop and waits with that timeout, logging (via `logger.exception`)
   and swallowing any failure so the next step still runs:

   ```python
   def _run_on_loop(
       loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], *, step: str
   ) -> None:
       try:
           asyncio.run_coroutine_threadsafe(coro, loop).result(_SHUTDOWN_TIMEOUT_S)
       except Exception:  # noqa: BLE001 - one teardown step must not abort the rest
           logger.exception("teardown step %r failed", step)
   ```

   Rewrite `_shutdown` so each step is independent, keeping the documented order (agent → ASR → api →
   loop): `wica.close()` in its own `try` (log + continue); then, **only when
   `bridge.status()["running"]`** (matching the sibling's guard — a bridge can exist without ever
   being started, and this removes the reliance on what `ASREngine.stop()` happens to do on a
   never-started engine), `_run_on_loop(loop, bridge.stop(), step="asr.stop")`; then
   `_run_on_loop(loop, api.__aexit__(None, None, None), step="api.exit")`; then always
   `loop.call_soon_threadsafe(loop.stop)`. Delete the old `_shutdown` comment that justified the
   unconditional bridge stop by appealing to `ASREngine.stop()` internals — the running-guard makes
   the contract local and identical to the sibling.

3. **Bug 1 — start inside the teardown `try`.** In `main()`, move `_start(system)` out of the
   `else:` branch and into the `try:` whose `finally:` calls `_shutdown`, immediately before
   `build_ui(system).launch()`. So the flow is: load config (degrade to `_degraded_system` on
   `ValueError`), then `build_system` in the `else`, then a single
   `try: _start(system); build_ui(system).launch() finally: _shutdown(system)`. Now a raise in
   `wica.start()` / `asr_bridge.start()` — after `build_system` has entered the robot session — still
   exits the media session and stops the loop.

4. **Use the properties.** In `_start` and `_shutdown`, gate on `system.can_reason` (agent built) and
   `system.can_listen` (bridge built) instead of `system.wica is not None` / `system.bridge is not
   None`, giving `can_listen` a real caller and `can_reason` a non-test one. Keep the `loop is not
   None` checks. (This resolves the "dead/test-only surface" item for these two properties.)

5. **Spec update.** In `specs/app.md`, update the last "Wiring and lifecycle" bullet to say teardown
   runs each step in isolation under a timeout so one hung/failed step cannot strand the robot session
   or the loop, that the bridge stop is guarded on `status()["running"]`, and that `_start` + launch
   sit inside the teardown `try`. app.md is `Implemented`; these edits describe new code, so set it to
   `Updated` when you edit the spec text and flip back to `Implemented` once this plan is `Done` (per
   AGENTS.md). No `asr-bridge.md` change is needed (the running-guard lives entirely in `app.py`).

## Verification

- New `tests/test_app.py` cases:
  - `test_start_failure_still_tears_down_robot`: inject a `FakeReachyMiniApi` whose session is
    entered, and a WICA/bridge whose `_start` raises (e.g. a `FakeASREngine.start` that raises);
    drive the `main`-shaped `try: _start finally: _shutdown` and assert `fake_api.exited` and the loop
    stopped.
  - `test_bad_system_prompt_file_degrades`: a config whose `wica.system_prompt_file` points at a
    missing file builds a system with an `Agent:` entry in `missing` and `can_reason is False`, no
    exception.
  - `test_shutdown_isolates_a_failing_step`: a fake whose `bridge.stop()` raises still results in
    `fake_api.exited` and a stopped loop.
  - `test_shutdown_skips_never_started_bridge`: build with fakes but do **not** call `_start`; call
    `_shutdown` and assert the fake engine's `stop` was never called (the `status()["running"]` guard
    held) and it did not raise.
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. Mark this plan `Done` (here and
  in [_index.md](_index.md)) and flip `specs/app.md` back to `Implemented` only once all three pass.
