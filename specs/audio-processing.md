---
code:
  - src/wica_interaction/computer/audio_processing.py
  - src/wica_interaction/computer/config.py
tests:
  - tests/computer/test_audio_processing.py
---

# Audio processing

**Status:** Implemented

## Purpose

Clean the **computer embodiment**'s microphone signal before ASR sees it ([computer-embodiment.md](computer-embodiment.md)), with one coordinator that runs the mic through a WebRTC audio-processing module offering two **independent** features:

- **Echo cancellation (AEC)** — stop the app from *hearing itself*. With a live mic open during TTS playback, the speaker output travels through the air into the mic, ASR transcribes it, and it lands as a `speech_input` — the agent reacts to its own voice. AEC taps what TTS is about to play as a *reference* signal and subtracts it from the mic.
- **Noise suppression (NS)** — attenuate steady ambient noise (fans, hum, hiss); it needs no reference and works with or without AEC.

The Reachy Mini embodiment needs none of this: its XVF3800 does it in firmware ([reachy-embodiment.md](reachy-embodiment.md), "Why the robot owns audio"). This removes the self-hearing loop; it does **not** add barge-in, which stays gated by WICA's one-reasoning-call-at-a-time model. Ported verbatim from `vocal-interaction-wica`'s `audio_processing.py`.

## Where it plugs in (the seams already exist)

Both engines expose a public injection seam, and `ComputerEmbodiment.enter()` builds both in one place — a pure composition, no engine edits:

- **TTS far-end reference** — `TTSEngine(cfg, sink=<AudioSink>)`: the engine calls `sink.feed(chunk)` with every synthesized PCM chunk and `sink.drain()` at the end (`tts-engine/specs/audio-sink.md`). Our sink is the reference tap — wired **only when echo cancellation is on**.
- **ASR cleaned input** — `ASREngine(cfg, audio_source=<AudioSource>)`: `source.start()` returns the `asyncio.Queue[bytes]` the engine drains (`asr-engine/specs/audio-source.md`). Our source runs each mic frame through the processor — it is the embodiment's `mic` whenever **any** feature is enabled.

With every feature **disabled** (the default), the embodiment's `mic` is `None` and the TTS engine gets no sink: the engines run exactly as before, zero overhead, no new failure modes.

## Signal facts that shape the design

| | Rate | Format | Chunk |
|---|---|---|---|
| Mic capture (`AudioCapture`) | **16 kHz** | s16 LE mono | 100 ms / 1600 samples |
| TTS playback (ElevenLabs) | **44.1 kHz** | s16 LE mono | variable (miniaudio decode) |
| WebRTC APM | **16 kHz** | s16 LE mono | **10 ms / 160 samples**, fixed |

1. **Resample the reference, not the playback.** Playback stays at the module's rate (full fidelity); only the *reference copy* is downsampled to 16 kHz with a **stateful** `samplerate` resampler (one `Resampler` carrying filter state across chunks), so a long utterance resamples without drift or boundary artifacts — drift would de-align the reference from the mic, which AEC punishes. (`samplerate` is MIT + BSD-2 libsamplerate; `soxr` was rejected as LGPL, `scipy` as ~90 MB for one function.)
2. **Re-chunk to 10 ms frames.** A 100 ms mic chunk is ten 160-sample frames; the coordinator buffers near-end samples and processes complete frames only.
3. **Alignment from what plays + delay hint (AEC).** The reference is taken as the voice's stream sends each block to the device — ahead of what the mic hears by the output buffering and the acoustic path, and by a constant amount, since the stream stays open; WebRTC's delay estimator, seeded by `stream_delay_ms`, absorbs that — the reason for a WebRTC-class processor over a bare adaptive filter.

## The module: `computer/audio_processing.py`

### `FrameProcessor` — the swappable backend interface

```python
FRAME_SAMPLES = 160  # 10 ms at 16 kHz


class FrameProcessor(Protocol):
    def process(self, near: bytes, far: bytes) -> bytes:
        """One FRAME_SAMPLES frame of s16 LE mono @16 kHz (320 bytes) in, the cleaned near frame
        out; `far` is the playback reference (ignored when echo cancellation is off)."""
```

### `WebRtcAudioProcessor` — the default backend

