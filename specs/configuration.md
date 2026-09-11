---
code:
  - src/reachy_mini_interaction_wica/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Purpose

The whole app is stood up from **one JSON config file** that carries the sub-configurations for the pieces it wires together: `wica` (the agent), `asr` (speech in), `tts` (the voice's synthesizer), and `reachy` (which robot backend to drive). Switching provider, voice, ASR backend, robot backend, or persona is a file edit, not a code change. This spec defines that file's shape and how each section becomes a live config, **reusing each engine's own loader and validation** rather than re-implementing it here.

## Each engine owns its config schema

Each sibling engine already defines and validates its own config, and we do **not** duplicate or fork those schemas:

- **WICA** — [`WicaConfig.from_dict(data, base_dir=…)`](../../wica-framework/specs/config.md) parses an `{"agent": {...}}` document (provider/model/key/`system_prompt_file`/`model_kwargs`).
- **ASR** — [`ASREngineConfig.from_dict(engine_block)`](../../asr-engine/specs/configuration.md) builds the ASR engine config from an `engine` block.
- **TTS** — [`TTSEngineConfig.from_dict(engine_block)`](../../tts-engine/specs/configuration.md) builds the TTS engine config from an `engine` block. Here it configures the **bridge's synthesizer**, not a local player (see below).

`reachy` is the one section that is **not** an engine with its own loader — it selects the robot backend the app hands to `reachy-mini-bridge`'s `ReachyMiniApi`, so it is parsed **here** into a small app-owned `ReachyConfig`.

## The composed file

```json
{
  "wica": {
    "agent": {
      "provider": "anthropic",
      "model": "claude-sonnet-5",
      "api_key_env": "WICA_ANTHROPIC_API_KEY",
      "system_prompt_file": "prompts/assistant.md"
    }
  },
  "asr": {
    "engine": {
      "module": { "type": "deepgram_v2", "api_key_env": "DEEPGRAM_API_KEY", "model": "flux-general-en" },
      "audio": { "device": null },
      "dictation_default_segmentation_mode": "trigger_word"
    }
  },
  "tts": {
    "engine": {
      "module": { "type": "elevenlabs", "api_key_env": "ELEVENLABS_API_KEY", "voice_id": "..." }
    }
  },
  "reachy": { "backend": "real" }
}
```

Nesting each engine's native document verbatim (rather than flattening the keys) is deliberate: it keeps this file a thin *composition* of schemas the engines already own, so an engine adding or renaming a field needs no change here, and a reader can follow each section straight to that engine's config spec.

### The `tts` section configures the bridge's synthesizer, not a local player

On Reachy Mini, TTS must play **through the robot** for echo cancellation to work ([reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md)). So the `tts` section no longer stands up a local `TTSEngine` with a speaker; it configures the bridge's default `TTSEngineSynthesizer` (`reachy_mini_bridge.audio.TTSEngineSynthesizer`), which the bridge's `say` resamples and pushes to the robot speaker. Concretely the app builds `TTSEngineSynthesizer(cfg.tts_engine)` and passes it to `ReachyMiniApi(backend, synthesizer=…)`. Only the **`module`** block matters (which voice/provider synthesizes the speech); a `player` block is accepted if present but **ignored**, because playback is the robot's, not a local device — the same "accepted but ignored" treatment the ASR/TTS `server` blocks already get (this app runs no MCP transports).

### The `reachy` section

```json
"reachy": { "backend": "real" }
```

- `backend` — one of `"real"` (default), `"sim"` (upstream MuJoCo mockup), or `"fake"` (the bridge's in-package stand-in — no daemon, no hardware; the offline/dev/test path). Validated against that set, raising a clear `ValueError` otherwise.
- Absent-safe: no `reachy` key ⇒ `backend="real"`. (More knobs — a tuned XVF3800 `audio_config` profile, head-tracking weight — may join later; kept minimal in v1.)

## Loading: `load_config`

```python
@dataclass
class ReachyConfig:
    backend: str = "real"


@dataclass
class AppConfig:
    wica: WicaConfig
    tts_engine: TTSEngineConfig
    asr_engine: ASREngineConfig
    reachy: ReachyConfig


def load_config(path: str) -> AppConfig: ...
```

`load_config`:

1. Reads and JSON-parses the file at `path`; raises a clear `ValueError` if it is missing, is not a JSON object, or lacks any of the three required engine sections (`wica`, `asr`, `tts`).
2. For each engine section, **delegates to that engine's own `from_dict`** so all field-level validation and defaults come from the engine, not from us — passing the in-memory block directly, no temp files:
   - `wica` → `WicaConfig.from_dict(cfg["wica"], base_dir=os.path.dirname(path))`
   - `asr` → `ASREngineConfig.from_dict(cfg["asr"]["engine"])`
   - `tts` → `TTSEngineConfig.from_dict(cfg["tts"]["engine"])`
3. Parses the optional `reachy` section into `ReachyConfig` (absent ⇒ `backend="real"`).
4. Returns `AppConfig(wica=…, tts_engine=…, asr_engine=…, reachy=…)`.

**Why `base_dir`, not a temp file:** the one thing a path-based loader gave us for free was WICA resolving `agent.system_prompt_file` *relative to the config file's directory*. `WicaConfig.from_dict` takes an optional `base_dir`, so we pass the original config's directory and that relative-path resolution is preserved without round-tripping any section through disk. This keeps `load_config` a pure, side-effect-free composition — read one file, hand blocks to validated constructors.

## Secrets stay referenced, never stored

Each section keeps the same secret hygiene its engine already prescribes: an `api_key_env` naming an environment variable, not a literal key. The composed file is safe to commit; a literal-key variant lives in a git-ignored `*.local.json` copy. A missing env var surfaces as that engine's own error at build time (WICA raises `MissingEnvError` at `Wica.init`; the ASR module and the bridge's synthesizer raise when they resolve their key) — the app catches it and reports what to set (see [app.md](app.md), "No credentials"). The robot backend needs no key; a `real` backend simply fails to connect if no daemon is reachable, which the app reports the same way.

## Relies on the engines' `from_dict`

This spec's dict-based composition rests on each engine exposing a `from_dict` constructor that runs the same validation as its file loader. All three exist — WICA's `from_dict(data, *, base_dir=None)`, ASR's `from_dict(engine_block)`, TTS's `from_dict(engine_block)` — and none reads an env var or a file at construction, so `load_config` here stays side-effect-free and succeeds with keys unset.

## Open questions

1. **A single shared `api_key_env` per provider.** The sections name their keys independently; whether to allow one top-level key reference shared across sections is deferred.
2. **Robot `audio_config` profile in the file.** Whether the tuned XVF3800 profile the bridge can apply is exposed as a `reachy.audio_config` block, or left to the bridge's shipped default, is deferred (see [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio.md), open questions).
