---
code:
  - pyproject.toml
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the WICA Interaction project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.12+ minimum.
- **Package layout:** `src/` layout — `src/wica_interaction/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root. The shared concepts are top-level modules; each embodiment is a **subpackage** (`wica_interaction/computer/`, `wica_interaction/reachy/`) holding only that embodiment's code, so the two never interleave in one file and each entry point imports only its own package ([embodiment.md](embodiment.md)).
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`.
- **Runtime dependencies — a base plus extras.** What every embodiment needs is a base dependency; what only one embodiment, one provider, or one optional feature needs is an **extra** (`[project.optional-dependencies]`), so a deployment installs only what it runs, and a missing extra surfaces at build time as the owner's own "install `<package>[<extra>]`" hint, caught into the UI banner ([app.md](app.md), "Degraded start"):

  | Extra | Pulls | For |
  |---|---|---|
  | *(base)* | `wica[gradio]`, `asr-engine`, `vision-modules` (its core: numpy + opencv, imported by `gesture_input.py`; the models are the `vision` extra), `gradio>=6`, `numpy` | the lifecycle, the UI, speech in, the vision plumbing |
  | `computer` | `tts-engine` | the computer embodiment's voice |
  | `audio-processing` | `aec-audio-processing`, `samplerate` | the computer's echo cancellation / noise suppression |
  | `reachy` | `reachy-mini-bridge` | the Reachy Mini embodiment (pulls the upstream `reachy-mini` SDK) |
  | `vision` | `vision-modules[hand]` | the hand-sign input (MediaPipe + the torch/transformers classifier), either embodiment |
  | `wica-anthropic` | `wica[anthropic]` | the agent provider the example configs select |
  | `asr-deepgram` | `asr-engine[deepgram]` | the ASR provider the example configs select |
  | `tts-elevenlabs` | `tts-engine[elevenlabs]` | the cloud voice — for **both** embodiments (the bridge builds its voice from the same tts-engine) |
  | `tts-pocket` | `tts-engine[pocket]` | the local voice (torch + a first-run weight download) — both embodiments |
  | `all` | every extra above | the dev environment |

  Extras, not dependency groups, because they are what a consumer can select by name (`uv sync --extra reachy --extra tts-pocket`, `pip install wica-interaction[reachy,tts-pocket]`), what the engines' runtime hints name, and what `[project.optional-dependencies]` lets reference another package's extra. Groups are dev-time only and invisible to an install. **Every third-party package the code imports directly is declared** in the base or in the extra of the module that imports it — `numpy` included — even when a sibling pulls it in transitively, so a sibling dropping a dependency cannot break this app silently; the converse holds too (a package no longer imported directly is removed).
- **The `dev` group** pulls `wica-interaction[all]` (a self-reference, through `[tool.uv.sources] wica-interaction = { workspace = true }`, as the bridge does) so the default `uv sync` runs both entry points and the whole test tier with nothing skipped, plus `reachy-mini-bridge[sim]` so the `sim` robot backend (the upstream MuJoCo mockup) runs locally for manual/e2e testing; `sim` stays out of runtime deps because a real-robot deployment needs none of it.
- **Sibling sources.** `wica`, `asr-engine`, `tts-engine`, `reachy-mini-bridge` and `vision-modules` are first-party sibling repos consumed as **path dependencies** via `[tool.uv.sources]` (`../wica-framework`, `../asr-engine`, `../tts-engine`, `../reachy-mini-bridge`, `../vision-modules`), editable, a pinned git URL later. `tts-engine` as a path source has one prerequisite: the bridge must name `tts-engine` by **plain name** in its own metadata. Today it names it by a GitHub direct URL, and uv refuses two different URLs for one package ("conflicting URLs for package `tts-engine`"), which is why the pre-merge pyproject deliberately left `tts-engine` out of its sources. Verified (2026-09-17): with the bridge on a plain name, a path source here resolves the whole graph — the bridge's requirement included — from `../tts-engine`, and the provider extras compose (`tts-engine[pocket]` pulls `pocket-tts`); with the bridge still on its URL, the only committed shapes that lock are the extras naming `tts-engine[<provider>] @ git+https://github.com/funwithagents/tts-engine@main` **verbatim** (the bridge's exact string, no path source), or an uncommitted `[tool.uv] override-dependencies` to a local checkout — which **replaces** the requirement, extras included, so it must repeat every provider extra it wants. The merge plan makes the bridge change its prerequisite and falls back to the verbatim-URL form if it cannot land first.
- **`[tool.uv] dependency-metadata`** for `pygobject`/`pycairo` (mirrored from the bridge, since path deps don't inherit `[tool.uv]` settings) lets uv resolve `reachy-mini`'s Linux-only sdists on macOS without building them.
- **Entry points:** `[project.scripts]` exposes one command per embodiment — `wica-interaction-computer` → `wica_interaction.computer.app:main`, `wica-interaction-reachy` → `wica_interaction.reachy.app:main` — each run as `uv run wica-interaction-<embodiment> --config <file>`. There is no umbrella command and no config discriminator: the command is the choice ([embodiment.md](embodiment.md), "Not in the protocol").
- **Formatting:** `uv run ruff format .` with ruff's default settings (88-column code; no `line-length` override — the same as the sibling packages). It runs as part of the verification gate right after `ruff check .` ([AGENTS.md](../AGENTS.md), "Commands"), so the tree is always formatter-clean. It also formats the Python code blocks inside the Markdown specs and plans. It never reflows comments or docstrings, which are hand-wrapped (at roughly 100 columns).
- **Linting:** `ruff check`, with its default rule set (no `select` override). Where a rule's heuristic conflicts with a deliberate, documented design (e.g. `TRY004` at the config-validation boundary, which raises `ValueError` for any malformed input to give `run_app` one error surface), suppress it locally with a `# noqa: <rule>` rather than reshaping the code. A `# noqa` for a rule that is not enabled is dead text and should be removed.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`). Full strategy in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Repo shape:**
  - `src/wica_interaction/` — the package: the shared modules (`config.py`, `asr_bridge.py`, `gesture_input.py`, `embodiment.py`, `system.py`, `app_ui.py`) and the `computer/` and `reachy/` subpackages.
  - `prompts/` — one persona per embodiment (`computer.md`, `reachy.md`).
  - `config.computer.example.json`, `config.reachy.example.json` — one template per embodiment.
  - `specs/` — pre-implementation design docs, one per concept (this folder).
  - `plans/` — implementation plans turning settled specs into buildable steps.
  - `tests/` at repo root, mirroring the package (`tests/computer/`, `tests/reachy/` mirror the subpackages).
  - `tests-e2e/` at repo root, for the live tier — not collected by the default `pytest` run.
- **The project map and its guard.** [AGENTS.md](../AGENTS.md) maps every module, subpackage modules included; `tests/test_project_map.py` checks the map against `src/wica_interaction/**/*.py` and the spec frontmatters against the same set, and holds an **`Implemented`** spec to naming only existing paths — a `Stable`/`Updated` spec may name the files it will govern, since the design gate precedes the code ([AGENTS.md](../AGENTS.md), "Spec frontmatter").

## Open questions

1. **Publishing.** Whether `wica-interaction` is ever published (and the sibling path sources pinned to git tags) is deferred; today it is a checkout-next-to-siblings project.
