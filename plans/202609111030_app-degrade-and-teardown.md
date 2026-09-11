# App degrade paths and teardown — TTS key, robot-less wording, ASR stop

**Status:** Done

Implements the settled behavior in [app.md](../specs/app.md) ("No credentials, no robot" and "One event loop") that the current `app.py` lags. Three gaps, three fixes:

1. **An unset TTS key must degrade only the voice.** Today `TTSEngineSynthesizer(config.tts_engine)` is built *inside* the robot `try` block in `build_system`, so an unset `ELEVENLABS_API_KEY` is reported as `"Robot: …"` and leaves `api` as `None` — which also removes the mic, the ASR bridge, and every robot action command. After this plan, the synthesizer is built in its own guarded step, the robot still comes up with `synthesizer=None`, and `say` returns a readable `"TTS error: …"`.
2. **The spec and the UI banner must say what actually happens without a robot.** Today both claim `say`/actions "report the robot as missing"; in fact, with no robot, `say` and the robot actions are simply **not registered**. The fix is wording only (decided: no robot-less stub commands).
3. **Teardown must stop the ASR bridge before the robot's media session closes.** Today `main()` closes WICA, `__aexit__`s the api, and stops the loop, but never calls `bridge.stop()` — which the spec lists.

Out of scope: any change to what the panels show, any new command, any change to `asr_bridge.py` / `tts_output.py` / `robot_control.py`.

> **How to work this plan.** Do the steps in order. Each step names the exact file, the exact text or code to find, and what to replace it with. After each code step, run `uv run ruff check . && uv run pyright && uv run pytest` — all three must stay green. Do not "improve" surrounding code; the diff should contain only what the steps describe.

## Background you need (verified facts, no need to re-check)

- `ReachyMiniApi.__init__(backend, *, synthesizer=None, …)` accepts `synthesizer=None`. Its `say()` then raises `reachy_mini_bridge.errors.BridgeError` with the message `say requires a SpeechSynthesizer: pass one, or configure a default synthesizer at construction (e.g. the tts extra's TTSEngineSynthesizer)` — and raises *before* touching the media session.
- `VoiceOutput.say` ([tts_output.py](../src/reachy_mini_interaction_wica/tts_output.py)) already catches any exception from `api.say` and returns `f"TTS error: {exc}"`. No change needed there.
- `TTSEngineSynthesizer(config)` (from `reachy_mini_bridge.audio`) builds the tts-engine module immediately. With the `elevenlabs` module and an unset env var it raises `tts_engine.config.ConfigError` (a plain `Exception` subclass) with the message `ElevenLabs module: environment variable 'WICA_TEST_UNSET_TTS_KEY' (named by 'api_key_env') is unset or empty`.
- `ReachyMiniApi("fake")` can be entered (`await api.__aenter__()`) and exited on an event loop with no hardware, no daemon, no network; `api.mic_sample_rate` is 16000 on the fake.
- The existing tests in [tests/test_app.py](../tests/test_app.py) pass `tts_engine=cast(TTSEngineConfig, None)` and inject `api=` — so the new code must **not** build a synthesizer when `api` is injected, or those tests break.
- `AsrBridge.stop()` awaits `engine.stop()`. The `FakeASREngine` in `tests/test_app.py` sets `self.started = False` in its `stop()`.

## Scope

- `src/reachy_mini_interaction_wica/app.py` — `build_system`: guarded synthesizer build (step 1); new `_shutdown(system)` helper that stops the bridge before exiting the api, called from `main` (step 3).
- `tests/test_app.py` — two new tests: TTS-key degrade (step 2) and shutdown order (step 4); the `FakeReachyMiniApi` / `FakeASREngine` doubles gain an optional shared event log.
- `src/reachy_mini_interaction_wica/app_ui.py` — banner wording only (step 5).
- `specs/app.md` — "No credentials, no robot" paragraph and the `main()` step list (step 6); status `Updated` → `Implemented` at the end (step 7).
- `specs/configuration.md` — one clause in "Secrets stay referenced, never stored" (step 6).
- `specs/_index.md`, `plans/_index.md` — status rows (step 7).

