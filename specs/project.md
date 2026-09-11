---
code:
  - pyproject.toml
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the Reachy Mini Interaction WICA project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.12+ minimum.
- **Package layout:** `src/` layout — `src/reachy_mini_interaction_wica/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`. The dev group also pulls `reachy-mini-bridge[sim]` so the `sim` robot backend (the upstream MuJoCo mockup) runs locally for manual/e2e testing; it stays out of runtime deps because a real-robot deployment needs none of it (mirroring the bridge's own dev setup).
- **Runtime dependencies:** the sibling packages `wica`, `asr-engine`, `reachy-mini-bridge`, and `tts-engine`, consumed as local **path dependencies** via `[tool.uv.sources]` (they live in the parent folder: `../wica-framework`, `../asr-engine`, `../reachy-mini-bridge`, `../tts-engine`). `reachy-mini-bridge` is pulled with its `[tts]` extra so its default `tts-engine`-backed synthesizer is available for `say`. `tts-engine` is **also** a direct dependency — not for speech-out (which goes through the bridge, see [tts-output.md](tts-output.md)) but because [config.py](../src/reachy_mini_interaction_wica/config.py) builds the `TTSEngineConfig` the app hands to the bridge's `TTSEngineSynthesizer` (see [configuration.md](configuration.md)). Its `[tool.uv.sources]` entry is `editable`, matching the bridge's own source for it (uv rejects conflicting editable/non-editable URLs for the same package across the resolved graph). `gradio` (the UI) is a plain PyPI dependency. `reachy-mini-bridge` transitively pulls the upstream `reachy-mini` SDK; a `[tool.uv] dependency-metadata` block (mirrored from the bridge, since path deps don't inherit `[tool.uv]` settings) lets uv resolve its Linux-only `PyGObject`/`pycairo` without building them on macOS.
- **Linting/formatting:** `ruff`.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`) that calls real external services. Full strategy is specced in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Repo shape:**
  - `src/reachy_mini_interaction_wica/` — the package, one module per core concept.
  - `specs/` — pre-implementation design docs, one per concept (this folder).
  - `plans/` — implementation plans turning settled specs into buildable steps.
  - `tests/` at repo root, mirroring the `src/reachy_mini_interaction_wica/` module structure.
  - `tests-e2e/` at repo root, for the live tier above — not collected by the default `pytest` run.

## Open questions

None currently.
