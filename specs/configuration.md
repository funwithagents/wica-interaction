---
code:
  - src/reachy_mini_interaction_wica/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Purpose

The whole app is stood up from **one JSON config file** that carries the sub-configurations for the pieces it wires together: `wica` (the agent), `asr` (speech in), `reachy` (the whole robot backend — including the voice), and optionally `vision` (the hand-sign input over the robot camera). Switching provider, voice, ASR backend, robot backend, or persona is a file edit, not a code change. This spec defines that file's shape and how each section becomes a live config, **reusing each engine's own loader and validation** rather than re-implementing it here.

## Each engine owns its config schema

Each sibling engine already defines and validates its own config, and we do **not** duplicate or fork those schemas:

- **WICA** — [`WicaConfig.from_dict(data, base_dir=…)`](../../wica-framework/specs/config.md) parses an `{"agent": {...}}` document (provider/model/key/`system_prompt_file`/`model_kwargs`).
- **ASR** — [`ASREngineConfig.from_dict(engine_block)`](../../asr-engine/specs/configuration.md) builds the ASR engine config from an `engine` block.
- **`reachy`** — [`ReachyMiniConfig.from_dict(...)`](../../reachy-mini-bridge/specs/config.md) builds the bridge's own declarative config: backend, upstream `ReachyMini(...)` kwargs, the daemon lifecycle, the voice (`tts`, a tts-engine `engine` block), and the XVF3800 audio profile. The bridge has its own loader now, so this section is delegated exactly like `wica` and `asr` — it is **not** a special case any more.

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
  "reachy": {
    "backend": "real",
    "tts": {
      "module": { "type": "pocket", "voice": "george", "device": "auto" }
    }
  }
}
```

Nesting each engine's native document verbatim (rather than flattening the keys) is deliberate: it keeps this file a thin *composition* of schemas the engines already own, so an engine adding or renaming a field needs no change here, and a reader can follow each section straight to that engine's config spec.

### The `reachy` section is a verbatim bridge config

```json
"reachy": {
  "backend": "sim",
  "robot": {},
  "daemon": { "spawn": "auto" },
  "tts": { "module": { "type": "pocket", "voice": "george", "device": "auto" } },
  "audio": { "xvf3800": null }
}
```

The whole section is handed, verbatim, to `ReachyMiniConfig.from_dict` — every field, default, and validation rule is the bridge's own (see [reachy-mini-bridge specs/config.md](../../reachy-mini-bridge/specs/config.md)):

- `backend` — `"real"` (default), `"sim"` (upstream MuJoCo mockup), or `"fake"` (the bridge's in-package stand-in — no daemon, no hardware; the offline/dev/test path).
- `robot` — upstream `ReachyMini(...)` kwargs, forwarded verbatim (validated against upstream's own signature).
- `daemon` — how the bridge brings up the daemon it talks to: `spawn` (`never` / `auto` / `always`), `headless`, `scene`, `preload_datasets`, `startup_timeout`. `spawn: "auto"` on a `sim` backend spawns (or borrows) a local headless MuJoCo daemon — the offline/dev path gains this lifecycle for free; on `real` it spawns the hardware daemon of a robot attached over USB (a wireless robot runs its own daemon, so its config leaves `spawn` at `"never"`). Only `fake` has no daemon, so `spawn` other than `"never"` is rejected there.
- `tts` — the voice: a tts-engine `engine` block (`module` matters; a `player` block is accepted but ignored, since playback is the robot's, not a local device). The provider named by `module.type` must have its **bridge extra** installed — `reachy-mini-bridge[tts-pocket]` / `[tts-elevenlabs]`, see [project.md](project.md) — and a missing extra is a voice *build* failure, degrading exactly like an unset key. The committed default is `pocket` (the local model, voice `george`): no key, no network at synthesis time, weights downloaded on first run. Absent ⇒ no voice (`say` unavailable, everything else unaffected — see [app.md](app.md), "No credentials, no robot").
- `audio.xvf3800` — the tuned XVF3800 profile applied on media-session start; `null`/absent keeps the firmware defaults.

Absent `reachy` section ⇒ `ReachyMiniConfig()` (the `real` backend, upstream defaults, no voice). Validation errors (an unknown backend, an unknown key, `daemon.spawn` set on the `fake` backend, a malformed block) raise the bridge's `ConfigError`, a `ValueError` subclass — so the documented "`load_config` raises `ValueError`" error surface holds without wrapping.

### The `vision` section — the app's own small schema

```json
"vision": { "enabled": false, "threshold": 0.55, "fps": 5, "max_hands": 2, "device": null }
```

Enables the hand-sign input ([gesture-input.md](gesture-input.md)). Unlike the other sections, this one is **not** delegated to a sibling loader: `vision-modules` is a library with constructor kwargs and no config document of its own, so the app owns this schema in `config.py` as a frozen `VisionConfig(enabled, threshold, fps, max_hands, device)` with a `from_dict(data, path=…)` that validates it:

- `enabled` — the switch; a boolean, default **`false`**. The section can stay in the file, tuned, with vision off; turning it on is a one-key edit.
- `threshold` — the gesture classifier's validation threshold (a hand's label is its top class only at or above it); a number in `[0, 1]`, default **`0.55`**.
- `fps` — one rate for the camera poll, the hand stage, and the classifier; a number `> 0`, default **`5`**.
- `max_hands` — how many hands the hand stage detects, publishes, and the classifier classifies per frame (each gets its own `hand_gesture_<n>` World entry); an integer `>= 1` (not a bool, not a float), default **`2`**, the library's default — see [gesture-input.md](gesture-input.md), "The pipeline", for why 2 and when to drop to 1.
- `device` — the classifier's torch device (`"mps"`, `"cuda"`, `"cpu"`); `null`/absent lets the library pick (`mps` → `cuda` → `cpu`).

**Vision is off unless `enabled` is `true`** — an absent section, `null`, `{}`, or `enabled: false` all mean: no camera is polled, no model is downloaded, and no `hand_gesture_<n>` entry is registered. It is an explicit opt-in because its first run downloads ~340 MB of models. `{"enabled": true}` ⇒ on, with the other defaults. Not an object, an unknown key, or an out-of-range value raises `ValueError` from `load_config`, naming the file and the field, like every other section.

## Loading: `load_config`

```python
@dataclass
class AppConfig:
    wica: WicaConfig
    asr_engine: ASREngineConfig
    reachy: ReachyMiniConfig


