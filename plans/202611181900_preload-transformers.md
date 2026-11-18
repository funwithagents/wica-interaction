# Transformers imported before any worker thread

**Status:** Done

Implements `specs/gesture-input.md` ("App integration", the transformers bullet): `build_system` imports `pipeline` from `transformers` on the main thread before the body, the ASR or the vision start, so the shipped gesture classifier and the `mlx-audio` ASR no longer import it on two threads at once. Leaves the libraries themselves (`vision-modules`, `mlx-audio`, `transformers`) unchanged.

## Scope

- `src/wica_interaction/gesture_input.py` — `preload_transformers()`, beside `preload_mediapipe()`
- `src/wica_interaction/system.py` — called first in `build_system` when vision is on and the classifier is the shipped one
- `tests/test_gesture_input.py` — the race, in a fresh interpreter, passing with the preload; a missing `transformers` left to the classifier

## Steps

1. `preload_transformers()`: `from transformers import pipeline`, an `ImportError` swallowed (the classifier reports it).
2. `build_system`: call it after `preload_mediapipe()`, under the same vision condition, when `gesture_models.classifier` is `None`.
3. The regression test: a subprocess calls the preload, then imports `mlx_audio.stt.models` and `from transformers import pipeline` on two threads, and exits non-zero on any error; skipped without `transformers` or `mlx_audio`.

## Verification

The race test fails with the preload disabled (`ImportError: cannot import name 'AutoTokenizer'`) and passes with it. `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`.
