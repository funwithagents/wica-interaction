# Bridge integration — speech & control through reachy-mini-bridge

**Status:** Done

Implements the retargeting captured in the `Updated` specs — [configuration.md](../specs/configuration.md),
[tts-output.md](../specs/tts-output.md), [asr-bridge.md](../specs/asr-bridge.md), and the wiring in
[app.md](../specs/app.md). It routes speech-out through the robot speaker and speech-in over the
robot's echo-cancelled mic, backed by a single `ReachyMiniApi`, and drops the local `TTSEngine`. It
deliberately leaves the robot *action* commands to the [robot-control plan](202609101715_robot-control.md).

## Scope

- `src/reachy_mini_interaction_wica/config.py` — add `ReachyConfig` + a `reachy` field on `AppConfig`; parse the optional `reachy` section (backend `real`/`sim`/`fake`, default `real`); the required sections stay `wica`/`asr`/`tts`.
- `src/reachy_mini_interaction_wica/tts_output.py` — `VoiceOutput.__init__(api: ReachyMiniApi)`; `say` delegates to `api.say`, guarded to the non-raising `"OK"` / `"TTS error: <msg>"` contract; drop the `TTSEngine` / `TTSTools` dependency.
- `src/reachy_mini_interaction_wica/asr_bridge.py` — add `RobotMicSource` (pumps `api.audio_input()` into the `asyncio.Queue[bytes]` the `ASREngine` drains). Its `start()`/`stop()` are **synchronous** — the `ASREngine` calls the injected `AudioSource` synchronously (`queue = source.start()`, not awaited), so `start()` spawns the pump via `loop.create_task` on the running loop and returns the queue, `stop()` cancels it; the source is restartable. `AsrBridge` itself unchanged.
- `src/reachy_mini_interaction_wica/app.py` — build one `ReachyMiniApi(cfg.reachy.backend, synthesizer=TTSEngineSynthesizer(cfg.tts_engine))` (from `reachy_mini_bridge.audio`), enter/exit it on the app-owned loop; build `VoiceOutput(api)` and `ASREngine(cfg.asr_engine, audio_source=RobotMicSource(api))`; report a robot-connection failure in `missing` (degrade, don't crash) — a `real`/`sim` backend can raise **at construction** (`ReachyMiniApi.__init__` → `build_robot` connects the upstream robot) *and/or* at `__aenter__`, and the failure is an upstream `reachy_mini` exception, not necessarily a bridge type, so wrap **both** the construction and the `__aenter__()` call in a broad `except Exception` that appends to `missing`; update the `--config` UI copy.
- `pyproject.toml` — **no dependency change** (Design A): `tts-engine` stays a direct dependency because `config.py` still builds `TTSEngineConfig` (which the app hands to the bridge's `TTSEngineSynthesizer`), and `reachy-mini-bridge[tts]` stays for that synthesizer path. Only refresh the `tts-engine` dependency comment, which currently says it is "still used directly by the current speech-out code (VoiceOutput)" — after this plan `VoiceOutput` no longer imports it; it is used by `config.py` to build the synthesizer's config.
- `config.example.json` — add a `reachy` section.
- `tests/test_config.py`, `tests/test_tts_output.py`, `tests/test_asr_bridge.py`, `tests/test_app.py` — update fakes and add functional tests (below).

## Steps

1. **Config.** Add `ReachyConfig(backend="real")`; parse `reachy` (validate backend ∈ {real, sim, fake}); add `reachy` to `AppConfig`. Keep `load_config` side-effect-free.
2. **Voice.** Rewrite `VoiceOutput` to hold a `ReachyMiniApi` and wrap `api.say` non-raisingly (empty text short-circuits; any exception → `"TTS error: <msg>"`).
3. **Mic source.** Add `RobotMicSource(api)` implementing the **synchronous** `AudioSource` protocol: `start() -> asyncio.Queue[bytes]` grabs `asyncio.get_running_loop()`, spawns a pump task (`loop.create_task`) that iterates `async for chunk in api.audio_input()` and `put_nowait`s each chunk, and returns the queue immediately; `stop()` cancels the pump task. `start()` resets its queue/task each call so the engine can restart the source. No `await` in `start`/`stop` themselves — the engine calls them synchronously (`queue = source.start()`).
4. **App wiring.** Build the single `ReachyMiniApi` with `TTSEngineSynthesizer(cfg.tts_engine)`; `run_coroutine_threadsafe(api.__aenter__(), loop)` at start and `__aexit__` at teardown; inject `RobotMicSource` into `ASREngine`; keep the build-order (api → voice → wica → bridge) and the no-credentials/degrade contract, adding the robot-connect failure to it. Because a `real`/`sim` backend can raise at **construction** (`build_robot` connects) as well as at `__aenter__`, and the exception is an upstream `reachy_mini` type, wrap **both** in one broad `except Exception` → append to `missing` (do not narrow to a bridge exception type). On failure `api` is `None`, so `voice`/mic wiring degrade like the other engines.
5. **Dependencies.** No dependency change (Design A): keep `tts-engine` (still imported by `config.py` to build `TTSEngineConfig`) and `reachy-mini-bridge[tts]` (provides the synthesizer). Only refresh the stale `tts-engine` dependency comment in `pyproject.toml`; confirm `uv sync` still resolves.
6. **Docs/status.** Flip [configuration.md](../specs/configuration.md), [tts-output.md](../specs/tts-output.md), and [asr-bridge.md](../specs/asr-bridge.md) from `Updated` back to `Implemented` (spec files + [specs/_index.md](../specs/_index.md)); mark this plan `Done`. [app.md](../specs/app.md) **stays `Updated`**: this plan built its speech-in/out + one-`ReachyMiniApi` wiring, but its command set still names the robot *action* commands (`play_emotion` / head-tracking / motors), which the [robot-control plan](202609101715_robot-control.md) has not yet wired — so `app.py` does not fully match `app.md` until that plan lands (its lag note is rewritten to name only that remaining gap).

## Verification

- Config tests: `reachy` parses (each backend), absent ⇒ `real`, invalid backend raises; required sections unchanged.
- Voice tests (fake `ReachyMiniApi`): `say("hi")` calls `api.say` once and returns `"OK"`; `say("")` returns the empty-text error without calling the api; an api exception maps to `"TTS error: <msg>"`.
- Mic-source tests (fake api yielding chunks): `start()` returns a queue receiving the chunks in order; `stop()` cancels the pump.
- App tests: `build_system` builds the api on the loop, wires `VoiceOutput`/`RobotMicSource`, and typed text + a closed segment both reach the agent and its `say` reaches the (fake) robot; robot-connect failure degrades with a captured message and the agent still reasons.
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass.
