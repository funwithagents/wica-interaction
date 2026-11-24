---
code:
  - src/wica_interaction/setup.py
  - src/wica_interaction/fields.py
  - src/wica_interaction/computer/app.py
  - src/wica_interaction/reachy/app.py
  - src/wica_interaction/computer/default/config.json
  - src/wica_interaction/computer/default/computer.md
  - src/wica_interaction/reachy/default/config.json
  - src/wica_interaction/reachy/default/reachy.md
tests:
  - tests/test_setup.py
  - tests/test_fields.py
  - tests/computer/test_app.py
  - tests/reachy/test_app.py
---

# Setup

**Status:** Stable

## Purpose

Each app (`wica-interaction-computer`, `wica-interaction-reachy`) is set up from its page and run from a folder, not from a hand-written config file. The mechanics are app-setup's (`app-setup/specs/app.md`): an app declares its **sections**, each described by a **setup contract**, and gets a model that keeps the folder, checks a config, installs what it misses and prepares what can be prepared, plus two panels to draw. This spec is what the two apps declare: their sections, the contracts of the sections no library owns, their default config and persona, and their folder. How a section becomes a live config is in [configuration.md](configuration.md); the page and the session are in [app.md](app.md) and [session.md](session.md).

## Decided

### Two apps, one declaration each

There is no single app with an embodiment choice: the command is the choice of body, as before ([embodiment.md](embodiment.md), "Not in the protocol"). Each embodiment's `app.py` declares its sections and its app:

```python
# src/wica_interaction/computer/app.py
SECTIONS: tuple[Section, ...]
APP = App(
    name="wica-interaction-computer",
    title="WICA Interaction — Computer",
    host="wica-interaction",
    sections=SECTIONS,
    default_config=DEFAULT_CONFIG,
    autostart=True,
)
```

| App | `name` (and folder) | Sections, in the page's order |
|---|---|---|
| Computer | `wica-interaction-computer` | `wica`, `asr`, `tts`, `audio_processing`, `vision`, `camera` |
| Reachy Mini | `wica-interaction-reachy` | `wica`, `asr`, `reachy`, `vision` |

| Section | Title on the page | Contract | Owner of the document |
|---|---|---|---|
| `wica` | Agent | `wica.setup` | WICA: `{"agent": {…}}` |
| `asr` | Speech recognition | `asr_engine.setup` | asr-engine: its engine block |
| `tts` | Voice | `tts_engine.setup` | tts-engine: its engine block |
| `reachy` | Reachy Mini | `reachy_mini_bridge.setup` | the bridge: its whole config, voice included |
| `vision` | Hand signs | `wica_interaction.setup.VISION` | this app |
| `audio_processing` | Microphone processing | `wica_interaction.setup.AUDIO_PROCESSING` | this app |
| `camera` | Camera | `wica_interaction.setup.CAMERA` | this app |

- **`host` is `wica-interaction`**: the environment is installed from this project, so an install from the page adds one of its extras from its lock ([project.md](project.md), "Extras").
- **`SECTIONS` is public** next to `APP`: a host built on this app (its own distribution, its own lock) composes its own `App` from the same sections.
- **Every section is always used** (no `when`), and app-setup counts a used section missing from `config.json` as a problem: each default config holds every section of its app. A section that can be left at its defaults is still written: every field of `vision`, `audio_processing` and `camera` has a default, and `{}` is a valid document.
- **The body's library is imported directly**, not through a loader function with a `host_extra`: the `computer` extra is required by `wica-interaction-computer` and `reachy` by `wica-interaction-reachy` ([project.md](project.md)), so by the time an `App` exists its library does too.
- **`autostart=True`**: both apps start their session at launch, as they always did; the user's choice lives in the folder's `launch.json` ([session.md](session.md), "Autostart").

### The folder

`app_setup.folder.default_folder(APP.name)`: `~/.config/wica-interaction-computer` and `~/.config/wica-interaction-reachy` (`$XDG_CONFIG_HOME` followed), or the one `--folder` names. One folder is one config; a second config of the same app is a second folder.