## Steps

### Step 1 — Build the synthesizer outside the robot `try` (`app.py`, `build_system`)

In [app.py](../src/reachy_mini_interaction_wica/app.py), inside `build_system`, find this block (currently right after `missing: list[str] = []`):

```python
    # --- The robot: one ReachyMiniApi behind say + the mic stream, entered on the app loop ------
    # Construction connects a real/sim robot and __aenter__ opens the media session; both can raise an
    # upstream reachy_mini exception, so wrap both in one broad except and degrade (api stays None).
    robot: ReachyMiniApi | None = None
    try:
        robot = (
            api
            if api is not None
            else ReachyMiniApi(
                config.reachy.backend, synthesizer=TTSEngineSynthesizer(config.tts_engine)
            )
        )
        asyncio.run_coroutine_threadsafe(robot.__aenter__(), loop).result()
    except Exception as exc:  # noqa: BLE001 - any construct/connect failure degrades, it never crashes
        missing.append(f"Robot: {exc}")
        robot = None
```

Replace it with:

```python
    # --- The voice's synthesizer: built on its own so an unset TTS key degrades only `say` ------
    # TTSEngineSynthesizer resolves the TTS module's key at construction and raises when it is
    # unset. That must not take the robot down with it (the mic, ASR, and actions need no TTS key),
    # so it is guarded separately and the robot is built with `synthesizer=None` on failure — the
    # bridge accepts that and `api.say` then raises a BridgeError, which VoiceOutput maps to a
    # readable "TTS error: …". Skipped when a test injects `api=` (the fake carries its own say).
    synthesizer: TTSEngineSynthesizer | None = None
    if api is None:
        try:
            synthesizer = TTSEngineSynthesizer(config.tts_engine)
        except Exception as exc:  # noqa: BLE001 - an unset/invalid TTS key degrades, it never crashes
            missing.append(f"TTS: {exc}")

    # --- The robot: one ReachyMiniApi behind say + the mic stream, entered on the app loop ------
    # Construction connects a real/sim robot and __aenter__ opens the media session; both can raise an
    # upstream reachy_mini exception, so wrap both in one broad except and degrade (api stays None).
    robot: ReachyMiniApi | None = None
    try:
        robot = (
            api
            if api is not None
            else ReachyMiniApi(config.reachy.backend, synthesizer=synthesizer)
        )
        asyncio.run_coroutine_threadsafe(robot.__aenter__(), loop).result()
    except Exception as exc:  # noqa: BLE001 - any construct/connect failure degrades, it never crashes
        missing.append(f"Robot: {exc}")
        robot = None
```

Also update the `build_system` docstring: find the sentence starting `a ``real``/``sim`` backend can raise at **construction**` and, just before it, insert: `The ``TTSEngineSynthesizer`` is built first, in its own guard (an unset TTS key appends a ``TTS:`` entry and the robot is built with no synthesizer, so only ``say`` degrades);`. Keep the rest of the docstring as is.

Run the gate. All 50 existing tests must still pass (they inject `api=`, so the new branch is skipped).

### Step 2 — Test the TTS-key degrade path (`tests/test_app.py`)

Append to [tests/test_app.py](../tests/test_app.py). This test uses the **real** `ReachyMiniApi("fake")` (no `api=` injected) so the real synthesizer guard runs, and a **real** `TTSEngineConfig` whose key env var is unset.

