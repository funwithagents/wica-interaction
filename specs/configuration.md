---
code:
  - src/wica_interaction/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Updated

## Purpose

Each app is stood up from **one config**, the `config.json` of its folder ([setup.md](setup.md), "The folder") or a config file of the same shape (`--config`, [setup.md](setup.md), "Three launches"), carrying the sub-configurations of the pieces it wires together: `wica` (the agent), `asr` (speech in), `vision` (the hand-sign input), plus the sections of the app's **embodiment** — `tts` / `audio_processing` / `camera` for the computer, `reachy` for the robot. Switching provider, voice, ASR backend, robot backend, camera or persona is a change of that config, made from the Setup tab or by hand, not a code change. This spec defines the shared part of that config and how a section becomes a live config, **reusing each engine's own loader and validation** rather than re-implementing it; the embodiment sections are specified in [computer-embodiment.md](computer-embodiment.md) and [reachy-embodiment.md](reachy-embodiment.md). Which sections an app has, how each is described to the page, and the default config are in [setup.md](setup.md).

## Each engine owns its config schema

Each sibling engine already defines and validates its own config, and we do **not** duplicate or fork those schemas:

- **WICA** — `WicaConfig.from_dict(data, base_dir=…)` (`wica-framework/specs/config.md`) parses an `{"agent": {...}}` document (provider/model/key variable/`system_prompt_file`/`model_kwargs`).
- **ASR** — `ASREngineConfig.from_dict(engine_block, base_dir=…)` (`asr-engine/specs/configuration.md`) builds the ASR engine config from its engine block.
- **TTS** (computer) — `TTSEngineConfig.from_dict(engine_block, base_dir=…)` (`tts-engine/specs/configuration.md`).
- **`reachy`** — `ReachyMiniConfig.from_dict(data, base_dir=…)` (`reachy-mini-bridge/specs/core/config.md`), the bridge's own declarative config, voice included.

Nesting each engine's native document verbatim (rather than flattening the keys) keeps the config a thin *composition* of schemas the engines already own: an engine adding or renaming a field needs no change here, and a reader can follow each section straight to that engine's config spec.

**A section holds exactly the document its setup contract describes** ([setup.md](setup.md)): the same object goes to the contract's checks from the Setup tab and to the loader at Start. That is why `asr` and `tts` are the engine block itself, with no `engine` key around it (until 2026-11-24 each was `{"engine": {…}}`, the shape of the engines' server configs): `asr_engine.setup` and `tts_engine.setup` describe the engine block, as the bridge's own `tts` block always was.

## The shared sections

```json
{
  "wica": {
    "agent": {
      "provider": "anthropic",
      "model": "claude-sonnet-5",
      "system_prompt_file": "reachy.md"
    }
  },
  "asr": {
    "module": { "type": "deepgram-v2", "model": "flux-general-en" },
    "audio_source": { "type": "custom" },
    "dictation_default_segmentation_mode": "trigger_word",
    "vad": { "type": "silero", "min_silence_duration_ms": 1500 }
  },
  "vision": { "threshold": 0.55, "fps": 10, "max_hands": 2, "hold_s": 0.3, "release_hold_s": 0.5, "device": null }
}
```