Wraps [`aec-audio-processing`](https://pypi.org/project/aec-audio-processing/) (WebRTC APM): `AudioProcessor(enable_aec, enable_ns, ns_level, …)`, `process_reverse_stream` / `process_stream`, `set_stream_delay`, `get_frame_size() == 160` @16 kHz. The import is **lazy** so the package stays optional. AEC and NS are independent flags; the far-end reference is fed via `process_reverse_stream` **only when echo cancellation is enabled**. Construction asserts the 160-sample frame (fail fast on a wrong rate).

### `AudioProcessingCoordinator` — bridges TTS sink → ASR source

Holds the processor, a `Resampler` for the reference, a thread-safe **far-end ring buffer** (int16 samples @16 kHz under a lock), and builds the real audio endpoints (its `ContinuousPlayer`, `asr_engine.audio.AudioCapture`). Exposes `.sink` and `.source`, and `close()` for the player's stream.

```python
class AudioProcessingCoordinator:
    def __init__(self, processor, *, asr_device, tts_device,
                 near_format=…, resampler_factory=…, player_factory=…, capture_factory=…): ...
    @property
    def sink(self) -> AudioSink: ...     # → TTSEngine(sink=…), echo cancellation only
    @property
    def source(self) -> AudioSource: ... # → the embodiment's mic, any feature
    def bind_far_rate(self, far_rate: int) -> None: ...  # echo cancellation only; opens the player's stream
    def close(self) -> None: ...  # closes it (the embodiment's exit)
```

**Far-end path (`sink`)** — the voice plays through the coordinator's own **`ContinuousPlayer`**, and the reference is taken from it as it plays:

- **One output stream for the app's lifetime.** The player opens its stream at `bind_far_rate` and keeps it running until `close()` (at the embodiment's `exit()`), playing what `feed` queued and silence between utterances. The echo's delay — output buffering, the room, input buffering — then stays where the canceller learned it. A stream per utterance (tts-engine's `AudioPlayer` opens one at the first `feed` and closes it at `drain`) moved that delay by tens of milliseconds at each reply, measured in the end-to-end tier ([e2e.md](e2e.md)); the canceller treated it as a new echo path and let the echo through for about two seconds while it relearned, and the app heard its own first words. That is what voice frameworks with echo cancellation do: the layer that owns the device keeps one stream (Pipecat's local transport, WebRTC's audio device module), while a TTS library's player opens one per call.
- **The reference is what went out.** The stream's callback hands each block that carries queued audio to the coordinator, which resamples it to 16 kHz and appends it to the far buffer under the lock (capping a pathological backlog by dropping the oldest). So the reference is on time by construction, whatever the size of the TTS module's chunks. (Taken when a chunk was fed, it ran up to a chunk late: `AudioPlayer.feed` blocks until the whole chunk is in the output buffer, and with phonon's second-long chunks the reference reached the canceller 1.1 s after its echo, which no canceller can remove.)
- **Pacing.** `feed(chunk)` queues the samples and returns once the queue is within 100 ms of the device, as a blocking write would, so a cancelled utterance stops within that much audio rather than playing all it was fed. `drain()` returns once the queue has gone out, leaving the stream open. A stream that stopped on its own (the device gone) unblocks both, dropping the queue, rather than hanging the TTS worker.
- The far buffer is left to empty as the pump consumes it: at drain time it still holds the reference for audio the mic has not finished hearing, so clearing it would leak the echo tail.

**Near-end path (`source`)** — the `AudioSource` handed to ASR, pumped on the app loop: `start()` starts the real `AudioCapture`, spawns a pump task and returns the owned output queue; the pump buffers raw mic samples and, per complete 160-sample frame, pops 160 far samples (silence when the buffer is empty — no playback), runs `processor.process(near, far)`, and re-batches to the input chunk size (preserving ASR's cadence); `stop()` stops the capture and cancels the pump. NS-only setups always see silence references, so `process` denoises the mic alone.

**The construction two-step** (AEC only): `TTSEngine(sink=…)` needs the sink before the engine exists, but the sink's player/resampler need the module's `sample_rate`, known only after. So the embodiment builds the coordinator, injects `coordinator.sink`, then calls `coordinator.bind_far_rate(engine.sample_rate)`. `feed` only runs during `say`, well after binding. NS-only setups never wire the sink and never bind.

### Building the coordinator: `build_coordinator(config)`

The one caught build point, called from `ComputerEmbodiment.enter()` ([computer-embodiment.md](computer-embodiment.md)) through its `coordinator_factory` seam. It checks the ASR format first (16 kHz mono linear16, else `RuntimeError`), builds `WebRtcAudioProcessor(…)` from `config.audio_processing` (`ImportError` if the binding is absent), and — **only when echo cancellation is on** — also imports `samplerate` there, so a partial install (binding present, resampler absent) degrades at this point instead of failing later inside `bind_far_rate`. The embodiment catches `ImportError` / `RuntimeError` / `ValueError` into `missing` (`Audio processing: …`) and runs with no processing.

## Configuration

An **optional** top-level `audio_processing` section of the computer config — app-owned (a bridge between two engines, not an engine), so it is parsed in `computer/config.py`, not delegated to a sibling loader:

```json
"audio_processing": {
  "echo_cancellation": { "enabled": true, "stream_delay_ms": 50 },
  "noise_suppression": { "enabled": false, "level": 2 }
}
```

```python
@dataclass(frozen=True)
class EchoCancellationConfig:
    enabled: bool = False
    stream_delay_ms: int = 0


@dataclass(frozen=True)
class NoiseSuppressionConfig:
    enabled: bool = False
    level: int = 2  # WebRTC aggressiveness 0 (mild) – 3 (max)


@dataclass(frozen=True)
class AudioProcessingConfig:
    echo_cancellation: EchoCancellationConfig
    noise_suppression: NoiseSuppressionConfig

    @property
    def any_enabled(self) -> bool: ...
```

Absent ⇒ all defaults ⇒ disabled. **Strict, like every other app-owned section:** an unknown key — in the section or in either feature — is a `ValueError` naming it, because the costly typo here is silent (`echo_cancelation` would otherwise load as "echo cancellation off", and the assistant would answer its own voice with nothing on screen to say why); a feature that is present but not an object (`"echo_cancellation": false`) is an error too, while `null`/absent means its defaults. Field types are validated (a string where an int belongs, a bool for an int field, or an out-of-range `level` raises a clear `ValueError`). The committed `config.computer.example.json` turns echo cancellation on with `stream_delay_ms: 50` and leaves noise suppression off (it can hurt as often as help; enable per environment). Grouping AEC and NS under one object matches the reality that both are features of the one WebRTC processor.

## Teardown

`ASREngine.stop()` stops the injected source (capture + pump), and `ComputerEmbodiment.exit()` calls `coordinator.close()`, which stops and closes the player's stream — so stopping the ASR bridge and exiting the body at app teardown ([app.md](app.md), "Wiring and lifecycle") is what releases the audio devices. ## Dependencies

`aec-audio-processing` (WebRTC APM, BSD; builds from sdist — needs a C++ toolchain, hence optional + lazy) and `samplerate` (MIT, prebuilt wheels, numpy-only) form the **`audio-processing` extra** ([project.md](project.md)), pulled into the `dev` group so the default `uv sync` / `uv run pytest` loop exercises them. `pyaec` is the documented fallback backend; the `FrameProcessor` interface makes that swap local.

## Testing

`tests/computer/test_audio_processing.py` — deterministic, no hardware ([testing.md](testing.md)):

- **Config:** an `audio_processing` block parses into `AudioProcessingConfig` (both features, partial sections, ns-level range, type errors); absent ⇒ disabled default; a misspelt feature, an unknown key inside a feature, and a non-object feature each raise, while a `null` feature keeps its defaults.
- **Processor (skips if the dependency is absent):** with echo cancellation, a delayed/attenuated copy of the far-end is cancelled from the near-end (steady-state ERLE materially positive) and near-end speech with a silent reference passes through; with noise suppression only, stationary noise is attenuated.
- **Coordinator (fakes, no devices):** `sink.feed` forwards to the player, and the reference is what the player reports played, not what was fed; `close()` closes the player; the source re-chunks a 1600-sample mic chunk into ten 160-sample `process` calls paired with the buffered far frames (silence when empty); output preserves the input chunk length.
- **`ContinuousPlayer` (a fake stream):** it plays what was fed and silence between, reporting only blocks that carry audio; `feed` blocks beyond its 100 ms lead; `drain` waits for the queue and keeps the stream open; `close` stops it; a stream that stopped on its own does not hang `drain`.
- **Wiring** — in `tests/computer/test_embodiment.py` ([computer-embodiment.md](computer-embodiment.md)): echo cancellation on wires sink + source and binds the far rate; NS-only wires the source but not the sink and never binds; a forced `ImportError` degrades to direct wiring with a `missing` line.

## Open questions

1. **Adaptive `stream_delay_ms`.** Per-device auto-calibration (measuring the round-trip at startup) is a deferral.
2. **Processing status in the UI.** "AEC / NS: on / degraded" on the ASR line — nice-to-have ([computer-embodiment.md](computer-embodiment.md), open question 2).
3. **A better canceller.** With the reference on time and one voice stream, `aec-audio-processing` 1.0.1 still lets short bursts of the app's voice through, during and after the person talking over it, measured in the end-to-end tier ([e2e.md](e2e.md), open question 4). WebRTC's current canceller (AEC3), as LiveKit's Python SDK ships it, is the candidate, compared offline on the tier's recorded audio first; the `FrameProcessor` interface keeps the swap local.
4. **Half-duplex fallback.** For hosts without the dependency, muting capture during `say` would still break the self-hearing loop; a lightweight second backend, deferred.
