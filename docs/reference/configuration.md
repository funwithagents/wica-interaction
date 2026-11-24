# Configuration reference

Each app is stood up from **one JSON config**, the `config.json` of its folder: the sections every embodiment shares (`wica`, `asr`, `vision`) plus the sections of the app's body (`tts`, `audio_processing`, `camera` for the computer; `reachy` for the robot). Switching the model, the voice, the speech recognizer, the robot backend, the camera or the persona is an edit of that file. This page is the config section by section; the design is [specs/configuration.md](../../specs/configuration.md) and [specs/setup.md](../../specs/setup.md).

Each engine section is **that engine's own document, nested verbatim**: the `wica` section is a WICA config, `asr` an asr-engine engine block, `tts` a tts-engine engine block, `reachy` a reachy-mini-bridge config. The app hands each to its owner's loader, so every field, default and validation rule is the engine's, and this page summarizes the fields you will touch and links to the engine's own reference for the rest. The two sections the app owns, `vision` and the computer's `audio_processing` / `camera`, are complete here.

## One folder per app

| Command | Its folder | Sections |
|---|---|---|
| `wica-interaction-computer` | `~/.config/wica-interaction-computer` | `wica`, `asr`, `tts`, `audio_processing`, `vision`, `camera` |
| `wica-interaction-reachy` | `~/.config/wica-interaction-reachy` | `wica`, `asr`, `reachy`, `vision` |

`--folder` names another folder (`$XDG_CONFIG_HOME` is followed when set). One folder is one config: a second config of the same app, a French persona or an offline robot, is a second folder. The folder holds:

