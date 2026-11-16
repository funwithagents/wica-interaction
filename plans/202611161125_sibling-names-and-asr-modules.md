# Catch up with the siblings' names, and asr-engine's new modules

**Status:** Done

Implements [specs/project.md](../specs/project.md) ("Runtime dependencies — a base plus extras") against wica `81dac6e`, asr-engine `c9433e5` and tts-engine `73b0053`, the commits in which the three siblings settled one naming rule: registry keys and extras written with hyphens, a vendor named by its own name, a protocol module named `<origin>-<api>-compatible`. The app's extras, templates, tests and docs follow, and asr-engine's new provider extras and its VAD extra are mirrored. Both templates also turn on asr-engine's VAD gate ([specs/configuration.md](../specs/configuration.md), "The shared sections"). Left out: the end-to-end configs, which stay ungated, and reachy-mini-bridge and vision-modules, which stay at their locked commits.

What changed upstream:

- **wica** — extras `google-genai` → `gemini` and `mistralai` → `mistral`, removed with no alias; a new `openai-compatible` extra for the new `openai-chat-completions-compatible` provider. The provider values `google_genai` / `mistralai` still load, with a deprecation warning.
- **asr-engine** — module types with hyphens (`deepgram-v2`, `deepgram-v1`; the underscore names still load, with a deprecation warning); new modules `mistral-realtime` (extra `mistral`), `openai-realtime-compatible` and `openai-transcriptions-compatible` (one extra, `openai-compatible`), `mlx-audio` (extra `mlx-audio`, Apple silicon); an optional VAD gate, `engine.vad` (extra `silero`), required by the batch modules.
- **tts-engine** — the module type `openai-compatible` is now `openai-speech-compatible` (the old name still loads); its extra keeps the name `openai-compatible`, so `tts-openai-compatible` is unchanged.

## Scope

- `pyproject.toml` — `wica-google-genai` → `wica-gemini`, `wica-mistralai` → `wica-mistral`; new `wica-openai-compatible`, `asr-mistral`, `asr-openai-compatible`, `asr-mlx-audio`, `asr-silero`; all of them in `all`, the three agent extras in `dev` (every agent provider is), `asr-silero` in `dev` (the templates turn the gate on), no other new speech extra there.
- `uv.lock` — the three siblings moved, the new extras resolved (mlx, mlx-audio, soxr added).
- `config.computer.example.json`, `config.reachy.example.json`, `tests-e2e/configs/*.json` — `"type": "deepgram-v2"`; the two templates gain `"vad": {"type": "silero", "min_silence_duration_ms": 1500}`.
- `tests/` — the configs the tests build name `deepgram-v2`.
- `specs/project.md` — the extras table, the mirror rule (a renamed extra is followed with no alias; the `silero` feature extra is mirrored). `specs/configuration.md`, `specs/e2e.md` — the module name.
- `docs/reference/install.md`, `docs/reference/configuration.md`, `docs/guides/personas.md`, `README.md` — the extras table, the provider and module names, the `vad` field, `asr-silero` in the install recipes, the 24 kHz limit of `openai-realtime-compatible`.

## Steps

1. Rename and add the extras in `pyproject.toml`; `uv lock --upgrade-package wica --upgrade-package asr-engine --upgrade-package tts-engine`, `uv sync --dev`.
2. Write the module type with a hyphen in the templates, the e2e configs and the tests.
3. Update the spec and the docs as listed in Scope; `tests/test_docs.py` holds the install page's table to the extras.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`, all passing.
- The live tier was not run for this change.
