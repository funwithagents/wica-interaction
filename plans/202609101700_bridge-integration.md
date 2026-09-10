# Bridge integration — speech & control through reachy-mini-bridge

**Status:** Todo

Implements the retargeting captured in the `Updated` specs — [configuration.md](../specs/configuration.md),
[tts-output.md](../specs/tts-output.md), [asr-bridge.md](../specs/asr-bridge.md), and the wiring in
[app.md](../specs/app.md). It routes speech-out through the robot speaker and speech-in over the
robot's echo-cancelled mic, backed by a single `ReachyMiniApi`, and drops the local `TTSEngine`. It
deliberately leaves the robot *action* commands to the [robot-control plan](202609101715_robot-control.md).

## Scope

- `src/reachy_mini_interaction_wica/config.py` — add `ReachyConfig` + a `reachy` field on `AppConfig`; parse the optional `reachy` section (backend `real`/`sim`/`fake`, default `real`); the required sections stay `wica`/`asr`/`tts`.
- `src/reachy_mini_interaction_wica/tts_output.py` — `VoiceOutput.__init__(api: ReachyMiniApi)`; `say` delegates to `api.say`, guarded to the non-raising `"OK"` / `"TTS error: <msg>"` contract; drop the `TTSEngine` / `TTSTools` dependency.
- `src/reachy_mini_interaction_wica/asr_bridge.py` — add `RobotMicSource` (pumps `api.audio_input()` into the `asyncio.Queue[bytes]` the `ASREngine` drains); `AsrBridge` itself unchanged.
- `src/reachy_mini_interaction_wica/app.py` — build one `ReachyMiniApi(cfg.reachy.backend, synthesizer=TTSEngineSynthesizer(cfg.tts_engine))`, enter/exit it on the app-owned loop; build `VoiceOutput(api)` and `ASREngine(cfg.asr_engine, audio_source=RobotMicSource(api))`; report a robot-connection failure in `missing` (degrade, don't crash); update the `--config` UI copy.
- `pyproject.toml` — drop the direct `tts-engine` dependency and its `[tool.uv.sources]` entry once `tts_output` no longer imports it (the bridge's `[tts]` extra provides the synthesizer).
- `config.example.json` — add a `reachy` section.
- `tests/test_config.py`, `tests/test_tts_output.py`, `tests/test_asr_bridge.py`, `tests/test_app.py` — update fakes and add functional tests (below).

## Steps

1. **Config.** Add `ReachyConfig(backend="real")`; parse `reachy` (validate backend ∈ {real, sim, fake}); add `reachy` to `AppConfig`. Keep `load_config` side-effect-free.
2. **Voice.** Rewrite `VoiceOutput` to hold a `ReachyMiniApi` and wrap `api.say` non-raisingly (empty text short-circuits; any exception → `"TTS error: <msg>"`).
3. **Mic source.** Add `RobotMicSource(api)` with `start() -> asyncio.Queue[bytes]` (spawn a pump task iterating `api.audio_input()` and `put`-ing chunks) and `stop()` (break + cancel).
4. **App wiring.** Build the single `ReachyMiniApi` with `TTSEngineSynthesizer(cfg.tts_engine)`; `run_coroutine_threadsafe(api.__aenter__(), loop)` at start and `__aexit__` at teardown; inject `RobotMicSource` into `ASREngine`; keep the build-order (api → voice → wica → bridge) and the no-credentials/degrade contract, adding the robot-connect failure to it.
5. **Dependency cleanup.** Remove the direct `tts-engine` dep/source (kept only for the old code path); confirm `uv sync` still resolves via `reachy-mini-bridge[tts]`.
6. **Docs/status.** Flip [configuration.md](../specs/configuration.md), [tts-output.md](../specs/tts-output.md), [asr-bridge.md](../specs/asr-bridge.md), and [app.md](../specs/app.md) from `Updated` back to `Implemented` (spec files + [specs/_index.md](../specs/_index.md)); mark this plan `Done`.

## Verification

- Config tests: `reachy` parses (each backend), absent ⇒ `real`, invalid backend raises; required sections unchanged.
- Voice tests (fake `ReachyMiniApi`): `say("hi")` calls `api.say` once and returns `"OK"`; `say("")` returns the empty-text error without calling the api; an api exception maps to `"TTS error: <msg>"`.
- Mic-source tests (fake api yielding chunks): `start()` returns a queue receiving the chunks in order; `stop()` cancels the pump.
- App tests: `build_system` builds the api on the loop, wires `VoiceOutput`/`RobotMicSource`, and typed text + a closed segment both reach the agent and its `say` reaches the (fake) robot; robot-connect failure degrades with a captured message and the agent still reasons.
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass.
