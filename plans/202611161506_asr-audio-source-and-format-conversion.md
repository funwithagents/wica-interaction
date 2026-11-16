# Catch up with asr-engine: the `audio_source` block, and sources that declare their format

**Status:** Done

Implements [specs/asr-bridge.md](../specs/asr-bridge.md) ("The mic is the embodiment's"), [specs/audio-processing.md](../specs/audio-processing.md) ("Building the coordinator") and [specs/configuration.md](../specs/configuration.md) ("The shared sections") against asr-engine `865764a`. Since that commit the engine keeps two formats, the source's and the module's, and converts between them; `engine.audio` is `engine.audio_source`, with a `type` (`input-device`, `file`, `custom`) checked against how the engine is built; a source handed to the engine may declare its `audio_format`. The app follows: both bodies' mic sources declare 16 kHz, the engine is told the source is custom when a body supplies one, and the "the module must deliver 16 kHz" rule is gone — a 24 kHz module (`openai-realtime-compatible`) runs on either body. Left out: a live run with such a module.

## Scope

- `uv.lock` — asr-engine `c9433e5` → `865764a` (`soxr` becomes one of its core dependencies).
- `src/wica_interaction/system.py` — `asr_config_for(config, mic)`: with a mic supplied, an `input-device` block becomes `custom`; any other case passes the config as written. The default engine factory uses it.
- `src/wica_interaction/reachy/embodiment.py` — `RobotMicSource.audio_format`: mono linear16 at `bridge.mic_sample_rate`.
- `src/wica_interaction/computer/audio_processing.py` — the coordinator's `source` declares `NEAR_FORMAT`; `build_coordinator` reads `audio_source.device`, and refuses a source that is not an input device, or a device format other than the working one, instead of checking the module's format.
- `config.computer.example.json`, `tests-e2e/configs/computer.json` — `"audio_source": {"type": "input-device", "device": null}`; `config.reachy.example.json`, `tests-e2e/configs/reachy.json` — `"audio_source": {"type": "custom"}`; `tests-e2e/conftest.py` — the mic override's path.
- `tests/` — the configs the tests build; new tests: each source's declared format, the engine built over a body's mic for each `audio_source` the file may carry (converting 16 kHz for a 24 kHz module), the config untouched with no mic, a `file` next to a mic refused, the two reasons the coordinator declines to build.
- `specs/` — asr-bridge, configuration, audio-processing, computer-embodiment, reachy-embodiment, embodiment, e2e. `docs/` — the configuration reference (the `audio_source` row, no 16 kHz rule), the computer and Reachy Mini guides, troubleshooting.

## Steps

1. `uv lock --upgrade-package asr-engine`, `uv sync --dev`; the fast tier shows every config the old block breaks.
2. Rename the block in the templates, the e2e configs and the tests.
3. Declare the two sources' formats; add `asr_config_for`; rewrite the coordinator's check.
4. Add the tests listed in Scope; update the specs and the docs.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`, all passing.
- The three templates load, and a real `ASREngine` builds over each body's kind of source.
- The live tier was not run for this change.