- `wica` is **required**; `asr` and `vision` are **optional** — absent means off ("Optional sections" below).
- `asr.vad` is asr-engine's optional voice-activity gate (`asr-engine/specs/vad.md`), part of the engine block and so parsed by its loader, not by the app. Both default configs turn it on (`silero`, the `asr-silero` extra): the recognizer they select, `mlx-audio`, is a batch one with no end-of-utterance signal of its own, so the gate's silence closes each utterance; in front of a hosted recognizer it also keeps the room's silence from being billed. It wraps the source the embodiment injects as it would the engine's own capture ([asr-bridge.md](asr-bridge.md)). `min_silence_duration_ms` is `1500` rather than the default `500`, asr-engine's setting for `deepgram-v2`: Flux hears enough real silence to end the turn itself, and the gate removes only the long idle stretches. The end-to-end configs stay ungated ([e2e.md](e2e.md)).
- `wica.agent.system_prompt_file` names the persona — `computer.md` or `reachy.md`, the file the app copied into its folder ([setup.md](setup.md), "The defaults ship inside the package") — resolved relative to the folder.
- `asr.audio_source` says where the audio comes from. The computer's config names its microphone, `{"type": "input-device", "device": …}`; the robot's says `{"type": "custom"}` (the mic is the robot's, supplied by the embodiment), as in the excerpt above. Both bodies' own sources produce 16 kHz mono linear16 and declare it, and the engine converts for a module fed another format, so the module is not constrained by the mic ([asr-bridge.md](asr-bridge.md), "The mic is the embodiment's").

### The `vision` section — the app's own small schema

Enables the hand-sign input ([gesture-input.md](gesture-input.md)) over whichever camera the embodiment provides. Unlike the engine sections it is **not** delegated to a sibling loader: `vision-modules` is a library with constructor kwargs and no config document of its own, so the app owns this schema in `config.py` as a frozen `VisionConfig(threshold, fps, max_hands, hold_s, release_hold_s, device)` with a `from_dict(data)` that validates it, from the same field declarations its setup contract draws the form and checks a draft with ([setup.md](setup.md), "The app's own contracts"):

- `threshold` — the gesture classifier's validation threshold; a number in `[0, 1]`, default **`0.55`**.
- `fps` — one rate for the camera poll, the hand stage and the classifier; a number `> 0`, default **`10`** (the robot's camera delivers 10 fps on a local daemon; `5` until 2026-11-18).
- `max_hands` — how many hands are detected, published and classified per frame, and so how many `hand_gesture_<n>` entries exist; an integer `>= 1` (not a bool, not a float), default **`2`** ([gesture-input.md](gesture-input.md), "The pipeline").
- `hold_s` — how long a hand's **sign** must be seen, unbroken, before it is delivered to the World (the hold filter — [gesture-input.md](gesture-input.md), "The hold filter"); a number `>= 0`, default **`0.3`**; `0` delivers on the first frame.
- `release_hold_s` — how long a hand must show **no sign**, unbroken, before its sign is delivered as gone (the same filter's other direction); a number `>= 0`, default **`0.5`**; `0` clears it on the first frame.
- `device` — the classifier's torch device (`"mps"`, `"cuda"`, `"cpu"`); `null`/absent lets the library pick.

**The section is the switch** ("Optional sections"): absent or `null` means no camera is read for hand signs, no model downloaded, no `hand_gesture_<n>` entry registered; present, `{}` included, means on. Hand signs need the `vision` extra and ~340 MB of models (both shown, installed and downloaded from the Setup tab). Not an object, an unknown key, an out-of-range value, or the retired `enabled` raises `ValueError` from the loader, naming the section and the field. Which camera it reads is the embodiment's business: the computer's `camera` section, the robot's own camera.

## Optional sections

A feature the user can leave out is an **optional section**, and its presence is its switch: **absent from the config (or `null`) means off; present, whatever it holds (`{}` included), means on.** No section carries an `enabled` field of its own for this.

| Section | App | Absent means | Its starting document |
|---|---|---|---|
| `asr` | both | no speech input: no ASR engine, no bridge, no dictation Commands, no banner line (off is a choice, not something missing); the session runs, the agent driven by typed text and hand signs; `speech_input` is still registered, for the typed hatch | the app's default config's `asr` |
| `vision` | both | no hand signs ([gesture-input.md](gesture-input.md)) | `{}` (every field at its default) |
| `audio_processing` | computer | no microphone processing ([audio-processing.md](audio-processing.md)) | echo cancellation on (`stream_delay_ms` 50), noise suppression off, level 2 — what the default config holds |
| `camera` | computer | no webcam ([computer-embodiment.md](computer-embodiment.md)) | `{"device": 0}` |

`wica`, the computer's `tts` and the robot's `reachy` stay required.

- **The starting document** is what the Setup tab writes into the draft when the user adds the section (app-setup's `Section(optional=True, default=…)`): the app's (or the library's) sensible starting point. The app-owned ones are constants next to their contracts in `setup.py` (`VISION_START`, `AUDIO_PROCESSING_START`, `CAMERA_START`); each app module gathers its optional sections' in `STARTING_DOCUMENTS` (`asr` taken from its default config). Each one loads and passes its contract's `check_config` as it is.
- **The default configs keep each app's first launch as it was**: a section on at first launch is present in the default config (speech input, hand signs, the computer's echo cancellation and webcam), one off is absent. An optional section the app wants on at first launch is simply in its default config.
- **A rule across sections is the app's**: app-setup checks one section at a time. The one rule today is the computer's — hand signs read the webcam, so `vision` present with `camera` absent is a problem ([computer-embodiment.md](computer-embodiment.md), "Hand signs need the camera"); it is checked over the whole document, by the computer's loader and by Start ([session.md](session.md), "Start").
- **`vision.enabled` is gone.** Until the section became the switch, `vision` had an `enabled` field (off by default). A document still holding it is **refused**, by the loader and by the contract's `check_config` (at `/enabled`), with a message saying to remove it — present is on, absent is off — rather than ignored: an ignored `enabled: false` would silently turn hand signs on.

## Loading

```python
@dataclass
class AppConfig:
    """The shared sections, what the lifecycle (system.py) is built from."""

    wica: WicaConfig
    asr_engine: ASREngineConfig | None = None  # None: no `asr` section, speech input off
    vision: VisionConfig | None = None  # None: no `vision` section, hand signs off


def load_app_config(
    data: Mapping[str, Any], *, base_dir: Path, sections: frozenset[str]
) -> AppConfig: ...
```

The config reaches the loaders as a document, not as a path: at Start the app hands them `AppModel.used_config(saved)` — the sections the app declares, as saved in the folder or the file — and `model.path` ([session.md](session.md), "Start"). Reading and writing the config is app-setup's (`ConfigFolder`, `ConfigFile`); nothing here opens a file.

1. **`load_app_config(data, base_dir=…, sections=…)`** validates the shared part: the required `wica` key is present; **no top-level key outside `sections`** (the shared names plus the calling embodiment's own) — an unknown section is a `ValueError` naming it, so a robot's config handed to the computer's loader fails on `'reachy'` rather than being silently ignored (`used_config` never returns one; a caller with a document of its own, the end-to-end tier, can); then delegates each section to its owner's `from_dict`, the in-memory block passed directly:
   - `wica` → `WicaConfig.from_dict(data["wica"], base_dir=base_dir)`
   - `asr` → `ASREngineConfig.from_dict(data["asr"], base_dir=base_dir)` when present and not `null` (a section that is not an object is a `ValueError`), else `None`
   - `vision` → `VisionConfig.from_dict(data["vision"])` when present and not `null`, else `None`
2. **Each embodiment loads its own.** `computer/config.py` and `reachy/config.py` each expose `load_config(data, *, base_dir) -> ComputerConfig | ReachyConfig`: call `load_app_config` with its section set, parse its own sections into the same object (`.app` holds the `AppConfig`), each with the same `base_dir`.

**`base_dir` is the app's folder, for every section** — or, from a config file, the file's directory. A relative path in any section — the persona, a voice file, a model path, an audio file, a scene — resolves against the directory the config lives in, by each owner's own loader. app-setup gives the setup contracts the same folder, so what the Setup tab checked, prepared and tested is what a session opens.

**One error type.** Everything a loader can raise for a bad document is a `ValueError`, so a caller has a single thing to catch ([session.md](session.md), "Start"). This falls out for free: WICA's, asr-engine's, tts-engine's and the bridge's `ConfigError` all subclass `ValueError`, and the app's own `VisionConfig`/`CameraConfig`/`AudioProcessingConfig` parsers raise it too. Field-level errors propagate unchanged, keeping the owner's message; nothing is re-wrapped or swallowed. An error names the section and the field (`config: vision 'fps' must be …`), not a file.

**The loader and the Setup tab agree.** A config with no problem under `AppModel.check_config` loads: each library's `check_config` and its `from_dict` run the same rules, and the app's own sections are read and checked from one declaration ([setup.md](setup.md)).

## Keys are named, never stored

**A config never holds a key.** Every engine's section names an environment variable in `api_key_env`, which defaults to the provider's usual one (`ANTHROPIC_API_KEY`, `HF_TOKEN`, `DEEPGRAM_API_KEY`, `GRADIUM_API_KEY`, …); a config carrying `api_key` is refused by its owner's loader. The default configs name no variable, so the provider's own applies. The value comes from the environment the app was launched from or from the `.env` next to the config — the folder's, or a config file's directory (`--config`), unless `--env` names another ([setup.md](setup.md), "Three launches") — which app-setup loads into the process environment when the app starts and keeps in step with the Secrets tab (the launch environment wins: `app-setup/specs/secrets.md`); the engines never read the file themselves.

A key that is not set is known before a session starts: it is a missing required item of its section, shown on the Setup tab with a field to set it, and a blocker of Start ([session.md](session.md)). Past that check, a provider that rejects a key surfaces as that engine's own error at build time (WICA raises `MissingSecretError` when no key is under the variable; the ASR and TTS modules raise theirs; the bridge records a voice build failure as `api.synthesizer_error`) — the app catches each separately and reports it ([app.md](app.md), "Degraded start").

## Relies on the owners' `from_dict`

This composition rests on each section's owner exposing a `from_dict` constructor that runs the same validation as its file loader and takes `base_dir`. All do, and none reads an environment variable or a file at construction, so loading succeeds with keys unset. `ReachyMiniConfig.from_dict` does import `reachy_mini` lazily (to validate `robot` keys against upstream's signature); that is a load-time cost of the robot's loader only — the computer's never imports the bridge.

## Testing

`tests/test_config.py` (shared) and each embodiment's `tests/<embodiment>/test_config.py`:

- `load_app_config`: a missing or `null` `wica`, an `asr` that is not an object, an unknown top-level key (given a section set), raise `ValueError` naming the offender; a relative `system_prompt_file` resolves against `base_dir`, whatever the working directory; keys unset do not fail the load; an absent or `null` `asr` loads as `None`; an `asr` still wrapped in `engine` is refused by asr-engine's loader (an unknown key).
- `VisionConfig`: absent/`null` ⇒ off; `{}` ⇒ on with defaults (`fps` 10, `hold_s` 0.3 and `release_hold_s` 0.5 among them); each field's type/range rule (a negative or non-numeric `hold_s` or `release_hold_s` included); an unknown key; `enabled` (true or false) refused with the message saying to remove it.
- Each default config ([setup.md](setup.md)) loads through its embodiment's `load_config` against a folder holding its persona, its optional sections present as they were on at first launch; each app's config with only its required sections loads with every optional one `None`; each starting document loads as its section (`tests/<embodiment>/test_config.py`) and passes its contract (`tests/test_setup.py`).

## Open questions

1. **A single shared key variable per provider.** The sections name their keys independently; whether to allow one reference shared across sections is deferred. In practice two sections on one provider default to the same variable.