def load_config(path: str) -> AppConfig: ...
```

`load_config`:

1. Reads and JSON-parses the file at `path`; raises a clear `ValueError` if it is missing, is not a JSON object, or lacks either required section (`wica`, `asr`).
2. For each section, **delegates to that engine's own `from_dict`** so all field-level validation and defaults come from the engine, not from us — passing the in-memory block directly, no temp files:
   - `wica` → `WicaConfig.from_dict(cfg["wica"], base_dir=os.path.dirname(path))`
   - `asr` → `ASREngineConfig.from_dict(cfg["asr"]["engine"])`
   - `reachy` → `ReachyMiniConfig.from_dict(cfg.get("reachy", {}))` (absent ⇒ the bridge's own defaults)
3. Returns `AppConfig(wica=…, asr_engine=…, reachy=…)`.

**Why `base_dir`, not a temp file:** the one thing a path-based loader gave us for free was WICA resolving `agent.system_prompt_file` *relative to the config file's directory*. `WicaConfig.from_dict` takes an optional `base_dir`, so we pass the original config's directory and that relative-path resolution is preserved without round-tripping any section through disk. This keeps `load_config` a pure, side-effect-free composition — read one file, hand blocks to validated constructors.

## Secrets stay referenced, never stored

Each section keeps the same secret hygiene its engine already prescribes: an `api_key_env` naming an environment variable, not a literal key. The composed file is safe to commit; a literal-key variant lives in a git-ignored `*.local.json` copy. A missing env var surfaces as that engine's own error at build time (WICA raises `MissingEnvError` at `Wica.init`; the ASR module raises when it resolves its key) — the app catches each one separately and reports what to set (see [app.md](app.md), "No credentials, no robot"). The voice is different: the bridge builds it from `reachy.tts` at `ReachyMiniApi` construction, and a build failure (typically the key unset) does **not** raise — it degrades to "no voice", exposed as `api.synthesizer_error`, which the app reads and reports on the `TTS:` axis; the robot itself still comes up. The robot backend needs no key; a `real` backend simply fails to connect if no daemon is reachable, which the app reports the same way.

## Relies on the engines' `from_dict`

This spec's dict-based composition rests on each section exposing a `from_dict` constructor that runs the same validation as its file loader. All three exist — WICA's `from_dict(data, *, base_dir=None)`, ASR's `from_dict(engine_block)`, and the bridge's `ReachyMiniConfig.from_dict(data)` (part of its `from_dict` / `from_json` / `from_json_file` trio) — and none reads an env var or a file at construction, so `load_config` here stays side-effect-free and succeeds with keys unset. `ReachyMiniConfig.from_dict` does import `reachy_mini` (lazily, to validate `robot` keys against upstream's `ReachyMini` signature — about 0.7 s cold); this is a load-time cost, not a side effect, and the app already imports `reachy_mini` on startup regardless.

## Open questions

1. **A single shared `api_key_env` per provider.** The sections name their keys independently; whether to allow one top-level key reference shared across sections is deferred.