```python
import asyncio  # add to the imports at the top of the file if not already present


def _tts_degrade_config() -> AppConfig:
    """A real TTS config referencing an unset env var (so the synthesizer build raises), with a fake
    WICA agent and the bridge's `fake` robot backend — only the voice should degrade."""
    wica = WicaConfig.from_dict(
        {"agent": {"provider": "fake", "model": "scripted", "system_prompt": "double"}}
    )
    tts = TTSEngineConfig.from_dict(
        {"module": {"type": "elevenlabs", "api_key_env": "WICA_TEST_UNSET_TTS_KEY"}}
    )
    return AppConfig(
        wica=wica,
        tts_engine=tts,
        asr_engine=cast(ASREngineConfig, None),  # a fake engine is injected
        reachy=ReachyConfig(backend="fake"),
    )


def test_missing_tts_key_degrades_only_the_voice(monkeypatch: Any) -> None:
    """With the TTS key unset, the robot still comes up (mic/ASR/actions intact) and `say` reports a
    readable TTS error instead of the whole robot being reported missing."""
    monkeypatch.delenv("WICA_TEST_UNSET_TTS_KEY", raising=False)
    fake_asr = FakeASREngine()
    # No `api=` injected: build_system builds the real fake-backend api with a synthesizer of None.
    system = build_system(_tts_degrade_config(), asr_engine=cast(ASREngine, fake_asr))
    try:
        assert system.api is not None  # the robot is up …
        assert system.voice is not None  # … so the voice exists (say is registered) …
        assert system.bridge is not None  # … and so does the mic-fed ASR bridge
        # The failure is reported on the TTS axis, naming the env var — not as a robot failure.
        assert any(m.startswith("TTS:") and "WICA_TEST_UNSET_TTS_KEY" in m for m in system.missing)
        assert not any(m.startswith("Robot:") for m in system.missing)
        # say degrades to a readable string on the app loop (VoiceOutput maps the BridgeError).
        assert system.loop is not None
        result = asyncio.run_coroutine_threadsafe(system.voice.say("hi"), system.loop).result(2.0)
        assert result.startswith("TTS error:")
        # The robot action commands were registered (the fake honors motor state).
        assert system.wica is not None
    finally:
        if system.api is not None and system.loop is not None:
            asyncio.run_coroutine_threadsafe(
                system.api.__aexit__(None, None, None), system.loop
            ).result(2.0)
        if system.wica is not None:
            system.wica.close()
```

Run the gate. If pyright complains that `system.voice` may be `None` inside the `run_coroutine_threadsafe` call, the `assert system.voice is not None` above narrows it — keep the asserts in that order.

### Step 3 — Stop the ASR bridge at teardown (`app.py`, `main`)

In [app.py](../src/reachy_mini_interaction_wica/app.py), find the end of `main()`:

```python
    try:
        build_ui(system).launch()
    finally:
        if system.wica is not None:
            system.wica.close()
        if system.api is not None and system.loop is not None:
            # Close the media session + robot connection on the loop before the loop stops.
            asyncio.run_coroutine_threadsafe(system.api.__aexit__(None, None, None), system.loop).result()
        if system.loop is not None:
            system.loop.call_soon_threadsafe(system.loop.stop)
```

Replace with:

```python
    try:
        build_ui(system).launch()
    finally:
        _shutdown(system)
```

and add this function **above** `main()` (right after `_degraded_system`):

```python
def _shutdown(system: AppSystem) -> None:
    """Tear the system down in dependency order: the agent, then ASR capture, then the robot's
    media session, then the loop. The bridge is stopped *before* the api exits so the mic pump is
    cancelled while its stream is still valid, rather than iterating a closed media session."""
    if system.wica is not None:
        system.wica.close()
    if system.bridge is not None and system.loop is not None:
        asyncio.run_coroutine_threadsafe(system.bridge.stop(), system.loop).result()
    if system.api is not None and system.loop is not None:
        # Close the media session + robot connection on the loop before the loop stops.
        asyncio.run_coroutine_threadsafe(system.api.__aexit__(None, None, None), system.loop).result()
    if system.loop is not None:
        system.loop.call_soon_threadsafe(system.loop.stop)
```

Note `bridge.stop()` is called even if the bridge was never started (the agent was missing, so `_start` skipped it). That is safe: the real `ASREngine` builds its segmenter in `__init__` and the Deepgram module's `stop()` only sets an event, so `stop()` on a never-started engine raises nothing (verified). Keep the call unconditional.