| File | What it is |
|---|---|
| `config.json` | the config, edited by hand; the app reads it at launch |
| `computer.md` / `reachy.md` | the persona, which the config names by this bare name; edit it here |
| `.env` | the keys, as `NAME=value` lines, loaded at launch; a variable already set in the shell wins ([Keys](#keys-stay-in-the-environment)) |

**The first launch fills an empty folder**: the app writes its default config as `config.json` and copies its persona there. Both ship inside the package ([computer](../../src/wica_interaction/computer/default/config.json), [robot](../../src/wica_interaction/reachy/default/config.json)). Neither is ever overwritten: what is in the folder is yours. A relative path in any section (the persona, a voice file, a scene) resolves against the folder.

The default configs spell out the app's own `vision` and `audio_processing` sections in full. The robot's `reachy` section, long otherwise, keeps to the fields that matter here and shows each motion switch, on or off; every field left out takes its owner's default, documented on the pages linked below.

Which body runs is decided by the command, not by the config: each command accepts its own sections only, so a `reachy` section in the computer's config is rejected rather than silently ignored ([What a loader rejects](#what-a-loader-rejects)).

## What the default configs run, and the other choices

Both default configs run as much as possible **on your machine** and need **one key**, `HF_TOKEN` (a Hugging Face token, the Hub provider's own variable):

| Part | The default configs' choice | Extra |
|---|---|---|
| the agent | `openai/gpt-oss-120b` on the Hugging Face Hub, served by Cerebras (`huggingface-hub`) | `wica-huggingface-hub` |
| speech in | Parakeet on the local mlx-audio runner (`mlx-audio`), behind the Silero voice-activity gate | `asr-mlx-audio`, `asr-silero` |
| the voice | Phonon, a local voice (`phonon`), on the computer's speakers or the robot's | `tts-phonon` |

**mlx-audio runs on Apple silicon only.** On another machine, pick another recognizer in `asr.module`: a hosted one (`deepgram-v2`, with the `asr-deepgram` extra and a `DEEPGRAM_API_KEY`), or `openai-transcriptions-compatible` pointed at a speech-to-text server you run yourself.

Every part is a field away from another provider. Change the block, install the matching extra ([install.md](install.md#extras)), set its key variable if it has one. What each provider needs and offers is on its owner's pages:

- **The agent** (`wica.agent`): the providers and their fields in WICA's [configuration reference](https://github.com/funwithagents/wica-framework/blob/main/docs/reference/configuration.md#providers); a model served on your machine or your own server in its [local models guide](https://github.com/funwithagents/wica-framework/blob/main/docs/guides/local-models.md).
- **Speech in** (`asr.module`): asr-engine's [choosing a module](https://github.com/funwithagents/asr-engine/blob/main/docs/choosing-a-module.md), one [page per module](https://github.com/funwithagents/asr-engine/blob/main/docs/modules/index.md), and its [local speech recognition guide](https://github.com/funwithagents/asr-engine/blob/main/docs/local-speech-recognition.md).
- **The voice** (`tts.module` on the computer, `reachy.tts.module` on the robot): tts-engine's [choosing a backend and a voice](https://github.com/funwithagents/tts-engine/blob/main/docs/choosing-a-backend-and-voice.md) and one [page per module](https://github.com/funwithagents/tts-engine/blob/main/docs/modules/index.md), each with its voices, its key and its platforms.

## `wica`: the agent

```json
"wica": {
  "agent": {
    "provider": "huggingface-hub",
    "model": "openai/gpt-oss-120b",
    "hf_provider": "cerebras",
    "system_prompt_file": "computer.md",
    "context_window": 128000,
    "history_budget": 0.5
  }
}
```

A WICA config, `{"agent": {...}}`, handed to `WicaConfig.from_dict`. The fields you will set:

| Field | What it does |
|---|---|
| `provider` | `anthropic`, `openai`, `gemini`, `mistral`, `huggingface-hub`, or `openai-chat-completions-compatible` (any server implementing OpenAI's Chat Completions API, a local model among them; it needs `base_url` and the `wica-openai-compatible` extra); the matching `wica-*` extra must be installed ([install.md](install.md#extras)). The old names `google_genai` and `mistralai` still load, with a deprecation warning |
| `model` | the model id (the Hub `repo_id` for `huggingface-hub`, with `hf_provider` naming the backend that serves it) |
| `api_key_env` | the environment variable holding the key, read when the agent is built; absent, the provider's usual one (`HF_TOKEN`, `ANTHROPIC_API_KEY`, …). A config never holds the key itself: an `api_key` field is refused |
| `system_prompt_file` | the persona, relative to the folder: `computer.md` or `reachy.md`, the copy the first launch put there ([guides/personas.md](../guides/personas.md)) |
| `model_kwargs` | forwarded to the provider's model constructor: `temperature`, Gemini's `thinking_level` |
| `context_window`, `history_budget`, `history_reactions` | bound the prompt's history; the default configs keep the prompt under half the model's 128k window |

Every field, the providers and their notes (Gemini thinks by default, Mistral's free tier) are in WICA's [configuration reference](https://github.com/funwithagents/wica-framework/blob/main/docs/reference/configuration.md). Two things the app does with this section: it resolves the persona itself and appends the sections the body and the hand-sign input need before WICA sees it ([agent.md](agent.md#the-persona-what-is-appended-at-startup)), and the `say` output Command is the body's voice, set at wiring, not a config field.

## `asr`: speech in

```json
"asr": {
  "module": {
    "type": "mlx-audio",
    "model": "mlx-community/parakeet-tdt-0.6b-v3",
    "interim_interval_s": 0.5
  },
  "vad": {
    "type": "silero",
    "min_silence_duration_ms": 300
  }
}
```

The section is an asr-engine engine block, handed to `ASREngineConfig.from_dict`. (Until 2026-11-24 it was wrapped in an `engine` key; a config still written that way is refused: move the block up one level.) The fields that matter here:

| Field | What it does |
|---|---|
| `module.type` | the speech recognizer: `deepgram-v2` (Deepgram's Flux models, turn-based) or `deepgram-v1` (Nova models), the `asr-deepgram` extra; `mistral-realtime` (`asr-mistral`); `openai-realtime-compatible` and `openai-transcriptions-compatible` (`asr-openai-compatible`); `mlx-audio`, local on Apple silicon, the default configs' choice (`asr-mlx-audio`). Other fields of `module` are the module's own: `api_key_env`, `model`, `language`, mlx-audio's `interim_interval_s` (how often the live text is refreshed while you speak). The names written with an underscore (`deepgram_v2`) still load, with a deprecation warning |
| `vad` | a voice-activity gate in front of the recognizer, on in both default configs: `{"type": "silero"}`, with the `asr-silero` extra (its 2.3 MB model is downloaded on first use). Only speech reaches the recognizer, so a billed one is not sent the room's silence. `min_silence_duration_ms` is how long the gate keeps forwarding after the person stops (default `500`); with `mlx-audio` it is the pause that ends an utterance, `300` in the default configs. With `deepgram-v2` (Flux), set it higher (`1500`) so the recognizer still ends the turn itself. Optional: remove the block, or set it to `null`, to send the recognizer everything. **Required** by the batch recognizers (`mlx-audio`, `openai-transcriptions-compatible`), which have no other end-of-utterance signal |
| `audio_source` | where the audio comes from; absent in the default configs. **On the computer**, `{"type": "input-device", "device": …}`, the default: the microphone, a name, an index, or `null` for the system default. **On the robot**, the mic is the robot's: leave the block out (the app hands the engine the robot's mic), or write `{"type": "custom"}`, which takes no other field. The older `audio` block is refused at load, with a message naming this one |
| `dictation_default_segmentation_mode` | how a dictated passage ends: `timeout` (a pause) or `trigger_word` (a word from `segmentation.trigger_words`, the default) ([guides/dictation.md](../guides/dictation.md)) |
| `segmentation.trigger_words`, `segmentation.end_of_speech_timeout_s`, `segmentation.initial_silence_timeout_s` | the words and the silences the two dictation modes use |

The recognizer does not have to match the microphone: the robot's mic and the computer's echo canceller deliver 16 kHz, and asr-engine converts for a recognizer fed another rate (`openai-realtime-compatible`, 24 kHz). With `audio_processing` on, leave `audio_source.audio_format` unset: the echo canceller opens the device itself, at 16 kHz. `auto_start` and `auto_start_dictation` are not honored: the app starts the engine itself, in conversation mode. The complete schema, the modes and the default trigger words are in asr-engine's [configuration reference](https://github.com/funwithagents/asr-engine/blob/main/docs/configuration.md); the Kyutai local modules are not offered here ([install.md](install.md#extras)).

## `vision`: hand signs

```json
"vision": {
  "enabled": true,
  "threshold": 0.65,
  "fps": 10,
  "max_hands": 2,
  "hold_s": 0.3,
  "release_hold_s": 0.5,
  "device": null
}
```

The hand-sign input over whichever camera the body has ([guides/hand-signs.md](../guides/hand-signs.md)). Optional, and **off unless `enabled` is `true`**: an absent section, `null`, `{}` or `enabled: false` opens no camera, downloads no model and registers no hand entry. The app owns this schema; the default configs turn it on, raise `threshold` to `0.65` so a sign is read only when the classifier is sure, and spell out every other field at its default, so the knobs are in front of you.

| Field | Default | What it does |
|---|---|---|
| `enabled` | `false` | the switch |
| `threshold` | `0.55` | the classifier's confidence a label needs to count, in `[0, 1]` |
| `fps` | `10` | one rate for the camera poll (where the body paces it), the hand stage and the classifier |
| `max_hands` | `2` | how many hands are detected and classified per frame, and so how many `hand_gesture_<n>` entries the agent has; an integer `>= 1` |
| `hold_s` | `0.3` | how long a hand's sign must be seen, unbroken, before it reaches the agent; `0` delivers on the first frame |
| `release_hold_s` | `0.5` | how long a hand must show no sign, unbroken, before its sign counts as gone; `0` clears it on the first frame |
| `device` | `null` | the classifier's torch device (`mps`, `cuda`, `cpu`); `null` lets the library pick |

The camera itself is the body's: the computer's `camera` section below, the robot's own camera. The `vision` extra must be installed; its first run downloads the models ([install.md](install.md#first-run-downloads)).

## `tts`: the computer's voice

```json
"tts": {
  "module": {
    "type": "phonon",
    "voice": "Miles"
  }
}
```

Computer only, required. The section is a tts-engine engine block (no `engine` key around it any more), handed to `TTSEngineConfig.from_dict`: `module.type` picks the voice provider and the other `module` fields are that provider's own (a key variable, a voice, a model); `player.device` is the speaker (`null` for the default); `gain_db` levels a voice against others. The provider's `tts-<type>` extra must be installed ([install.md](install.md#extras)). Every provider has a page with its install line, fields and voices under tts-engine's [modules](https://github.com/funwithagents/tts-engine/blob/main/docs/modules/index.md); the block itself is documented in its [configuration page](https://github.com/funwithagents/tts-engine/blob/main/docs/configuration.md). The default config's `phonon` voice is local and needs no key; it downloads its voice on the first `say`.

## `audio_processing`: the computer's mic processing

```json
"audio_processing": {
  "echo_cancellation": {
    "enabled": true,
    "stream_delay_ms": 50
  },
  "noise_suppression": {
    "enabled": false,
    "level": 2
  }
}
```

Computer only, optional: absent means both features off; the default config turns echo cancellation on and spells out noise suppression, off. One WebRTC processor cleans the microphone before the speech recognizer hears it ([guides/computer.md](../guides/computer.md#echo-cancellation)); the `audio-processing` extra. The app owns this schema.

| Field | Default | What it does |
|---|---|---|
| `echo_cancellation.enabled` | `false` | subtract what the voice is playing from the mic, so the agent does not hear itself |
| `echo_cancellation.stream_delay_ms` | `0` | the output latency of your audio path, in milliseconds, passed to the canceller as a hint; the current one (AEC3) measures the delay itself and all but ignores it |
| `noise_suppression.enabled` | `false` | attenuate steady ambient noise |
| `noise_suppression.level` | `2` | WebRTC's aggressiveness, `0` (mild) to `3` (max) |

Strict on purpose: a misspelt feature or an unknown key is rejected, because the costly typo here is silent (`echo_cancelation` would load as "off" and the assistant would answer its own voice). A feature given as `null` keeps its defaults; one given as a non-object (`false`) is rejected.

## `camera`: the computer's webcam

```json
"camera": {
  "device": 0
}
```

Computer only, optional: absent means device `0`, which is why the default config leaves it out. What the hand-sign input reads when `vision.enabled` is `true`.

| Field | Default | What it does |
|---|---|---|
| `device` | `0` | what OpenCV opens: a device index, or a path or URL |

## `reachy`: the robot

```json
"reachy": {
  "backend": "sim",
  "daemon": {
    "spawn": "auto",
    "headless": false,
    "camera": {
      "source": "webcam",
      "device": null,
      "hfov_deg": 70.0
    },
    "sim_displays": {
      "camera_overlay": true,
      "robot_gaze": true,
      "face_markers": true
    }
  },
  "tts": {
    "module": {
      "type": "phonon",
      "voice": "Micah"
    }
  },
  "face_detection": {
    "detector": "yunet",
    "enabled": true,
    "width": 1280,
    "score_threshold": 0.6,
    "target_fps": null
  },
  "motion": {
    "presence": true,
    "wobbling": true,
    "tracking": {
      "enabled": false,
      "mirroring": {
        "enabled": true
      },
      "approach": {
        "enabled": true
      },
      "personal_space": {
        "enabled": true,
        "distance_m": 0.3,
        "span_m": 0.1,
        "exit_margin_m": 0.1
      }
    },
    "base_following": {
      "enabled": true
    }
  }
}
```

Robot only. The section is a reachy-mini-bridge config, handed whole to `ReachyMiniConfig.from_dict`; the bridge's [configuration reference](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/reference/configuration.md) documents every field with its default; the default config keeps to the fields that matter here and leaves the rest at them. What each block means for this app ([guides/reachy-mini.md](../guides/reachy-mini.md)):

| Block | What it does here |
|---|---|
| `backend` | `real` for the robot, `sim` for the MuJoCo simulator, `fake` for an offline stand-in (no daemon: delete the `daemon` block with it) |
| `robot` | how to reach the robot: empty for a daemon on this machine; `host` for a wireless robot running its own daemon |
| `daemon` | whether the bridge starts a daemon (`spawn`), with or without the viewer window (`headless`), what the simulated camera shows (`camera.source`: your `webcam`, or the rendered `sim` scene, the default), what the viewer draws over the scene (`sim_displays`: the camera picture, where the robot looks, where it places each face), how long to wait for it (`startup_timeout`, 45 s by default) |
| `tts` | **the robot's voice**: a tts-engine engine block, the same shape as the computer's `tts`, built by the bridge; its `tts-<type>` extra must be installed. Absent, the robot comes up without a voice |
| `audio` | the XVF3800 profile; absent or `null` keeps the firmware defaults. Echo cancellation is the robot's, in hardware |
| `face_detection` | the bridge's face detector (`yunet`, shipped; weights downloaded on first use), `score_threshold` (the confidence a face is kept from, strictly between 0 and 1; the default config's `0.6` is upstream's default, `null` follows it), and its cost knobs `width` and `target_fps`. With a detector the robot sees people and tells the agent, and the app runs detection for the whole session whatever `enabled` says; without one it sees nobody ([guides/reachy-mini.md](../guides/reachy-mini.md#the-users-in-front-of-the-robot)) |
| `motion` | the robot's aliveness between actions: `presence`, the `idle` move, `wobbling` with the voice, none of it agent-facing, all on by default; and `tracking`, following a person with the head: an object whose `enabled` says whether a session starts following (`false` by default: the agent starts it), the rest being the bridge's posture settings, each with its own switch; `base_following` turns the robot's body toward whoever the head follows. The default config shows every switch at its default and moves the personal space closer (`0.3` m). Needs a detector |

Absent, the section means the `real` backend with the bridge's defaults and no voice. On the robot the app listens through the robot's echo-cancelled microphone, whatever rate the ASR module is fed.

## Keys stay in the environment

Every section references its key by **environment variable name** (`api_key_env`), never by value: a config holding `api_key` is refused. Left out, `api_key_env` is the provider's usual variable, so the default configs name none and need one, `HF_TOKEN`, a Hugging Face token for the agent; their speech recognizer and voice run locally and need none. A hosted recognizer or voice adds its own variable (`DEEPGRAM_API_KEY`, `GRADIUM_API_KEY`, …), or the one its `api_key_env` names (the e2e tier's configs use `WICA_ANTHROPIC_API_KEY`).

Set each variable in the shell that runs the command, or in the folder's `.env` (`HF_TOKEN=hf_...`, one per line), which the app loads at launch; the shell wins when both set it. A non-interactive shell does not read your `~/.zshrc`. A variable that is unset does not fail the load: it fails that engine's build, and the app opens with a banner naming what to set ([guides/troubleshooting.md](../guides/troubleshooting.md#the-banner)).

## What a loader rejects

Everything a loader can raise for a bad config is one `ValueError`, so the app opens in degraded mode with the message in the banner rather than crashing:

- a `config.json` that is unreadable, not JSON, or not a JSON object;
- a missing `wica` or `asr` section, or one that is not an object;
- a top-level key the command does not accept: `reachy` in the computer's config; `tts`, `audio_processing` or `camera` in the robot's; anything misspelt;
- an engine section its owner rejects, with the owner's own message: an `asr` or `tts` still wrapped in `engine`, a key written as `api_key`, an unknown `agent` key, both `system_prompt` and `system_prompt_file`, a `daemon.spawn` on the `fake` backend, `motion.tracking.enabled` with no `face_detection.detector`, a `motion.tracking` written as a boolean;
- in the app-owned sections, an unknown key, a wrong type, or an out-of-range value, naming the section and the field: `vision.threshold` outside `[0, 1]`, `max_hands` below 1 or not an integer, a negative `hold_s` or `release_hold_s`, a `camera.device` that is neither an int nor a string, a `noise_suppression.level` outside `0` to `3`.

A key that is unset, or a provider extra that is not installed, is **not** a load error: the file loads, and the failure lands on that engine's banner line at build time.
