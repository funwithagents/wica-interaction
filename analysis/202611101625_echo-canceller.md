# A better echo canceller for the computer embodiment — analysis (2026-11-10)

A working document, not a spec: it has no frontmatter and governs no code. It hands over the search for a better acoustic echo canceller (AEC) for the computer embodiment: why one is needed, what it must and must not do, which technologies fit the project's needs and license, how to evaluate them without hardware, and how the end-to-end tier can grow once one is in. The normative design is [specs/audio-processing.md](../specs/audio-processing.md) (open question 3, "A better canceller") and [specs/e2e.md](../specs/e2e.md) (open question 4).

## 1. The short version

- The computer plays the agent's voice through loudspeakers while its microphone stays open. Without echo cancellation the speech recognizer transcribes the app's own voice and the agent answers itself.
- The app runs WebRTC's audio processing module through the `aec-audio-processing` package (1.0.1, BSD-3-Clause). **It is already WebRTC's AEC3**: the binding wraps `libwebrtc-audio-processing-2` (the standalone packaging of WebRTC's module, as PulseAudio and PipeWire ship it) and enables `config.echo_canceller` with `mobile_mode = false`, on the default AEC3 settings. It exposes no tuning.
- Two faults in the app itself were found and fixed on 2026-11-10 (PR #10): the echo reference arrived 1.1 s late, and the echo's delay moved at each reply. Since then the reference is on time and the delay holds at 160–180 ms in CI, and the canceller removes a median 33 dB of echo.
- **What remains:** about 1.4 s of the app's voice per conversation still leaks through, in short bursts, during and just after the person talks over the app (double-talk) and on the short replies that follow. Replaying the recorded audio offline reproduces the leaks exactly, so they are the canceller's behaviour on these signals. The end-to-end tier records them and does not fail on them, for now.
- **Found since (section 6, "The CI recordings"):** part of CI's leaks came from the test setup moving the echo path, not from the canceller: pytest freezing the process ~300 ms at the end of a test, from ~17,000 recorded sounddevice warnings (the mic then ran ~87 ms behind, and AEC3 relearned for ~3 s), and the test room's two clocks drifting apart (~1 s of leaks per conversation). Both are fixed on the branch `worktree-echo-canceller`; what remains is double-talk.
- **The task:** find a canceller, or a configuration of this one, that removes those leaks without muting the person, on macOS, Linux and Windows, under a license compatible with the project's MIT. Evaluate it offline on the tier's recorded audio first. Then the tier's echo check can fail again on any leak.

## 2. Context

### The pipeline

```
 agent's say("…") ─▶ TTSEngine (chunks) ─▶ coordinator.sink.feed(chunk)
                                              │
                                              ▼
                               ContinuousPlayer: one output stream for the
                               app's lifetime, silence between replies
                                 │ stream callback, per block sent to the device:
                                 │   on_played(block) → resample → FAR buffer (16 kHz)
                                 ▼
                   speaker → room → mic          FAR buffer
                                 │                   │
                                 ▼                   ▼
                       mic capture ─▶ pump, per 10 ms frame (160 samples @ 16 kHz):
                                       far = pop 160 from FAR (zeros if empty)
                                       out = FrameProcessor.process(near, far)
                                 │
                                 ▼
                       ASR (Deepgram Flux) → speech_input → the agent
```

The code: [src/wica_interaction/computer/audio_processing.py](../src/wica_interaction/computer/audio_processing.py). The processor sits behind one interface, so a replacement is local:

```python
class FrameProcessor(Protocol):
    def process(
        self, near: bytes, far: bytes
    ) -> bytes: ...  # one 10 ms frame each, s16 mono @16 kHz
```

`WebRtcAudioProcessor` implements it today; `build_coordinator(config)` builds it from the config's `audio_processing` section (`echo_cancellation.enabled`, `echo_cancellation.stream_delay_ms`, `noise_suppression.enabled`, `noise_suppression.level`). The robot does not use any of this: its XVF3800 chip cancels echo in hardware, from a reference taken at the speaker output.

### What was measured (2026-11-10)

Everything below comes from the end-to-end tier ([specs/e2e.md](../specs/e2e.md)): the real app in CI on Linux, with the room process playing the app's voice back into the virtual mic 50 ms later and 10 dB quieter. `EchoRecorder` (in [tests-e2e/support.py](../tests-e2e/support.py)) saves what the canceller saw and produced as `audio.npz` (`near` = mic, `far` = reference, `out` = cleaned mic, int16 @ 16 kHz, frame-aligned).

| Stage | Echo lag (reference → mic) | Echo removed (median, while the voice plays) | What leaked |
|---|---|---|---|
| Reference queued after `AudioPlayer.feed` returned | −1.1 s (echo before its reference) | 0.8 dB | everything |
| Reference queued before the chunk is played | +140…+200 ms, but moving between replies | 53 dB | ~2.5 s after each delay change |
| `ContinuousPlayer`: one stream, reference from the callback (current) | +160…+180 ms, stable | 33 dB | ~1.4 s per conversation, in bursts |

Where the current leaks sit, from the last recording: during and right after the person's spoken "stop" over the story (double-talk), and on short replies right after it ("Stopped." transcribed as "Start."). Replaying offline with `stream_delay_ms` at 0, 50, 170 or 250 ms changed nothing, and adding −60 dBFS dither to the reference changed nothing either. The first is expected and proves little on its own: AEC3 estimates the delay itself and all but ignores the hint (it uses an external delay only with `delay.use_external_delay_estimator`, off by default; a synthetic replay at 0–500 ms gives the same residual within 0.1 dB). What rules timing out is the stable 160–180 ms lag measured in the recordings, and the replay reproducing the leaks exactly. The dither result rules out the reference going silent between replies.

Two more behaviours of AEC3, measured offline with synthetic signals:

- **With no echo at all** (a headset, or a cable with no echo path), AEC3 has nothing to learn from and treats near-end speech during playback as echo: it cut the person by 6–60 dB until it had heard about 8 s of the app's voice. Hence the documented rule: echo cancellation off with headphones.
- **With a simple room echo** (delayed copy at −10 dB), it converges in a second or two and lets the person through, apart from roughly the first quarter-second of speech (~40 dB cut).

### Facts to keep in mind

- The project is **MIT**-licensed ([LICENSE](../LICENSE)). Optional extras may pull other licenses when the README says so (`tts-piper` is GPL-3.0+, as an opt-in voice).
- The audio format at the processor is fixed: **16 kHz, mono, int16, 10 ms frames**. It is the robot's mic rate and the ASR's.
- The dev environment resolves on macOS arm64, Linux x86_64 and Windows x86_64 ([docs/reference/install.md](../docs/reference/install.md), "Platforms"); CI runs the fast tier on all three. `aec-audio-processing` builds from source on Linux and macOS (C++ toolchain), with wheels on Windows only.
- `onnxruntime` is already in the dependency tree (through the hand models), which matters for neural candidates.

## 3. What the canceller must do, and must not do

**Must:**

1. **Remove the steady echo**: at least ~30 dB in echo-only stretches, enough that the ASR transcribes nothing of the app's voice.
2. **Survive double-talk**: when the person talks over the app, let the person through (barge-in is a product feature: a spoken "stop" must reach the ASR), and keep cancelling the echo during and after it. This is where the current one fails.
3. **Converge fast**: within ~0.5–1 s of the first reply of a fresh app (stricter than today's AEC3, measured at 1–2 s), and recover within a few hundred ms from a change in the echo path.
4. **Be transparent with no echo**: with a headset, pass the person untouched. If it can't, the config rule "off with headphones" stays, but a canceller that needs no such rule is better.
5. **Fit the pipeline**: 16 kHz mono int16 in 10 ms frames, a far-end reference given separately, ≤ ~40 ms added latency (the ASR does not need conversational latency; AEC3 adds almost none, a neural stage like DTLN-aec ~32–40 ms), a small CPU share on a laptop (well under one core).
6. **Ship on the three platforms**, installable by `uv sync` (a wheel, or a source build CI can run), Python 3.12.
7. **Have a permissive license** (below).

**Must not:**

- **Gate the mic** (half-duplex: mute while the app speaks). That kills barge-in. It is only acceptable as a degraded fallback ([specs/audio-processing.md](../specs/audio-processing.md), open question 4).
- **Distort the speech the ASR hears**: aggressive residual-echo suppression or noise suppression that smears consonants raises the ASR's error rate. AGC stays off.
- **Need a cloud service, an account, a key or special hardware.**
- **Need the app to give up its own audio pipeline** unless the gain is decisive (the platform-native options below do).

## 4. Licensing

| License | Usable? |
|---|---|
| MIT, BSD-2/3-Clause, Apache-2.0, ISC, zlib | yes |
| LGPL (dynamically linked, unmodified) | yes with care: GStreamer already comes in this way through `reachy-mini`. Not for code we would modify and vendor |
| MPL-2.0 | yes, file-level copyleft only |
| GPL / AGPL | not in the default path; only as a clearly labelled opt-in extra, like `tts-piper` |
| Proprietary SDKs, per-seat or per-minute keys (Krisp, NVIDIA Maxine/Broadcast, Agora, Dolby.io, Picovoice's commercial engines) | no |
| Model weights | check the weights' own license, separately from the code's (some research models are CC-BY-NC: not usable) |

## 5. Candidates

Each needs its facts checked before any work: package name, wheels per platform, the exact license of code and weights, and the exposed API. "To verify" marks what this analysis did not confirm.

| # | Candidate | What it is | License | Platforms | Fit |
|---|---|---|---|---|---|
| A | **AEC3, tuned**: our own small binding to `libwebrtc-audio-processing-2` (or patching `aec-audio-processing`) exposing `EchoCanceller3Config` | Same canceller, with the knobs: the residual echo suppressor's tuning (normal and near-end), the delay estimator's settings (including `delay.use_external_delay_estimator`, since our delay is known and stable), the echo-path-change behaviour | BSD-3-Clause | all three, if we build it (meson + a C++ toolchain; Windows needs MSVC) | **High**: the leaks are a suppressor/double-talk behaviour, and the defaults are tuned for WebRTC calls, not for a voice agent. Cheapest experiment once a binding exists. Which config fields help is to find out |
| B | **LiveKit's Python SDK**, `livekit.rtc.AudioProcessingModule` | WebRTC's module from a recent libwebrtc build: echo cancellation, noise suppression, high-pass filter, gain control as on/off flags; `process_stream` / `process_reverse_stream` on 10 ms `AudioFrame`s, `set_stream_delay_ms` | Apache-2.0 (SDK); BSD-3 (WebRTC) | wheels for macOS, Linux, Windows (to verify per arch) | **Medium**: the same AEC3, a newer build, no tuning beyond on/off. Worth one offline replay, since it is a `pip install` away: a newer AEC3 may leak less |
| C | **SpeexDSP** (`speex_echo_cancellation` + `speex_preprocess` residual echo suppression), through a Python binding: `pyaec` (named in the spec as the fallback) wraps `aec-rs`, a Rust port of SpeexDSP, license and per-platform wheels to verify (PyPI showed Windows x86_64 and Linux aarch64); `speexdsp` (xiongyihui) builds from source and declares no standard license | Classic frequency-domain adaptive filter | BSD-3-Clause (SpeexDSP itself); the bindings' to verify | SpeexDSP builds everywhere; the bindings' wheels are patchy | **Low** as a replacement (weaker in double-talk than AEC3), useful as a baseline. As a fallback without a C++ toolchain, only once a binding with wheels and a clear license is found |
| D | **macOS Voice Processing I/O** (`kAudioUnitSubType_VoiceProcessingIO`, or `AVAudioEngine`'s `inputNode.setVoiceProcessingEnabled(true)`), through PyObjC | Apple's system canceller, the one FaceTime uses; owns both the input and the output | the OS (PyObjC is MIT) | macOS only | **High quality, high cost**: the app's speaker and mic must both go through the unit, replacing `ContinuousPlayer` and the capture on macOS; no CI (macOS runners have no audio devices); not usable with the virtual cable as tested today. A separate macOS backend |
| E | **Windows communications AEC**: the capture endpoint's AEC effect in communications mode, or the Voice Capture DSP (`CLSID_CWMAudioAEC`) | Microsoft's system canceller | the OS | Windows only | as D, for Windows: high quality, a platform backend, hard to test in CI |
| F | **PipeWire / PulseAudio `module-echo-cancel`** (it runs WebRTC's module itself) | A system virtual source with echo removed, given a sink | LGPL (the server); BSD (WebRTC) | Linux only | **Medium**: no code in the app (the user selects the echo-cancelled source), testable in our Linux CI with the null sinks. Same AEC3 underneath, so the same leaks unless its settings help (it exposes some `webrtc.*` arguments, but several — `extended_filter`, `delay_agnostic`, `experimental_*` — belong to the older AEC1, so whether any reaches AEC3 is to verify) |
| G | **A neural residual echo suppressor after AEC3**: DTLN-aec (Westhausen and Meyer, the ICASSP 2021 AEC challenge; 16 kHz only; pretrained in three sizes, 128/256/512 units, 1.8M/3.9M/10.4M parameters) | A small network fed the near and far signals that removes what the linear canceller left | MIT for code and weights, per forks and ports (to confirm on the official repo, `breizhn/DTLN-aec`) | the official models are TF-lite; for `onnxruntime` (already a dependency) they need a conversion (tf2onnx / tflite2onnx) or a community ONNX port, to verify. Otherwise `tflite-runtime`, a new dependency | **High potential**: built for exactly the residual and double-talk cases where AEC3 leaks; real-time on a CPU at the small sizes. Risks: ASR-relevant distortion, CPU, and latency: 32 ms windows moved by 8 ms, so ~32 ms algorithmic delay, plus re-blocking from 10 ms frames. Run as AEC3 → DTLN or DTLN alone, and compare |
| H | **Other neural AEC models** from the Microsoft AEC challenge and later work | Larger, often better scores | varies: many are research-only or CC-BY-NC | — | only after G, and only those with a permissive license on code and weights |

Ruled out: commercial SDKs (license), anything cloud-side (latency, privacy, keys).

**Suggested order:** A and G first (most likely to remove the leaks while staying cross-platform), B as a quick replay since it costs one install, C as the baseline. D and E only if the cross-platform options cannot reach the requirements; F as a documented Linux alternative.

## 6. How to evaluate, offline first

### Data

- **CI recordings.** Each e2e run of the computer with the echo on saves `audio.npz` per scenario in the run's evidence. CI uploads the computer's evidence on every run, kept 30 days ([specs/ci.md](../specs/ci.md)): start a manual run (`gh workflow run ci.yml --ref <branch>`) and download it (`gh run download <id> -n e2e-computer-evidence`). It also holds the room's log of its echo path (`room.npz`) and the app's stream logs (in `audio.npz`). A conversation recording is ~6 MB and covers every situation: replies, a spoken stop over a story (double-talk), a shown stop, a dictated note.
- **Synthetic sets**, built with the same voices as the tier (phonon `Micah` for the app, `Harper` for the person, via `TTSEngine(cfg, sink=<capturing sink>)`): far = the app's speech; near = echo (far delayed and attenuated, optionally convolved with a room impulse response; check the RIR dataset's license) + the person's clean speech at known times + optional noise. Because the person's clean signal is known, near-end preservation can be measured exactly. Variants: echo at −20/−10/−3 dB; delay 50–300 ms; a delay jump mid-run; the person interrupting 1 s into the first reply of a fresh canceller; continuous double-talk; no echo at all (the headset case).

### Metrics

| Metric | How |
|---|---|
| Echo removed (ERLE) | 10·log10(near power / out power) over echo-only windows (reference loud, person silent) |
| Leak time | seconds of echo-only 100 ms windows whose output stays above a threshold (the current canceller: ~1.4 s per CI conversation) |
| Near-end preservation | on synthetic sets: output vs the person's clean speech in double-talk windows (level ratio, and ideally PESQ/STOI — check those tools' licenses) |
| Convergence | time from the first reply's start until echo-only windows stay below the leak threshold |
| Headset transparency | no-echo set: output vs input level while the reference plays (should be ~0 dB) |
| ASR leaks (the real criterion) | run the outputs through the ASR and count transcribed words from the app's lines; costs Deepgram minutes, so on the shortlist only |
| CPU and latency | time per 10 ms frame on a laptop; algorithmic delay |

### Method

Replay is deterministic: feeding the same `near`/`far` frames to `WebRtcAudioProcessor` reproduced the live output exactly. So each candidate gets a `FrameProcessor` wrapper and the same replay loop, in the bench [echo_bench.py](echo_bench.py) (not shipped, not tested):

```
uv run python analysis/echo_bench.py synth <dir>                         # the synthetic sets, from the tier's phonon voices
uv run python analysis/echo_bench.py run <dir>/*.npz <audio.npz>... -c none,aec3
uv run --with livekit python analysis/echo_bench.py run <files> -c aec3,livekit   # candidate B, nothing added to the lock
```

A new candidate is one entry in its `CANDIDATES`. It reads both kinds of input: a CI recording (`near`, `far`, `out`, where the echo is predicted from the reference) and a synthetic set (`near`, `far`, `echo`, `person`, where the echo and the person are known). The synthetic sets play one ~30 s conversation (a request, a story stopped 4 s in, or 1 s in for `early`; short replies; the person talking through a whole reply) in ten rooms: `ci` (170 ms, −10 dB), `loud` (−3 dB), `quiet` (−20 dB), `reverb` (a synthetic 0.4 s tail), `jump` (170 → 260 ms at the midpoint), `drift` and `drift1k` (the delay growing at 250 and 1000 ppm), `early`, `noise` (−50 dBFS) and `headset` (no echo).

To measure the echo's lag in a recording (useful to check alignment for a new backend): correlate the log-energy envelopes (10 ms RMS) of `near` and `far` over ±3 s; a stable positive lag of 100–300 ms is healthy, a negative one means the reference is late. The bench prints it for each recording.

### First results (2026-11-10, synthetic sets only)

AEC3 (today's) and LiveKit's build (candidate B), 100 ms windows, leaks above −45 dBFS:

| Room | ERLE dB (AEC3 / LiveKit) | Leak s | Person cut > 6 dB in double-talk | Double-talk, median dB |
|---|---|---|---|---|
| ci | 60.7 / 60.1 | 0 / 0 | 26 % / 38 % | −1.4 / −1.7 |
| loud | 67.3 / 64.9 | 0 / 0.1 | 46 % / 46 % | −5.2 / −4.0 |
| quiet | 52.0 / 51.9 | 0 / 0 | 16 % / 16 % | −0.6 / −0.8 |
| reverb | 54.5 / 53.5 | 0 / 0 | 49 % / 49 % | −5.0 / −5.7 |
| jump | 59.8 / 58.0 | **0.8 / 0.6** | 6 % / 6 % | 0.1 / 0.2 |
| early | 60.4 / 59.5 | 0 / 0 | 25 % / 36 % | −1.1 / −1.5 |
| noise | 19.1 / 19.4 (the noise floor) | 0 / 0 | 21 % / 18 % | −1.2 / −1.3 |
| headset | — | — | 9 % / 9 % | −0.2 / −0.3 |

CPU: 0.11 ms (AEC3) and 0.04 ms (LiveKit) per 10 ms frame on an M-series Mac.

What it says:

- **Neither canceller leaks in a fixed, linear room**, even at −3 dB or with reverberation. The only leaks are after a **delay jump** (0.8 s from one 90 ms jump). So the ~1.4 s CI leaks need something these sets do not have. One candidate is the test room itself: [tests-e2e/room.py](../tests-e2e/room.py) cuts its echo backlog back to 50 ms whenever it passes 150 ms (`ECHO_SLACK_S`, the two devices' clocks drifting), which is a delay jump of up to 100 ms in the echo path. To check on the CI recordings: whether the echo's lag moves over a run, and whether the leaks sit right after those moves. If they do, part of what the tier reports is the room, not the canceller.
- **The leaks are not the only fault: the person is cut.** In double-talk, both cancellers cut the person by more than 6 dB in a quarter to half of the windows, worst with a loud echo or reverberation. That is the barge-in risk, and what a residual echo suppressor (candidate G) or AEC3's near-end tuning (candidate A) would have to improve.
- **LiveKit's build (B) is not better**: the same echo removal, slightly more cutting of the person in two rooms. It is faster, which does not matter at these numbers. B is ruled out as a fix.
- **The headset case did not reproduce** the strong muting measured earlier (6–60 dB until ~8 s of the app's voice): here only 9 % of the person's double-talk windows are cut. The person in these sets first talks over the app 4 s into its first reply, inside that window, so the difference is to explain (the synthetic voice, or the earlier measurement's signals) before the "off with headphones" rule is revisited.

### The CI recordings (2026-11-10, manual runs of the branch)

Manual runs of the branch `worktree-echo-canceller`, with the computer's evidence uploaded on every run. `echo_bench.py track` follows the echo's delay through each conversation (the waveform cross-correlation's peak, per 2 s window where the app speaks) and lists the leaks. From run 4 on, the evidence also logs the room's echo path (`room.npz`) and, from run 5, each callback of the app's own two audio streams (`audio.npz`, `capture` and `playback`: latency, underflow and overflow flags).

| Run | What changed | Computer job | Echo delay through the conversation | Leaks | The app heard itself |
|---|---|---|---|---|---|
| 1 | evidence always uploaded | failed, step 6: the agent answered the app's own story ("I think the Dragon's sword") with noop | 135 ms, **+87 ms in one step** after step 6's request, then creeping to 231 ms | 2.3 s, mostly in the 3 s after the jump | "Okay. I'm stopped.", "Got it.", the story fragment |
| 2 | — | failed, step 7: the dictated note arrived as several inputs, not an echo | 159 → 179 ms, creeping | 2.0 s | none |
| 3 | — | passed | 141 → 177 ms, creeping | 1.4 s | none |
| 4 | the room logs its echo path | passed | 140 ms, **+83 ms** after step 6's request, then creeping | 3.6 s | none |
| 5 | the app's streams logged (the mic only, a wiring slip) | passed | 139 ms, **+87 ms** after step 6's request, then creeping | 3.2 s | none |
| 6 | a temporary stall probe | passed | 179 → 194 ms, then −15 ms after a stall (the speaker underflowed, the mic held) | 1.5 s | none |
| 7 | **sounddevice's warning filtered** | passed | 198 → 226 ms, creeping, **no jump** | 1.6 s | none |
| 8 | **the room holds its delay** | passed | 181 → 188 ms in the first 26 s, then **flat at 188–189 ms** for 60 s | 2.2 s | none |

Replaying a recording through AEC3 offline reproduces its leaks (run 1: 2.2 s replayed, 2.3 s recorded): the leaks are AEC3's, on the signals it got. Two things moved those signals' echo path, and neither was the canceller or a room's acoustics.

**The jump: pytest freezing the process.** Every time, it came at the end of step 6's spoken request. The app's mic log (run 5) shows its capture callback arriving 321 ms late there, after which the mic stream stayed ~87 ms behind for the rest of the run (input latency 87 → 174 ms, no overflow, no sample lost): the reference is taken as the voice plays, so the echo now arrived 87 ms later against it, and AEC3 leaked for ~3 s while it relearned. The room's log (run 4) held at 97–99 ms over the same stretch: not the room. A stall probe (run 6) caught the freeze, 311 ms with as much CPU burnt, the main thread in pytest's end-of-test handling: sounddevice 0.5.6 raises a NumPy 2.5 `DeprecationWarning` in every audio callback (`data.shape = …`, fixed upstream in spatialaudio/python-sounddevice#641, not released), which a plain run ignores but pytest records, ~17,000 per conversation, and processes at the end of each test. Filtered for sounddevice alone in `pyproject.toml`, until a release after 0.5.6: in run 7, 4 warnings, no stall, no jump, no flagged callback. A test artifact, then, but one that shows a real fragility: any ~300 ms freeze of the app's process would do the same, since the canceller pairs the mic with the reference by arrival, not by time.

**The creep: the test room's two clocks.** The room records the app's speaker on one stream and plays into the cable on another; its log (run 4) shows its echo backlog growing 30 → 50 ms over the run (its share of the delay 85 → 108 ms), the two streams' clocks ~250–400 ppm apart. A laptop's speakers and mic share one sound card's clock, so its echo path does not creep; the `drift` synthetic room (250 ppm) puts the cost at ~1 s of leaks per conversation (AEC3's echo removal 61 → 50 dB). The room now holds the echo's delay, from recording to playback ([specs/e2e.md](../specs/e2e.md), "The echo's delay holds"): offline, with a recording clock 0, ±400 or +1000 ppm off and a 90 ms late burst, it holds within 0.1 ms, where the old room went 50 → 103 ms over 40 s. In CI (run 8) the echo's delay seen by the app no longer creeps, and the room needed no hard correction. The leaks did not drop with it, though: 2.2 s, against 1.4–2.0 s in the runs without a jump, so the ~1 s the `drift` room predicts is within the spread between runs. The tier now measures the canceller on a fixed echo path; it takes several runs to compare candidates on it.

**What remains**, once both are gone, is the canceller's own: short leaks while and just after the person talks over a story (run 7: 28–33 s, around the spoken stop) and right after some short replies. That is the double-talk case the candidates are for (G first, then A), now measured on a fixed echo path.

**Drift is still a real case**, for a USB speaker with the built-in mic, or Bluetooth (two clocks, 50–500 ppm): it stays in the bench (the `drift` rooms) and is a later variant of the room, on purpose, with its own expectations.

Suggested acceptance, for the tier's echo check to fail again: zero ASR-transcribed leaks over ten CI conversations; ERLE ≥ 30 dB in echo-only windows; near-end preserved within ~3 dB in double-talk outside the first ~250 ms; the headset set passes at ~0 dB, or the rule "off with headphones" is kept explicitly.

## 7. Integrating the winner

- A new `FrameProcessor` in `audio_processing.py`, built by `build_coordinator`. If more than one backend stays, a config field (for instance `audio_processing.echo_cancellation.backend`) chooses, with the current one as default. That is a spec change in [specs/audio-processing.md](../specs/audio-processing.md) and [docs/reference/configuration.md](../docs/reference/configuration.md).
- The dependency goes in the `audio-processing` extra (or a new extra), and the dev group if CI is to exercise it. A source build needs CI's toolchain on Linux and macOS ([specs/ci.md](../specs/ci.md)).
- A platform-native backend (D, E) replaces the player and the capture on its platform, not just the processor: a larger change to the coordinator, and to the embodiment's mic.
- Fast tests: the existing processor tests (echo removed from a delayed copy, near-end passed with a silent reference) for the new backend, skipping when its dependency is absent.

## 8. How the end-to-end tier grows after

Today's tier ([specs/e2e.md](../specs/e2e.md)): the room plays the app's voice back at 50 ms and −10 dB; step 8 of the computer's conversation records any leak in the report, without failing.

With a canceller that meets the requirements:

1. **Step 8 fails again** on any of the app's words heard as input: the check exists ([tests-e2e/scenarios.py](../tests-e2e/scenarios.py), `never_heard_itself`), only its assertion was relaxed.
2. **The echo becomes a measured quantity.** The run computes ERLE and leak time from `audio.npz` and puts them in the report, with thresholds asserted. A regression in the canceller or the player then fails CI with a number.
3. **Harder rooms.** The room process ([tests-e2e/room.py](../tests-e2e/room.py)) takes parameters: louder echo (−3 dB), a longer delay, reverberation (convolving an impulse response), background noise, and **a delay jump mid-conversation**. Each is a conversation variant, or a parametrized run.
4. **Barge-in early.** A step interrupting 1 s into the very first reply of a fresh app, before any convergence. The current canceller would lose that.
5. **Continuous double-talk.** A step where the person talks through a whole reply, checking that both the person's words arrive and none of the app's.
6. **The headset case with echo cancellation on**: no echo, canceller on, the person must pass. That decides whether the "off with headphones" rule can go.
7. **Locally on a Mac**: with BlackHole 16ch as the app's speaker (`brew install blackhole-16ch`, `WICA_E2E_SPEAKER_DEVICE="BlackHole 16ch"`), the room puts the echo on the Mac as in CI.
8. The robot is unaffected: its canceller is the XVF3800 in hardware, and the sim has no echo.

## 9. Pointers

- Design: [specs/audio-processing.md](../specs/audio-processing.md) ("Far-end path", open questions), [specs/e2e.md](../specs/e2e.md) ("The room", open question 4), [specs/computer-embodiment.md](../specs/computer-embodiment.md).
- Code: [src/wica_interaction/computer/audio_processing.py](../src/wica_interaction/computer/audio_processing.py) (`FrameProcessor`, `WebRtcAudioProcessor`, `ContinuousPlayer`, `AudioProcessingCoordinator`), [tests/computer/test_audio_processing.py](../tests/computer/test_audio_processing.py).
- The tier: [tests-e2e/room.py](../tests-e2e/room.py), [tests-e2e/support.py](../tests-e2e/support.py) (`EchoRecorder`, `echoes`), [tests-e2e/scenarios.py](../tests-e2e/scenarios.py), [tests-e2e/configs/computer.json](../tests-e2e/configs/computer.json).
- History: PR #10 (the reference and the voice stream), PR #12 (the tier); the plan [plans/202611100025_e2e-tier.md](../plans/202611100025_e2e-tier.md), "The computer's echo on Linux".
- Corrections already carried into the specs: the current canceller is AEC3, so the candidate is AEC3 **tuned** or a different technology (PR #13, [specs/audio-processing.md](../specs/audio-processing.md) open question 3, [specs/e2e.md](../specs/e2e.md) open question 4); and `stream_delay_ms` is close to a no-op for AEC3 on its defaults ([specs/audio-processing.md](../specs/audio-processing.md), "Signal facts", point 3; the guide and the troubleshooting page no longer say to tune it).
