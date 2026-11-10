---
code:
  - src/wica_interaction/computer/embodiment.py
  - src/wica_interaction/computer/config.py
  - src/wica_interaction/computer/app.py
  - prompts/computer.md
tests:
  - tests/computer/test_embodiment.py
  - tests/computer/test_config.py
---

# Computer embodiment

**Status:** Implemented

## Purpose

The agent embodied in **the machine the app runs on**: speech in from a local microphone, speech out through the local speakers via a `tts-engine` `TTSEngine`, optionally the mic cleaned by [audio processing](audio-processing.md) (WebRTC echo cancellation and noise suppression) and the person's hand signs read through a **webcam**. It has no actions beyond speaking. This is the former `vocal-interaction-wica` app, ported here as one implementation of the [Embodiment](embodiment.md) protocol; its persona is a plain voice assistant, [prompts/computer.md](../prompts/computer.md). It runs as `wica-interaction-computer --config <file>`.

## Configuration

The file carries the shared sections (`wica`, `asr`, optional `vision` — [configuration.md](configuration.md)) plus this embodiment's own, parsed by `computer/config.py`'s `load_config(path) -> ComputerConfig`:

```json
{
  "wica": { "agent": { "…": "…", "system_prompt_file": "prompts/computer.md" } },
  "asr": { "engine": { "…": "…", "audio": { "device": null } } },
  "tts": {
    "engine": {
      "module": { "type": "elevenlabs", "api_key_env": "ELEVENLABS_API_KEY", "voice_id": "…" },
      "player": { "device": null }
    }
  },
  "audio_processing": {
    "echo_cancellation": { "enabled": true, "stream_delay_ms": 50 },
    "noise_suppression": { "enabled": false, "level": 2 }
  },
  "vision": { "enabled": true, "threshold": 0.55, "fps": 5, "max_hands": 2, "hold_s": 0.5, "device": null },
  "camera": { "device": 0 }
}
```

```python
@dataclass(frozen=True)
class CameraConfig:
    device: int | str = 0  # an OpenCV index or path


@dataclass
class ComputerConfig:
    app: AppConfig  # the shared sections
    tts_engine: TTSEngineConfig
    audio_processing: AudioProcessingConfig
    camera: CameraConfig
```

- **`tts` (required)** — a tts-engine document; its `engine` block goes verbatim to `TTSEngineConfig.from_dict`, so the provider (`module.type`: `elevenlabs`, `gradium`, `pocket`, …), its key reference and voice, and the `player.device` (the speaker) are the engine's own schema. The provider named must have its extra installed (`tts-<name>`, one per tts-engine provider: `tts-elevenlabs`, `tts-gradium`, `tts-pocket`, … — [project.md](project.md), "Extras").
- **`audio_processing` (optional)** — absent ⇒ every feature off; the schema and semantics are in [audio-processing.md](audio-processing.md).
- **`camera` (optional)** — the webcam `GestureInput` reads when `vision.enabled`; `device` is what `cv2.VideoCapture` takes (an index, default `0`, or a path/URL). Absent ⇒ device `0`. A non-object section, an unknown key, or a `device` that is neither an int nor a string raises `ValueError` from `load_config`, naming the file and the field.
- The microphone is the ASR config's own `asr.engine.audio.device` (the engine opens it), so it needs no section here.
- Any top-level key outside `wica`/`asr`/`vision`/`tts`/`audio_processing`/`camera` is rejected — a `reachy` section in a computer config is a `ValueError`, not silently ignored ([configuration.md](configuration.md), "Each embodiment loads its own file").

## `ComputerEmbodiment`

```python
class ComputerEmbodiment:
    display_name = "computer"

    def __init__(
        self,
        config: ComputerConfig,
        *,
        tts_engine: TTSEngine | None = None,  # test seam
        coordinator_factory: Callable[[ComputerConfig], AudioProcessingCoordinator] = …,
    ) -> None: ...
```

Construction does no I/O and opens no device; everything happens in `enter()`, on the app loop:

1. **Audio processing.** When `config.audio_processing.any_enabled`, build the `AudioProcessingCoordinator` through `coordinator_factory` (the module's `build_coordinator`, [audio-processing.md](audio-processing.md), "Building the coordinator"). `ImportError` (the WebRTC binding or the resampler absent), `RuntimeError` (the ASR audio format is not 16 kHz mono linear16) and `ValueError` are caught into `missing` as `Audio processing: …`, and the embodiment carries on with no coordinator — the mic goes straight to ASR, the speakers straight to the player.
2. **The voice.** `TTSEngine(config.tts_engine, sink=coordinator.sink)` when echo cancellation is on (the far-end tap), plain `TTSEngine(config.tts_engine)` otherwise; then, with a sink, `coordinator.bind_far_rate(engine.sample_rate)` — the two-step the coordinator needs (the player and the resampler are built once the module's rate is known). The engine is wrapped once in `TTSTools(engine)`. **Any** exception from that build — tts-engine's `ConfigError` (a key unset, a misconfigured module), `ImportError` (the provider's extra not installed — the SDK import is lazy inside the module), or whatever else a provider's constructor raises (a local model that fails to load) — is caught into `missing` as `TTS: …` and kept as the reason `say` reports; the body stays up, mic and camera included ([embodiment.md](embodiment.md), "Degradation is per component").

The members:

- **`voice`** — the bound `say` below, **always**: with no engine it stays wired and answers with the TTS error, the same contract as the robot's ([embodiment.md](embodiment.md), "A voice that failed to build stays wired").
- **`mic`** — `coordinator.source` when a coordinator was built (the processed mic, whatever features are on), else `None`: the ASR engine opens its default `AudioCapture` on `asr.engine.audio.device`.
- **`mic_error`** — always `None`; a local capture that dies raises inside the engine, which reports it its own way.
- **`camera(fps=…)`** — a `StreamFeed(WebcamSource(config.camera.device, fps=fps))`: the paced webcam source below, behind the gesture module's own `StreamProvider` adapter ([gesture-input.md](gesture-input.md), "`StreamFeed`"), so the hand stage samples a latest-value slot here as it does on the robot. `open()` starts the provider (which opens the webcam), `close()` stops it; `capture_error` is the source's.
- **`commands()`** — `()`; the computer has nothing to act with.
- **`prompt_addendum`** — `""`.
- **`exit()`** — closes the coordinator's voice stream, kept open for the app's lifetime so the echo's delay stays put; stopping the ASR bridge has already stopped the injected source (capture + pump) ([audio-processing.md](audio-processing.md), "Teardown").

### `say`

```python
async def say(self, text: str) -> str:
    """Speak `text` aloud to the user through the machine's speakers.

    This is your voice: whatever you want the user to hear, say it here.
    Returns "OK" when the utterance finished playing.
    """
```

Delegates to `TTSTools(engine).say` (`tts-engine/specs/tools.md`), reusing its guarded, non-raising contract rather than calling `engine.say` directly: an empty string returns `"TTS error: text must not be empty"`, a synthesis failure `"TTS error: <message>"`, success `"OK"`. With no engine (the build failed in `enter()`), it returns `"TTS error: <the build error>"` without touching anything. It blocks until playback finishes (streaming playback inside the engine), so the Command stays `running` in the World for the real duration of the speech. As a bound method its docstring is what the model sees as the voice's description ([embodiment.md](embodiment.md)).

### `WebcamSource`

The `CameraSource` ([gesture-input.md](gesture-input.md), "`StreamFeed`") over `vision-modules`' own `OpenCVSource` (`cv2.VideoCapture` behind the `FrameSource` protocol; OpenCV is the library's `opencv` extra, pulled by the app's `vision` extra — [project.md](project.md) — and imported only when the device is opened):

```python
class WebcamSource:
    def __init__(self, device: int | str, *, fps: float) -> None: ...
    def open(
        self,
    ) -> None: ...  # never raises: a device that cannot be opened is a capture_error
    def read(self) -> np.ndarray | None: ...  # paced at fps; None only once closed
    def close(self) -> None: ...
    def fps(
        self,
    ) -> float: ...  # the poll rate (a webcam's own rate is unknown or irrelevant)
    @property
    def capture_error(self) -> str | None: ...
```

- **It paces itself** to `fps`: the `StreamProvider` calls `read()` back-to-back, and a webcam delivering 30 fps into a 5 fps hand stage would be wasted captures. (The robot's camera is a feed at the daemon's own rate and paces nothing — [reachy-embodiment.md](reachy-embodiment.md).)
- **A device that cannot be opened is visible, not fatal.** `OpenCVSource.open()` raises when the device is missing; `WebcamSource` catches it into `capture_error` (`"camera open failed: …"`) and `read()` keeps retrying the open at the poll rate, so an unplugged webcam shows on the Vision line and heals when plugged in. A failed `read()` (the device went away) is recorded the same way and retried; only `close()` makes `read()` return `None`. The provider never `ended`s on its own.
- **A stream that ended is reopened in the same poll.** OpenCV signals the end of a stream by returning no frame, not by raising: an image file after its one frame, a video file played through, a webcam unplugged mid-stream. So a device that has delivered a frame since it was opened and then yields none is released and reopened at once, and read again: a file loops with no gap and no error (the end-to-end tier uses an image file as the webcam — [e2e.md](e2e.md)), and a webcam that went away shows its open failure on the Vision line. A device that has delivered nothing yet is a webcam still warming up, and is polled again as is.
- **An open never outlives the source.** The retry-open runs on the capture thread while `close()` runs on the main one; the opened flag flips under the source's lock, and an open that completes after `close()` is closed again rather than kept.
- No mirroring: the classifier sees the frame as captured. (The hand demo mirrors only for display.)

## Entry point

`wica_interaction.computer.app:main` — `wica-interaction-computer` in `[project.scripts]`:

```python
def _load(path: str) -> tuple[AppConfig, Embodiment]:
    config = load_config(path)
    return config.app, ComputerEmbodiment(config)


def main() -> None:
    run_app(description="Talk to a WICA voice assistant.", load=_load)
```

`run_app` in `system.py` does the rest ([app.md](app.md), "Wiring and lifecycle"): `--config` (required), load or degrade, build, UI, wire, serve, tear down. The module imports `tts_engine` and this package only — never `reachy_mini_bridge` — so a `wica-interaction[computer]` install has no robot SDK to import ([project.md](project.md)).

## Persona

[prompts/computer.md](../prompts/computer.md): a friendly voice assistant in a spoken conversation; it speaks only through `say`, may choose not to react, and can switch to dictation when asked — including the sentence that an empty dictated passage means nothing came through. It says nothing about hands: the "Hand signs" section is appended at startup by the app when vision is on ([gesture-input.md](gesture-input.md), "What the agent does with it"), so the same file serves with and without a webcam. It does **not** say how a dictated passage ends: that depends on the config's `dictation_default_segmentation_mode`, so `start_dictation`'s reply says it — for the model to know, not to announce ([asr-bridge.md](asr-bridge.md), "Dictation").

## Testing

`tests/computer/` — deterministic, no audio device, no camera, no provider ([testing.md](testing.md)):

- **Config:** `load_config` on a computer file yields the shared `AppConfig` plus `tts_engine`, `audio_processing`, `camera`; `tts` missing, `tts` without an `engine` block, a `camera` that is not an object or has an unknown key, a `reachy` section, each raise `ValueError` naming the file; `camera` absent ⇒ device `0`; the `audio_processing` cases are in [audio-processing.md](audio-processing.md).
- **Voice:** with an injected fake `TTSEngine`, `say("hello")` reaches the engine once and returns `"OK"`; `say("")` returns the empty-text error without calling it; a `TTSError` maps to `"TTS error: …"`.
- **Enter:** with a fake engine, `voice` is set, `mic` is `None` with processing off; a `coordinator_factory` returning a fake coordinator makes `mic` its `source`, and with echo cancellation on the engine is built with its `sink` and `bind_far_rate` is called with the engine's rate; a factory raising `ImportError` degrades to `Audio processing: …` in `missing` with a still-working voice; a `TTSEngine` build raising `ConfigError`, `ImportError` or any other exception degrades to `TTS: …` with `voice` still the `say`, which returns the TTS error naming the cause; `commands()` is empty and `prompt_addendum` is `""`.
- **Webcam:** `WebcamSource` over an injected fake `OpenCVSource`-like object: `read()` paces to `fps`; an `open()` that raises sets `capture_error` and a later success clears it; `close()` makes `read()` return `None` promptly; an open that completes after `close()` is released; a device whose stream ends after each frame (as an image file's does) is reopened in the same poll, delivering every poll with no `capture_error`, while a device that has delivered nothing yet is not reopened; a stream that ends and whose reopen fails sets `capture_error` (`camera open failed`), and the source heals once the device opens again. `camera(fps=…)` returns a `StreamFeed` whose `capture_error` is that webcam's (an unopenable device shows through it) and whose `open()` / `close()` open and close the device.

A whole-loop live test (real mic + speakers + provider, asserting the agent replies and does not self-trigger) stays deferred to `tests-e2e/`.

## Open questions

1. **Mirroring for the webcam.** A person facing their own laptop may find the hand demo's mirrored view more natural on a future camera panel; classification does not need it. Deferred with the camera panel ([gesture-input.md](gesture-input.md), open question 3).
2. **Processing status in the UI.** "echo cancellation / noise suppression: on / degraded" on the ASR line — the coordinator handle is on the embodiment, so it is easy; nice-to-have ([audio-processing.md](audio-processing.md), open question 2).
