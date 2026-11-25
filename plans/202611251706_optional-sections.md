# Optional sections

**Status:** In progress

Implements [specs/configuration.md](../specs/configuration.md) ("Optional sections"), with its consequences in [setup.md](../specs/setup.md), [session.md](../specs/session.md), [app.md](../specs/app.md), [gesture-input.md](../specs/gesture-input.md), [computer-embodiment.md](../specs/computer-embodiment.md) and [audio-processing.md](../specs/audio-processing.md): four sections become optional, their presence their switch — `asr` (both apps), `vision` (both), `audio_processing` and `camera` (computer) — absent means off, present (any document) means on. `wica`, `tts` and `reachy` stay required. Two phases: the app itself first, then the sections declared optional to app-setup.

## Scope

- `src/wica_interaction/config.py` — `asr` optional (`AppConfig.asr_engine` `None` when absent); `VisionConfig` loses `enabled`, a document holding it refused with `VISION_ENABLED_GONE`
- `src/wica_interaction/setup.py` — `enabled` gone from `VISION_FIELDS`; the vision contract no longer gated on it (its extra, models, prepare and probe for any section present), `/enabled` reported with the loader's message; the starting documents `VISION_START`, `AUDIO_PROCESSING_START`, `CAMERA_START`
- `src/wica_interaction/computer/config.py` — `audio_processing` and `camera` `None` when absent; `config_problems` (hand signs need the camera), raised by `load_config`
- `src/wica_interaction/computer/embodiment.py`, `computer/audio_processing.py` — no processing without the section, the processing refused (one `missing` line) with speech input off; `camera()` without the section raises the rule's wording
- `src/wica_interaction/system.py` — no ASR engine or bridge without `asr`; vision on with a `vision` section; `AppSystem.speech_on` / `signs_on`; `run_app(check=…)`
- `src/wica_interaction/session.py` — `Session(check=…)`, `Session.blockers(model)`: app-setup's blockers, then the app's rule across sections
- `src/wica_interaction/app_ui.py` — the bar reads `Session.blockers`; "speech input is off" and "hand signs are off" lines
- `src/wica_interaction/computer/app.py`, `reachy/app.py` — `STARTING_DOCUMENTS`; the computer passes `config_problems` as its `check`
- `src/wica_interaction/*/default/config.json`, `tests-e2e/configs/*.json` — `vision.enabled` removed (hand signs stay on: the section stays)
- `tests/` — the cases below; `docs/` and `README.md` — the optional sections, the retired `enabled`, the rule across sections

## Steps

### Phase 1: the app (done)

1. Loaders: `asr`, `audio_processing`, `camera` absent ⇒ `None`; `vision` without `enabled`, refusing it with how to fix it.
2. The vision contract follows: no `enabled` field or gate; `/enabled` reported.
3. The runtime: no ASR without `asr` (no banner line), no processing without `audio_processing` (and none with speech input off), no webcam without `camera`; the page says "off" for a section left out, "unavailable" for one that failed.
4. The rule across sections: `computer.config.config_problems`, raised by the loader, handed to `run_app` as `check`, folded into `Session.blockers` (Start and the session bar).
5. The starting documents, per section (`setup.py`) and per app (`STARTING_DOCUMENTS`, `asr` from the default config).
6. Default configs keep the first launch as it was: every section that was on stays present, `vision` without `enabled`.
7. Specs, docs, tests.

### Phase 2: the sections declared optional to app-setup

1. Pick up app-setup's `Section(optional=True, default=…)` (`uv lock --upgrade-package app-setup`).
2. Declare `asr`, `vision`, `audio_processing`, `camera` with `optional=True, default=STARTING_DOCUMENTS[name]` in both apps' `SECTIONS`; an absent one is then neither checked nor counted by Start's blockers, and the Setup tab offers Add and Remove.
3. Tests: an app config without an optional section has no app-setup problem and starts; Add writes the starting document. Then this plan `Done`, and the Updated specs back to `Implemented`.

## Verification

`tests/test_config.py`, `tests/computer/test_config.py`, `tests/reachy/test_config.py` (each section absent ⇒ `None`, `vision.enabled` refused, vision without camera refused, the starting documents load, the default configs load), `tests/test_setup.py` (`/enabled`, the vision extra for any section, the starting documents pass their contracts), `tests/test_system.py` (no `asr`: no engine, no dictation, nothing missing, typed text answered; `vision: {}` is on), `tests/computer/test_embodiment.py` (no processing, processing with speech off, no camera), `tests/test_session.py` and `tests/computer/test_app.py` (the rule across sections blocks Start), `tests/test_app_ui.py` (the off lines), `tests/test_docs.py`. Then `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`, `uv run pytest`.