| File | Holds | Written by |
|---|---|---|
| `config.json` | The current config: one key per section | app-setup: the default at first launch, then the Setup tab's Save |
| `.env` | The keys | app-setup: the Secrets tab |
| `launch.json` | Whether the session starts at launch | app-setup: the Setup tab's checkbox |
| `computer.md` / `reachy.md` | The persona, to edit in place | this app: copied at first launch, never overwritten |

**Relative paths are the folder's.** The app builds every section with the folder as `base_dir` ([configuration.md](configuration.md), "Loading"), and app-setup passes the same folder to the contracts' checks, so Check, Prepare, Test and a session look at the same files: the persona, a voice file, a scene.

### The defaults ship inside the package

Each embodiment's package has a `default/` folder, installed with it, holding what a new folder starts from:

| File | Role |
|---|---|
| `<embodiment>/default/config.json` | `App.default_config`: the first config of a new folder, and what the Setup tab's Reset goes back to |
| `<embodiment>/default/computer.md`, `…/reachy.md` | The persona the default config names (`"system_prompt_file": "computer.md"`) |

- **`ensure_persona(folder)`**, one per embodiment's `app.py`: copies the persona into the folder when the folder has none under that name. Called at launch, right after the `AppModel` is built. Never overwrites: the file in the folder is the user's.
- **The default configs are the former templates** (`config.computer.example.json`, `config.reachy.example.json`), with: `asr` and `tts` as engine blocks ([configuration.md](configuration.md)); no `api_key_env` naming a `WICA_`-prefixed variable (each library's default variable applies: `HF_TOKEN` for the agent they select); the persona by its bare name. They select the same modules as before: the Hugging Face Hub agent, the local `mlx-audio` recognizer behind the `silero` gate, the `phonon` voice, vision on; the robot's on the simulator.
- **They are the repo's only committed configs** besides the end-to-end tier's: there is no separate examples folder, which would be a second copy to keep in step.
- The root `prompts/` folder and the two root templates no longer exist.

### The app's own contracts

`wica_interaction/setup.py` holds three contract objects, `VISION`, `AUDIO_PROCESSING` and `CAMERA`, each with the seven functions of `app-setup/specs/module-contract.md` over **its own section's document**. They follow the contract's rules as a library's do: plain data out, nothing imported from app-setup at runtime, `config_schema` / `required_extras` / `check_config` working with no extra installed.

**Fields are declared once.** `wica_interaction/fields.py` is the family's field helper (`Field`, `read_fields`, `field_problems`, `fields_schema`), copied from WICA's until a shared runtime package exists; it is standard library only. Each section's fields are declared once, and the same declaration:

- is read by the section's loader (`VisionConfig.from_dict`, `CameraConfig.from_dict`, the audio-processing parser), raising `ValueError` on the first invalid field;
- is checked by `check_config`, which reports every invalid field with the same message;
- emits the section's schema, with each field's description, default and bounds.

So what the form accepts, what `check_config` accepts and what the loader accepts cannot drift. The fields and their rules are unchanged ([configuration.md](configuration.md) for `vision`, [computer-embodiment.md](computer-embodiment.md) for `camera`, [audio-processing.md](audio-processing.md)); the messages take the helper's wording. An unknown key is a problem at its own pointer.

| | `VISION` | `AUDIO_PROCESSING` | `CAMERA` |
|---|---|---|---|
| Schema | `enabled`, `threshold`; `fps`, `max_hands`, `hold_s`, `release_hold_s`, `device` advanced | two optional objects, `echo_cancellation` (`enabled`, `stream_delay_ms`) and `noise_suppression` (`enabled`, `level`) | `device` (an integer or a string) |
| `required_extras` | `wica-interaction[vision]` when `enabled` is true | `wica-interaction[audio-processing]` when either feature is enabled | none |
| `check_setup` | the extra, then the two models (below); nothing when off | the extra, when a feature is on | nothing |
| `prepare` | one model | nothing to prepare: `ValueError` for any item | the same |
| `options` | `[]` | `[]` | `[]` |
| `probe` | loads the detector and the classifier, releases them | builds the WebRTC processor with the configured features, releases it | opens the device, reads one frame, releases it |

- **The extra item** is `extra:wica-interaction[<extra>]`, required, never preparable, found by trying the imports the feature makes (`mediapipe`, `transformers`, `torch`, `cv2` for vision; `aec_audio_processing`, `samplerate` for audio processing). app-setup maps a requirement on the host itself to that extra, so the page's Install runs `--extra vision`.
- **The two vision models**, each a preparable item that is **not required** (a session downloads what is missing at its first start, slower; getting them during setup is better):
  - `model:hand-landmarker`: MediaPipe's hand landmarker bundle, `ready` when the file is in vision-modules' cache (`vision_modules.hand.default_model_path()`); `prepare` downloads it with the library's own `ensure_model`.
  - `model:gesture-classifier`: the HaGRID classifier (`HaGRIDViTClassifier.MODEL_ID`), `ready` when its snapshot is in the Hugging Face cache, looked up without the network; `prepare` downloads the files the classifier loads (its JSON and its weights).
  - Neither download can measure its progress: `prepare` reports its start (`fraction` `None`, what it downloads) and its end (`1.0`).
  - Both are listed only once the extra imports. `unknown` when the cache cannot be read.
- **`probe` never raises**, releases what it built, and is light: no frame is classified, no audio processed. The camera's probe runs only where a webcam can be opened; a device that yields no frame is `ok: false` naming it.
- **No keys**: none of the three sections names an environment variable.

### Conformance and carriers

- **`check_contract`** (app-setup's conformance check) runs over each of the three contracts, with the matching section of both default configs and of the end-to-end configs as examples.
- **Every section of both default configs passes its contract's `check_config`**, the libraries' included: a default config a loader would refuse fails the fast tier.
- **Every requirement has a carrier.** For each section of each app, every requirement its `required_extras` can return (over the default configs, the end-to-end configs and each module of each choice) is met by one of this project's extras (`app_setup.extras.host_extras(requirement, host="wica-interaction")`), with a listed set of exceptions: `reachy-mini-bridge[placo]`, and asr-engine's `kyutai-mlx` / `kyutai-torch` ([project.md](project.md), "Extras": not mirrored). A sibling adding an extra the app does not mirror fails this test until the extra, or the exception, is added.

## Testing

`tests/test_fields.py`: the copied helper's own tests, copied with it. `tests/test_setup.py`: `check_contract` over the three contracts; for each, a bad value reported at its pointer with the loader's message, and the loader refusing the same document; `required_extras` following the switches and never raising on `{}` or a malformed document; `check_setup` with the imports and the caches replaced: the extra missing, then installed with both models missing (not required), one prepared alone, then ready; `prepare` refusing an unknown item; `probe` over fakes returning `ok` and its message, and `ok: false` instead of raising. The carrier test above. `tests/computer/test_app.py`, `tests/reachy/test_app.py`: the app's sections and titles; its default config passing every section's `check_config`; a new folder getting the default config and the persona, an edited persona left alone at the next launch; `used_config` of the default holding exactly the app's sections.

## Open questions

1. **One app, the embodiment a choice in the config.** Deferred until both apps run this way; it needs `when` on the embodiment's sections, a loader with `host_extra` for the body's library, and a persona per embodiment.
2. **A shared home for `fields.py`.** A copy per repository until a runtime package the libraries and this app can all depend on exists.
3. **Lists for the form.** `options` returns nothing: the webcams and the torch devices of the machine could be offered for `camera.device` and `vision.device`.
4. **The Hugging Face sign-in as a key source.** A machine signed in with `hf auth login` has a token the `HF_TOKEN` item does not see.
