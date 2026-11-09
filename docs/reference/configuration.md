# Configuration reference

The whole app is stood up from **one JSON file**: the sections every embodiment shares (`wica`, `asr`, `vision`) plus the sections of the body the file is for (`tts`, `audio_processing`, `camera` for the computer; `reachy` for the robot). Switching the model, the voice, the speech recognizer, the robot backend, the camera or the persona is a file edit. This page is the file section by section; the design is [specs/configuration.md](../../specs/configuration.md).

Each engine section is **that engine's own document, nested verbatim**: the `wica` section is a WICA config, `asr.engine` an asr-engine engine block, `tts.engine` a tts-engine engine block, `reachy` a reachy-mini-bridge config. The app hands each to its owner's loader, so every field, default and validation rule is the engine's, and this page summarizes the fields you will touch and links to the engine's own reference for the rest. The two sections the app owns, `vision` and the computer's `audio_processing` / `camera`, are complete here.

## One file per embodiment

| Command | Template | Sections |
|---|---|---|
| `wica-interaction-computer` | [config.computer.example.json](../../config.computer.example.json) | `wica`, `asr`, `vision`, `tts`, `audio_processing`, `camera` |
| `wica-interaction-reachy` | [config.reachy.example.json](../../config.reachy.example.json) | `wica`, `asr`, `vision`, `reachy` |

`--config` is required and has no default. The committed files are **templates**: copy one next to them (a git-ignored `*.local.json`, say), fill in what you have, and point the command at your copy. The persona path inside (`system_prompt_file`) resolves relative to the config file's directory, so a copy in the repo root keeps working.

