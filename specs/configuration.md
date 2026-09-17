---
code:
  - src/wica_interaction/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Purpose

The whole app is stood up from **one JSON config file** carrying the sub-configurations of the pieces it wires together: `wica` (the agent), `asr` (speech in), optionally `vision` (the hand-sign input), plus the sections of the **embodiment** the file is for — `tts` / `audio_processing` / `camera` for the computer, `reachy` for the robot. Switching provider, voice, ASR backend, robot backend, camera or persona is a file edit, not a code change. This spec defines the shared part of that file and how a section becomes a live config, **reusing each engine's own loader and validation** rather than re-implementing it; the embodiment sections are specified in [computer-embodiment.md](computer-embodiment.md) and [reachy-embodiment.md](reachy-embodiment.md).

## Each engine owns its config schema

Each sibling engine already defines and validates its own config, and we do **not** duplicate or fork those schemas:

- **WICA** — [`WicaConfig.from_dict(data, base_dir=…)`](../../wica-framework/specs/config.md) parses an `{"agent": {...}}` document (provider/model/key/`system_prompt_file`/`model_kwargs`).
- **ASR** — [`ASREngineConfig.from_dict(engine_block)`](../../asr-engine/specs/configuration.md) builds the ASR engine config from an `engine` block.
- **TTS** (computer) — [`TTSEngineConfig.from_dict(engine_block)`](../../tts-engine/specs/configuration.md), the same shape one level down.
- **`reachy`** — [`ReachyMiniConfig.from_dict(...)`](../../reachy-mini-bridge/specs/config.md), the bridge's own declarative config, voice included.

Nesting each engine's native document verbatim (rather than flattening the keys) keeps the file a thin *composition* of schemas the engines already own: an engine adding or renaming a field needs no change here, and a reader can follow each section straight to that engine's config spec.

## The shared sections

```json
{
  "wica": {
    "agent": {
      "provider": "anthropic",
      "model": "claude-sonnet-5",
      "api_key_env": "WICA_ANTHROPIC_API_KEY",
      "system_prompt_file": "prompts/reachy.md"
    }
  },
  "asr": {
    "engine": {
      "module": { "type": "deepgram_v2", "api_key_env": "DEEPGRAM_API_KEY", "model": "flux-general-en" },
      "audio": { "device": null },
      "dictation_default_segmentation_mode": "trigger_word"
    }
  },
  "vision": { "enabled": false, "threshold": 0.55, "fps": 5, "max_hands": 2, "device": null }
}
```