Also update the `AppSystem` docstring: find `` ``main`` is what ``__aexit__``s the api at teardown. `` and replace with `` ``main`` tears down via ``_shutdown`` (agent, then ASR bridge, then the api's ``__aexit__``, then the loop). ``. And in the module docstring, find `` - ``main()`` — parse ``--config``, build, start, launch, and ``__aexit__`` the api at teardown. `` and replace with `` - ``main()`` — parse ``--config``, build, start, launch, and ``_shutdown`` at teardown (bridge stopped before the api exits). ``

Run the gate.

### Step 4 — Test the shutdown order (`tests/test_app.py`)

Give the two doubles a shared, ordered event log so the test can prove the bridge stopped *before* the api exited. In [tests/test_app.py](../tests/test_app.py):

1. Change `FakeReachyMiniApi.__init__` signature to `def __init__(self, *, raise_on_enter: str | None = None, log: list[str] | None = None) -> None:` and add `self._log = log` to its body. In `__aexit__`, before `self.exited = True`, add:
   ```python
           if self._log is not None:
               self._log.append("api.exit")
   ```
2. Change `FakeASREngine.__init__` signature to `def __init__(self, *, log: list[str] | None = None) -> None:` and add `self._log = log`. In `stop()`, before `self.started = False`, add:
   ```python
           if self._log is not None:
               self._log.append("asr.stop")
   ```
3. Change the import line `from reachy_mini_interaction_wica.app import build_system` to `from reachy_mini_interaction_wica.app import _shutdown, _start, build_system`.
4. Append the test:

```python
def test_shutdown_stops_asr_before_the_robot_exits() -> None:
    """Teardown stops the ASR bridge (cancelling the mic pump) *before* the robot's media session
    closes, then exits the api — the order specs/app.md lists."""
    log: list[str] = []
    fake_api = FakeReachyMiniApi(log=log)
    fake_asr = FakeASREngine(log=log)
    system = build_system(
        _config(script=[{"tool_calls": [{"name": "noop", "args": {}}]}]),
        api=cast(ReachyMiniApi, fake_api),
        asr_engine=cast(ASREngine, fake_asr),
    )
    _start(system)
    assert fake_asr.started  # capture was live before teardown

    _shutdown(system)

    assert not fake_asr.started
    assert fake_api.exited
    assert log == ["asr.stop", "api.exit"]
    assert system.wica is not None and not system.wica.is_running
```

Run the gate. The project's ruff config uses the default rule set, which does not flag importing the private `_shutdown` / `_start` helpers in tests — do not rename them.

### Step 5 — Reword the banner (`app_ui.py`, `build_ui`)

In [app_ui.py](../src/reachy_mini_interaction_wica/app_ui.py), find:

```python
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set, even with no robot (speech-out and robot actions will report the robot "
                "as missing). For offline development, set `reachy.backend` to `\"fake\"` or `\"sim\"`."
```

Replace with:

```python
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set. With no robot, the `say` voice and the robot actions are not registered "
                "(the agent reasons but cannot speak or move); with no TTS key, `say` exists but reports "
                "a TTS error. For offline development, set `reachy.backend` to `\"fake\"` or `\"sim\"`."
```

Run the gate (ruff line length is the only thing that can bite here).

### Step 6 — Align the specs

**`specs/app.md`, section "No credentials, no robot".** Find the paragraph that begins `Like the WICA demo, the app **opens even with keys unset (or no robot reachable)**` and replace the whole paragraph with:

> Like the WICA demo, the app **opens even with keys unset (or no robot reachable)** and says what to fix, rather than crashing. `build_system` degrades along three independent axes, each captured as one line in the UI banner, and nothing contacts a model, ASR, TTS provider, or robot without its prerequisite in place:
>
> - **Agent key missing** (`Wica.init` raises `MissingEnvError`, or `ImportError` for a missing provider extra) — `Agent: …`. Nothing reasons; ASR capture is not started either (there is no consumer for speech). Everything else still builds.
> - **TTS key missing** (`TTSEngineSynthesizer` raises when it resolves its key) — `TTS: …`. The robot is still built and entered, with **no synthesizer**: the mic, the ASR bridge, and the robot action commands are unaffected; `say` is registered and returns `"TTS error: …"` when called (the bridge's `say` raises `BridgeError` without a synthesizer).
> - **Robot unreachable** (a `real`/`sim` backend fails to construct or `__aenter__`) — `Robot: …`. There is no `ReachyMiniApi`, so the `say` voice and the robot action commands are **not registered** and no ASR bridge is built (its mic is the robot's). Typed text still drives the agent, so reasoning is visible; the agent simply has nothing to speak or move through.
>
> ASR's own key (`ASR: …`) degrades only speech-in — typed text remains the way in. For offline development, `reachy.backend = "fake"` (or `"sim"`) gives a robot with no hardware.

**`specs/app.md`, section "Wiring and lifecycle", step 7.** Find `7. \`wica.start()\`, then schedule \`asr_bridge.start()\` onto the loop with \`run_coroutine_threadsafe\`, then launch the Gradio UI. Teardown \`__aexit__\`s the api on the loop.` and replace the last sentence with: `Teardown (\`_shutdown\`) closes WICA, then schedules \`asr_bridge.stop()\` onto the loop, then \`__aexit__\`s the api on the loop, then stops the loop — the bridge before the api, so the mic pump is cancelled while the media session is still open.`

**`specs/app.md`, section "Wiring and lifecycle", step 3.** Find `` `ReachyMiniApi(cfg.reachy.backend, synthesizer=TTSEngineSynthesizer(cfg.tts_engine))` `` and replace with `` `ReachyMiniApi(cfg.reachy.backend, synthesizer=…)`, where the synthesizer is `TTSEngineSynthesizer(cfg.tts_engine)` built in its own guard just before (or `None` if its key is unset — see "No credentials, no robot") ``.

**`specs/configuration.md`, section "Secrets stay referenced, never stored".** Find `the ASR module and the bridge's synthesizer raise when they resolve their key) — the app catches it and reports what to set (see [app.md](app.md), "No credentials").` and replace with `the ASR module and the bridge's synthesizer raise when they resolve their key) — the app catches each one separately and reports what to set (see [app.md](app.md), "No credentials, no robot"); in particular an unset TTS key degrades only \`say\`, the robot itself still comes up.`

These are edits to `Updated`/`Implemented` specs that *describe* the code this plan builds, so no further status change beyond step 7.

### Step 7 — Statuses

- `specs/app.md`: change `**Status:** Updated` to `**Status:** Implemented`.
- `specs/_index.md`: the `app.md` row's Status column `Updated` → `Implemented`.
- This file: `**Status:** Todo` → `**Status:** Done`; `plans/_index.md`: this plan's row → `Done`.

## Verification

- `uv run pytest` runs 52 tests (50 existing + the two added here) and all pass:
  - `test_missing_tts_key_degrades_only_the_voice` — `missing` has a `TTS:` line naming the env var and no `Robot:` line; `api`, `voice`, `bridge` all present; `say` returns a string starting `"TTS error:"`.
  - `test_shutdown_stops_asr_before_the_robot_exits` — log is exactly `["asr.stop", "api.exit"]`.
  - The existing degrade tests (`test_robot_connect_failure_degrades_but_agent_still_reasons`, `test_missing_asr_key_degrades_but_robot_voice_survive`) pass unchanged.
- `uv run ruff check .` and `uv run pyright` report nothing.
- Manual smoke (optional, not required for `Done`): `ELEVENLABS_API_KEY= uv run reachy-mini-interaction-wica --config config.example.json` with `reachy.backend` set to `"fake"` opens the UI with a single `TTS: …` banner line and a live ASR status; Ctrl-C exits without a traceback.
- Mark this plan `Done` (here and in [_index.md](_index.md)) and app.md `Implemented` only once all of the above hold.