Which body runs is decided by the command, not by the file: each command accepts its own sections only, so a reachy file handed to the computer command is rejected on its `reachy` key rather than silently ignored ([What a loader rejects](#what-a-loader-rejects)).

## `wica`: the agent

```json
"wica": {
  "agent": {
    "provider": "anthropic",
    "model": "claude-sonnet-5",
    "api_key_env": "WICA_ANTHROPIC_API_KEY",
    "system_prompt_file": "prompts/computer.md"
  }
}
```

A WICA config, `{"agent": {...}}`, handed to `WicaConfig.from_dict`. The fields you will set:

| Field | What it does |
|---|---|
| `provider` | `anthropic`, `openai`, `google_genai`, `mistralai` or `huggingface-hub`; the matching `wica-*` extra must be installed ([install.md](install.md#extras)) |
| `model` | the model id (the Hub `repo_id` for `huggingface-hub`, with `hf_provider` naming the backend that serves it) |
| `api_key_env` | the environment variable holding the key, read when the agent is built; never a literal key in a committed file |
| `system_prompt_file` | the persona, relative to the config file: `prompts/computer.md` or `prompts/reachy.md` ([guides/personas.md](../guides/personas.md)) |
| `model_kwargs` | forwarded to the provider's model constructor: `temperature`, Gemini's `thinking_level` |
| `context_window`, `history_budget`, `history_reactions` | bound the prompt's history; the reachy template keeps the prompt under half a 128k window |

Every field, the providers and their notes (Gemini thinks by default, Mistral's free tier) are in WICA's [configuration reference](https://github.com/funwithagents/wica-framework/blob/main/docs/reference/configuration.md). Two things the app does with this section: it resolves the persona itself and appends the sections the body and the hand-sign input need before WICA sees it ([agent.md](agent.md#the-persona-what-is-appended-at-startup)), and the `say` output Command is the body's voice, set at wiring, not a config field.

## `asr`: speech in

```json
"asr": {
  "engine": {
    "module": { "type": "deepgram_v2", "api_key_env": "DEEPGRAM_API_KEY", "model": "flux-general-en" },
    "audio": { "device": null },
    "dictation_default_segmentation_mode": "timeout"
  }
}
```

The `engine` block is an asr-engine engine block, handed to `ASREngineConfig.from_dict`. The fields that matter here:

| Field | What it does |
|---|---|
| `module.type` | the speech recognizer: `deepgram_v2` (Deepgram's Flux models, turn-based) or `deepgram_v1` (Nova models); the `asr-deepgram` extra. Other fields of `module` are the module's own: `api_key_env`, `model`, `language` |
| `audio.device` | **the computer's microphone** (a name, an index, or `null` for the system default). The robot ignores it: the mic is the robot's |
| `dictation_default_segmentation_mode` | how a dictated passage ends: `timeout` (a pause) or `trigger_word` (a word from `segmentation.trigger_words`) ([guides/dictation.md](../guides/dictation.md)) |
| `segmentation.trigger_words`, `segmentation.end_of_speech_timeout_s`, `segmentation.initial_silence_timeout_s` | the words and the silences the two dictation modes use |

The module must deliver **16 kHz mono linear16**, the default: that is the robot's mic rate and the echo canceller's working rate. `auto_start` and `auto_start_dictation` are not honored: the app starts the engine itself, in conversation mode. The complete schema, the modes and the default trigger words are in asr-engine's [configuration spec](https://github.com/funwithagents/asr-engine/blob/main/specs/configuration.md); the Kyutai local modules are not offered here ([install.md](install.md#extras)).

## `vision`: hand signs

```json
"vision": { "enabled": true, "threshold": 0.55, "fps": 5, "max_hands": 2, "hold_s": 0.5, "device": null }
```

The hand-sign input over whichever camera the body has ([guides/hand-signs.md](../guides/hand-signs.md)). Optional, and **off unless `enabled` is `true`**: an absent section, `null`, `{}` or `enabled: false` opens no camera, downloads no model and registers no hand entry. The app owns this schema.

| Field | Default | What it does |
|---|---|---|
| `enabled` | `false` | the switch |
| `threshold` | `0.55` | the classifier's confidence a label needs to count, in `[0, 1]` |
| `fps` | `5` | one rate for the camera poll (where the body paces it), the hand stage and the classifier |
| `max_hands` | `2` | how many hands are detected and classified per frame, and so how many `hand_gesture_<n>` entries the agent has; an integer `>= 1` |
| `hold_s` | `0.5` | how long a hand's sign must be seen, unbroken, before it reaches the agent; `0` delivers on the first frame |
| `device` | `null` | the classifier's torch device (`mps`, `cuda`, `cpu`); `null` lets the library pick |

The camera itself is the body's: the computer's `camera` section below, the robot's own camera. The `vision` extra must be installed; its first run downloads the models ([install.md](install.md#first-run-downloads)).

## `tts`: the computer's voice

```json
"tts": {
  "engine": {
    "module": { "type": "elevenlabs", "api_key_env": "ELEVENLABS_API_KEY", "voice_id": "JBFqnCBsd6RMkjVDRZzb", "model": "eleven_flash_v2_5" },
    "player": { "device": null }
  }
}
```

Computer only, required. The `engine` block is a tts-engine engine block, handed to `TTSEngineConfig.from_dict`: `module.type` picks the voice provider and the other `module` fields are that provider's own (a key variable, a voice, a model); `player.device` is the speaker (`null` for the default); `gain_db` levels a voice against others. The provider's `tts-<type>` extra must be installed ([install.md](install.md#extras)). Every provider has a page with its install line, fields and voices under tts-engine's [modules](https://github.com/funwithagents/tts-engine/blob/main/docs/modules/index.md); the block itself is documented in its [configuration page](https://github.com/funwithagents/tts-engine/blob/main/docs/configuration.md). The local `pocket` voice needs no key.

## `audio_processing`: the computer's mic processing

```json
"audio_processing": {
  "echo_cancellation": { "enabled": true, "stream_delay_ms": 50 },
  "noise_suppression": { "enabled": false, "level": 2 }
}
```

Computer only, optional: absent means both features off. One WebRTC processor cleans the microphone before the speech recognizer hears it ([guides/computer.md](../guides/computer.md#echo-cancellation)); the `audio-processing` extra. The app owns this schema.

| Field | Default | What it does |
|---|---|---|
| `echo_cancellation.enabled` | `false` | subtract what the voice is playing from the mic, so the agent does not hear itself |
| `echo_cancellation.stream_delay_ms` | `0` | a hint for the canceller's delay estimator: the output latency of your audio path, in milliseconds |
| `noise_suppression.enabled` | `false` | attenuate steady ambient noise |
| `noise_suppression.level` | `2` | WebRTC's aggressiveness, `0` (mild) to `3` (max) |

Strict on purpose: a misspelt feature or an unknown key is rejected, because the costly typo here is silent (`echo_cancelation` would load as "off" and the assistant would answer its own voice). A feature given as `null` keeps its defaults; one given as a non-object (`false`) is rejected.

## `camera`: the computer's webcam

```json
"camera": { "device": 0 }
```

Computer only, optional: absent means device `0`. What the hand-sign input reads when `vision.enabled` is `true`.

| Field | Default | What it does |
|---|---|---|
| `device` | `0` | what OpenCV opens: a device index, or a path or URL |

## `reachy`: the robot

```json
"reachy": {
  "backend": "sim",
  "robot": {},
  "daemon": { "spawn": "auto", "headless": false, "camera": { "source": "webcam", "device": null, "hfov_deg": 70.0 }, "startup_timeout": 45.0 },
  "tts": { "module": { "type": "pocket", "voice": "george", "device": "auto" } },
  "audio": { "xvf3800": null },
  "face_detection": { "detector": "yunet", "enabled": true, "width": 640, "target_fps": null },
  "motion": { "presence": true, "idle": "breathing", "wobbling": true, "tracking": { "enabled": true } }
}
```

Robot only. The section is a reachy-mini-bridge config, handed whole to `ReachyMiniConfig.from_dict`; the bridge's [configuration reference](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/reference/configuration.md) documents every field with its default. What each block means for this app ([guides/reachy-mini.md](../guides/reachy-mini.md)):

| Block | What it does here |
|---|---|
| `backend` | `real` for the robot, `sim` for the MuJoCo simulator, `fake` for an offline stand-in (no daemon: delete the `daemon` block with it) |
| `robot` | how to reach the robot: empty for a daemon on this machine; `host` for a wireless robot running its own daemon |
| `daemon` | whether the bridge starts a daemon (`spawn`), with or without the viewer window (`headless`), what the simulated camera shows (`camera.source`: your `webcam`, or the rendered `sim` scene), how long to wait for it (`startup_timeout`) |
| `tts` | **the robot's voice**: a tts-engine engine block, the same shape as the computer's `tts.engine`, built by the bridge; its `tts-<type>` extra must be installed. Absent, the robot comes up without a voice |
| `audio` | the XVF3800 profile; `null` keeps the firmware defaults. Echo cancellation is the robot's, in hardware |
| `face_detection` | the bridge's face detector (`yunet`, shipped; weights downloaded on first use), whether it runs, and its cost knobs `width` and `target_fps` |
| `motion` | the robot's aliveness between actions: `presence`, the `idle` move, `wobbling` with the voice, and `tracking` the person's face: an object whose `enabled` turns it on (needs a detector), the rest being the bridge's posture settings. None of it is agent-facing |

Absent, the section means the `real` backend with the bridge's defaults and no voice. The ASR section's `audio.device` is ignored on the robot: the app listens through the robot's echo-cancelled microphone, and the ASR module must deliver 16 kHz mono linear16 to match it.

## Keys stay in the environment

Every section references its key by **environment variable name** (`api_key_env`), never by value, so the templates are safe to commit. The names the templates use:

| Variable | Used by |
|---|---|
| `WICA_ANTHROPIC_API_KEY` | the computer template's agent (Anthropic) |
| `WICA_HF_TOKEN` | the reachy template's agent (the Hugging Face Hub) |
| `DEEPGRAM_API_KEY` | the speech recognizer, both templates |
| `ELEVENLABS_API_KEY` | the computer template's voice; the reachy template's `pocket` voice needs no key |

A variable that is unset does not fail the load: it fails that engine's build, and the app opens with a banner naming what to set ([guides/troubleshooting.md](../guides/troubleshooting.md#the-banner)). Export the variables in the shell that runs the command; a non-interactive shell does not read your `~/.zshrc`.

## What a loader rejects

Everything a loader can raise for a bad file is one `ValueError`, so the app opens in degraded mode with the message in the banner rather than crashing:

- a file that is missing, unreadable, not JSON, or not a JSON object;
- a missing `wica` or `asr` section, or an `asr` section without an `engine` block;
- a top-level key the command does not accept: `reachy` in a computer file; `tts`, `audio_processing` or `camera` in a reachy file; anything misspelt;
- an engine section its owner rejects, with the owner's own message: an unknown `agent` key, both `system_prompt` and `system_prompt_file`, a `daemon.spawn` on the `fake` backend, `motion.tracking.enabled` with no `face_detection.detector`, a `motion.tracking` written as a boolean;
- in the app-owned sections, an unknown key, a wrong type, or an out-of-range value, naming the file and the field: `vision.threshold` outside `[0, 1]`, `max_hands` below 1 or not an integer, a negative `hold_s`, a `camera.device` that is neither an int nor a string, a `noise_suppression.level` outside `0` to `3`.

A key that is unset, or a provider extra that is not installed, is **not** a load error: the file loads, and the failure lands on that engine's banner line at build time.
