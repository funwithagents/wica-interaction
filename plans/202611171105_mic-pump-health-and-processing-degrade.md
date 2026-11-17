# The processed mic's health, and audio processing that degrades whatever fails

**Status:** Done

Implements [specs/audio-processing.md](../specs/audio-processing.md) ("Near-end path": the pump's health and its bounded queue) and [specs/computer-embodiment.md](../specs/computer-embodiment.md) (`enter()` step 1, `mic_error`). Two gaps a repository review found on the computer: a coordinator build failing with an exception outside a shortlist of three types cost the whole body, against the protocol's per-component rule; and a mic pump that died left the ASR line reading `running` with no audio. Left out: the engine's own capture (no coordinator), which already reports its failures through the engine.

## Scope

- `src/wica_interaction/computer/audio_processing.py` — `AudioProcessingCoordinator.capture_error`; the pump catches and records its failure; the output queue is bounded (`out_maxsize`, 20 chunks) and drops its oldest chunk when full.
- `src/wica_interaction/computer/embodiment.py` — `enter()` catches any exception from the coordinator factory; `mic_error` returns the coordinator's `capture_error`.
- `tests/computer/test_audio_processing.py` — a raising processor and a malformed chunk become a `capture_error`, cleared by a restart; a stalled consumer keeps the newest chunks at the bound.
- `tests/computer/test_embodiment.py`, `tests/computer/fakes.py` — an unforeseen build failure degrades the processing alone; `mic_error` follows the coordinator.
- `specs/audio-processing.md`, `specs/computer-embodiment.md`, `specs/embodiment.md` — the behavior above; `docs/guides/troubleshooting.md` — the computer's `⚠️ capture:` cause.

## Steps

1. Coordinator: `_capture_error`, reset in `_source_start`; `_pump` wrapped in `try/except Exception` (cancellation propagates), logging and recording `mic processing failed: <exc>`; the output queue built with `maxsize`, the oldest chunk dropped when full — `RobotMicSource`'s pattern.
2. Embodiment: `except Exception` around the factory call; `mic_error` from the coordinator.
3. Tests, then the specs and the troubleshooting line.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`: all pass. The three specs went `Implemented` → `Updated` → `Implemented` within this change.
