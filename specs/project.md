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
  | *(base)* | `wica[gradio]`, `asr-engine`, `vision-modules` (its core: **numpy only** — the stream, pipeline and `FrameLike` runtime `gesture_input.py` imports; OpenCV and the models are the `vision` extra), `gradio>=6`, `numpy` | the lifecycle, the UI, speech in, the vision plumbing |
  | `computer` | `tts-engine` | the computer embodiment's voice |
  | `audio-processing` | `aec-audio-processing`, `samplerate` | the computer's echo cancellation / noise suppression |
  | `reachy` | `reachy-mini-bridge` | the Reachy Mini embodiment (pulls the upstream `reachy-mini` SDK) |
  | `vision` | `vision-modules[hand,opencv]` | the hand-sign input (MediaPipe + the torch/transformers classifier), either embodiment — with the library's `opencv` extra, which its `OpenCVSource` (the computer's webcam) and its snapshot writer (both embodiments' Vision snapshot buttons) import lazily; the library made OpenCV optional on 2026-09-29, and nothing here imports `cv2` outside the `vision` feature (the tests that do read fixtures with it come through `dev` → `vision`) |
  | `wica-anthropic` | `wica[anthropic]` | the Anthropic agent provider |
  | `wica-huggingface-hub` | `wica[huggingface-hub]` | the Hugging Face agent provider (Inference Providers hub) |
  | `wica-openai` | `wica[openai]` | the OpenAI agent provider |
  | `wica-google-genai` | `wica[google-genai]` | the Gemini agent provider (Gemini Developer API) |
  | `wica-mistralai` | `wica[mistralai]` | the Mistral agent provider (La Plateforme) |
  | `asr-deepgram` | `asr-engine[deepgram]` | the ASR provider the example configs select |
  | `tts-elevenlabs` | `tts-engine[elevenlabs]` | the ElevenLabs cloud voice — for **both** embodiments (the bridge builds its voice from the same tts-engine) |
  | `tts-gradium` | `tts-engine[gradium]` | the Gradium cloud voice — both embodiments |
  | `tts-mistral`, `tts-deepgram`, `tts-gemini`, `tts-cartesia`, `tts-openai`, `tts-openai-compatible` | `tts-engine[<name>]` | the other hosted voices (Mistral, Deepgram, Gemini, Cartesia, OpenAI's Realtime API, any OpenAI-compatible `/v1/audio/speech` server) — both embodiments |
  | `tts-pocket` | `tts-engine[pocket]` | the local voice the reachy template selects (torch + a first-run weight download) — both embodiments |
  | `tts-phonon`, `tts-qwen3`, `tts-kokoro`, `tts-piper` | `tts-engine[<name>]` | the other local voices (Gradium's Phonon, Qwen3-TTS — Apple silicon only, gated by tts-engine's own marker — Kokoro-82M, Piper — GPL-3.0+) — both embodiments |
  | `all` | every extra above | a one-command install of everything; **not** the dev environment (see "The `dev` group" below) |

  The provider extras mirror the siblings' **one-to-one**, under the sibling's own extra name (`wica-<name>`, `asr-<name>`, `tts-<name>`), so an engine's "install `<package>[<name>]`" hint always has a matching extra here. Exceptions: the siblings' `mcp` extras (a transport this app never runs) and their bundles (`hosted-modules`, `all-modules`, `all` — they hide which provider an install carries) are not mirrored; nor are asr-engine's `kyutai-mlx` / `kyutai-torch`, whose `moshi-mlx` / `moshi` pin `numpy<2.3` against the base's `vision-modules` (`numpy>=2.5.3`) — declaring either makes the universal lock unsolvable (verified 2026-11-03), so they wait for one side to move.

  Extras, not dependency groups, because they are what a consumer can select by name (`uv sync --extra reachy --extra tts-pocket`, `pip install wica-interaction[reachy,tts-pocket]`), what the engines' runtime hints name, and what `[project.optional-dependencies]` lets reference another package's extra. Groups are dev-time only and invisible to an install. **Every third-party package the code imports directly is declared** in the base or in the extra of the module that imports it — `numpy` included — even when a sibling pulls it in transitively, so a sibling dropping a dependency cannot break this app silently; the converse holds too (a package no longer imported directly is removed).
- **The `dev` group** pulls the app's own extras (a self-reference, through `[tool.uv.sources] wica-interaction = { workspace = true }`, as the bridge does) — **what the example configs and the test tier run, not `all`**: both embodiments, `audio-processing`, `vision`, every agent provider (light LangChain packages), and the speech providers the example configs select (`asr-deepgram`; `tts-elevenlabs`, `tts-gradium`, `tts-pocket`) — so the default `uv sync` runs both entry points against every example config and the whole test tier with nothing skipped. The tests never import a speech provider's SDK (they drive tts-engine's core over fakes), so installing the other voices would buy no coverage and cost their runtimes (onnxruntime, ptts, a GPL-3.0+ package); `uv lock` resolves every extra regardless, so a provider that stops resolving still fails the lock. A config template that selects a new provider adds its extra to `dev` in the same change. Plus `reachy-mini-bridge[sim]` so the `sim` robot backend (the upstream MuJoCo mockup) runs locally for manual/e2e testing; `sim` stays out of runtime deps because a real-robot deployment needs none of it.
- **Sibling sources.** `wica`, `asr-engine`, `tts-engine`, `reachy-mini-bridge` and `vision-modules` are first-party sibling repos, every one consumed from **GitHub** through `[tool.uv.sources]`: `<name> = { git = "https://github.com/funwithagents/<repo>", rev = "main" }` (`wica` lives in the `wica-framework` repo; the other four are named after their package). `uv.lock` records the exact commit each resolved to, so an install is reproducible and needs no checkout next door, and a newer commit is taken deliberately (`uv lock --upgrade-package <name>`, then commit the lock). No sibling is a path dependency any more (the three that were — `../wica-framework`, `../reachy-mini-bridge`, `../vision-modules` — moved on 2026-11-03, once their APIs had settled and every sibling was public with its own CI). `tts-engine` has one constraint the others don't: the bridge names it by a **GitHub direct URL** in its own metadata (`tts-engine @ git+https://github.com/funwithagents/tts-engine@main` — its settled policy for first-party siblings, so that its own consumers inherit the source, which `[tool.uv.sources]` never are), and uv refuses two *different* URLs for one package ("conflicting URLs for package `tts-engine`"). The git source here must therefore be that URL **exactly**, reference kind included: `rev = "main"` is the same reference as the bridge's `@main` suffix, whereas `branch = "main"` is a different kind and fails to lock with the two URLs printed identically (verified 2026-09-25). With the URLs identical, no `[tool.uv] override-dependencies` is needed, and each install carries only the `tts-engine` provider extras the selected `tts-*` extras name. **Developing a sibling locally** against this app is an *uncommitted* convenience: point its source at the checkout for the duration (`{ path = "../<repo>", editable = true }`); for `tts-engine` alone that path source has to be paired with `[tool.uv] override-dependencies = ["tts-engine"]`, which lets the checkout win over the bridge's direct URL (it costs every install all of tts-engine's providers). Either shape is reverted before committing — the declared dependency is always the GitHub source.
- **`[tool.uv] dependency-metadata`** for `pygobject`/`pycairo` (mirrored from the bridge, since a dependency's `[tool.uv]` settings are not inherited) lets uv resolve `reachy-mini`'s Linux-only sdists on macOS without building them.
- **Linux torch from PyTorch's CPU index.** PyPI's Linux `torch` wheel is the CUDA build and drags gigabytes of NVIDIA libraries into the lock; `[[tool.uv.index]] pytorch-cpu` (`explicit = true`) plus a `torch` source gated on `sys_platform == 'linux'` resolve it from `https://download.pytorch.org/whl/cpu` instead — the same choice every sibling that pulls torch makes (`vision-modules[hand]`, `tts-engine[pocket]`), declared here because a consumer cannot rely on inheriting a dependency's index. macOS and Windows keep PyPI's wheel, CPU-only there.
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

1. **Publishing.** Whether `wica-interaction` is ever published (and the sibling git sources pinned to tags rather than `main`) is deferred; today it runs from a checkout, its siblings installed from GitHub.
