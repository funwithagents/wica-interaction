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
- **Runtime dependencies:** the sibling packages `wica`, `asr-engine`, `reachy-mini-bridge`, and `vision-modules`, consumed as local **path dependencies** via `[tool.uv.sources]` (they live in the parent folder: `../wica-framework`, `../asr-engine`, `../reachy-mini-bridge`, `../vision-modules`). `vision-modules` is pulled with its `[hand]` extra (MediaPipe hand detection + the torch/transformers HaGRID gesture classifier) for the fist gesture input (see [gesture-input.md](gesture-input.md)); its core is numpy + opencv only. `wica` is pulled with two extras: `[anthropic]` (the provider the committed example config selects) and `[gradio]` (WICA's reusable `wica.contrib.gradio` observability panels, which the app's UI places — see [app.md](app.md)); core `wica` never imports Gradio, so the extra is what makes `wica.contrib.gradio` importable. `reachy-mini-bridge` carries [`tts-engine`](../../tts-engine) as a **base** dependency (that is where `say`'s default synthesizer comes from — the bridge builds it itself from the config's `reachy.tts` block, see [configuration.md](configuration.md)) and mirrors one extra per tts-engine provider, so **the extra to install is named by the config**: `reachy.tts.module.type` `pocket` ⇒ `reachy-mini-bridge[tts-pocket]`, `elevenlabs` ⇒ `[tts-elevenlabs]`. The app pulls `[tts-pocket]`, matching the committed example config. **`pocket` is the committed default** — a local model, so a fresh checkout has a talking robot with no TTS credential and no network call at synthesis time; it costs `torch` plus a first-run weight download, which is already paid for by `vision-modules[hand]`. ASR is the same story one level up: the provider SDKs are `asr-engine`'s own extras, so the app selects `asr-engine[deepgram]` (asr type `deepgram_v2`, what the example config uses). Switching provider in either case means editing the config and swapping the matching extra. **`tts-engine` is deliberately absent from `[tool.uv.sources]`** (and from `dependencies`): the bridge names it by GitHub URL as a *direct reference* in its own metadata, which a uv source here cannot redirect — sources reach only this project's own dependencies, and re-declaring `tts-engine` directly to force one fails the resolve outright (`conflicting URLs for package tts-engine`). So it resolves from GitHub, the lock pins its commit, and picking up newer ones is a deliberate `uv lock --upgrade-package tts-engine`. Driving a *local* `../tts-engine` checkout from this repo needs an uncommitted `[tool.uv] override-dependencies = ["tts-engine[pocket] @ file:///abs/path"]` — an override replaces the whole requirement, so the extra has to be repeated in it. `gradio` (the UI) is a plain PyPI dependency. `reachy-mini-bridge` transitively pulls the upstream `reachy-mini` SDK; a `[tool.uv] dependency-metadata` block (mirrored from the bridge, since path deps don't inherit `[tool.uv]` settings) lets uv resolve its Linux-only `PyGObject`/`pycairo` without building them on macOS.
- **Formatting:** `uv run ruff format .` with ruff's default settings (88-column code; no `line-length` override — the same as the sibling `wica-framework` / `tts-engine` / `asr-engine` / `vocal-interaction-wica` packages). It runs as part of the verification gate right after `ruff check .` (see [AGENTS.md](../AGENTS.md), "Commands"), so the tree is always formatter-clean. It also formats the Python code blocks inside the Markdown specs and plans. It never reflows comments or docstrings, which are hand-wrapped (at roughly 100 columns).
- **Linting:** `ruff check`, with its default rule set (no `select` override). A `# noqa` for a rule that is not enabled is dead text and should be removed.
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