- `wica` and `asr` are **required**; `vision` is optional.
- `wica.agent.system_prompt_file` names the persona — `prompts/computer.md` or `prompts/reachy.md` — resolved relative to the config file's directory.
- `asr.engine.audio.device` is the computer embodiment's microphone; the Reachy Mini embodiment ignores it (the mic is the robot's). Either way the module must produce **16 kHz mono linear16** (the robot's `mic_sample_rate`; the audio-processing working rate).

### The `vision` section — the app's own small schema

Enables the hand-sign input ([gesture-input.md](gesture-input.md)) over whichever camera the embodiment provides. Unlike the engine sections it is **not** delegated to a sibling loader: `vision-modules` is a library with constructor kwargs and no config document of its own, so the app owns this schema in `config.py` as a frozen `VisionConfig(enabled, threshold, fps, max_hands, device)` with a `from_dict(data, path=…)` that validates it:

- `enabled` — the switch; a boolean, default **`false`**. The section can stay in the file, tuned, with vision off; turning it on is a one-key edit.
- `threshold` — the gesture classifier's validation threshold; a number in `[0, 1]`, default **`0.55`**.
- `fps` — one rate for the camera poll, the hand stage and the classifier; a number `> 0`, default **`5`**.
- `max_hands` — how many hands are detected, published and classified per frame, and so how many `hand_gesture_<n>` entries exist; an integer `>= 1` (not a bool, not a float), default **`2`** ([gesture-input.md](gesture-input.md), "The pipeline").
- `device` — the classifier's torch device (`"mps"`, `"cuda"`, `"cpu"`); `null`/absent lets the library pick.

**Vision is off unless `enabled` is `true`** — an absent section, `null`, `{}`, or `enabled: false` all mean no camera is opened, no model downloaded, no `hand_gesture_<n>` entry registered. It is an explicit opt-in because its first run downloads ~340 MB of models. Not an object, an unknown key, or an out-of-range value raises `ValueError` from the loader, naming the file and the field. Which camera it reads is the embodiment's business: the computer's `camera` section, the robot's own camera.

## Loading

```python
@dataclass
class AppConfig:
    """The shared sections, what the lifecycle (system.py) is built from."""

    wica: WicaConfig
    asr_engine: ASREngineConfig
    vision: VisionConfig | None = None


def read_config_file(path: str) -> dict[str, Any]: ...


def load_app_config(
    data: dict[str, Any], *, path: str, sections: frozenset[str]
) -> AppConfig: ...
```

Two layers, so the shared rules live once and each embodiment adds only its own sections:

1. **`read_config_file(path)`** reads and JSON-parses the file; raises `ValueError` if it is missing, unreadable, not valid JSON, or not a JSON object.
2. **`load_app_config(data, path=…, sections=…)`** validates the shared part of the parsed object: the required `wica` and `asr` keys are present; **no top-level key outside `sections`** (the shared names plus the calling embodiment's own) — an unknown section is a `ValueError` naming it, so a reachy file handed to the computer command fails on `'reachy'` rather than being silently ignored; then delegates each section to its owner's `from_dict`, passing the in-memory block directly (no temp files):
   - `wica` → `WicaConfig.from_dict(data["wica"], base_dir=os.path.dirname(path))`
   - `asr` → `ASREngineConfig.from_dict(data["asr"]["engine"])` (a section that is not an object with an `engine` block is a `ValueError`)
   - `vision` → `VisionConfig.from_dict(data["vision"], path=path)` when present and not `null`, else `None`
3. **Each embodiment loads its own file.** `computer/config.py` and `reachy/config.py` each expose `load_config(path) -> ComputerConfig | ReachyConfig`: read the file once, call `load_app_config` with its section set, parse its own sections into the same object (`.app` holds the `AppConfig`). The entry point calls that loader ([app.md](app.md), "Wiring and lifecycle").

**One error type.** Everything a loader can raise for a bad file is a `ValueError`, so `run_app` has a single thing to catch to open in degraded mode ([app.md](app.md), "Degraded start"). This falls out for free: WICA's, tts-engine's and the bridge's `ConfigError` all subclass `ValueError`, the ASR loader raises `ValueError` directly, and the app's own `VisionConfig`/`CameraConfig`/`AudioProcessingConfig` parsers raise it too. Field-level errors propagate unchanged, keeping the owner's message; nothing is re-wrapped or swallowed.

**Why `base_dir`, not a temp file:** the one thing a path-based loader gave for free was WICA resolving `agent.system_prompt_file` *relative to the config file's directory*. `WicaConfig.from_dict` takes an optional `base_dir`, so the original config's directory is passed and that resolution is preserved without round-tripping any section through disk. Loading stays a pure, side-effect-free composition.

## Secrets stay referenced, never stored

Each section keeps the secret hygiene its engine prescribes: an `api_key_env` naming an environment variable, not a literal key. The committed `config.computer.example.json` and `config.reachy.example.json` are safe to commit and are **templates** — copy one to a git-ignored `*.local.json` and adapt it. A missing env var surfaces as that engine's own error at build time (WICA raises `MissingEnvError` at `Wica.init`; the ASR and TTS modules raise when they resolve their key; the bridge records a voice build failure as `api.synthesizer_error`) — the app catches each separately and reports what to set ([app.md](app.md), "Degraded start").

## Relies on the owners' `from_dict`

This dict-based composition rests on each section's owner exposing a `from_dict` constructor that runs the same validation as its file loader. All do — WICA's `from_dict(data, *, base_dir=None)`, ASR's and TTS's `from_dict(engine_block)`, the bridge's `ReachyMiniConfig.from_dict(data)` — and none reads an env var or a file at construction, so loading succeeds with keys unset. `ReachyMiniConfig.from_dict` does import `reachy_mini` lazily (to validate `robot` keys against upstream's signature); that is a load-time cost of the reachy loader only — the computer loader never imports the bridge.

## Testing

`tests/test_config.py` (shared) and each embodiment's `tests/<embodiment>/test_config.py`:

- `read_config_file`: a missing file, invalid JSON, a non-object document each raise `ValueError` naming the path.
- `load_app_config`: a missing `wica` or `asr`, an `asr` without an `engine` block, an unknown top-level key (given a section set), raise `ValueError` naming the offender; a relative `system_prompt_file` resolves against the file's directory; keys unset do not fail the load.
- `VisionConfig`: absent/`null`/`{}` ⇒ off; `{"enabled": true}` ⇒ on with defaults; each field's type/range rule; an unknown key.

## Open questions

1. **A single shared `api_key_env` per provider.** The sections name their keys independently; whether to allow one top-level key reference shared across sections is deferred.
