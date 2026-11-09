---
code:
  - tests-e2e/conftest.py
  - tests-e2e/support.py
  - tests-e2e/person.py
  - tests-e2e/configs/computer.json
  - tests-e2e/configs/reachy.json
  - tests-e2e/fixtures/README.md
tests:
  - tests-e2e/computer/test_e2e.py
  - tests-e2e/reachy/test_e2e.py
---

# End-to-end tier

**Status:** Draft

## Purpose

This tier tests the assembled product with **every module real**: the app, the ASR, the WICA agent and its LLM, the TTS, the hand models, the robot software and the MuJoCo sim. **Only the physical devices are replaced:**

- the person's voice reaches the mic through a virtual audio cable;
- a hand reaches the camera as a photo: a file the computer's webcam reads, or a prop in the robot's sim scene;
- the robot is the sim.

It answers the question the other tiers cannot: *does the product work once assembled?* That covers the connections between modules, the timings and formats, startup and teardown, and the degrade paths. It keeps one model fixed.

It is a part of the live tier of [testing.md](testing.md): it lives in `tests-e2e/`, it is not collected by the default `pytest`, and it skips without its keys and devices. It replaces `tests-e2e/test_gesture_input_live.py`: the hand scenario puts the same photos through the real camera paths.

| Tier | What is real | What is replaced |
|---|---|---|
| Fast (`tests/`) | the app's own code | every provider, device and body |
| Benchmark (planned) | the app and the LLM under test | speech in and out, hand signs, the body |
| **End-to-end (this spec)** | **everything: app, ASR, LLM, TTS, hand models, robot software, sim** | **the mic, the speaker, what the camera sees, the robot's hardware** |
| Desk run | everything, including the hardware and the room | nothing |

## Decided

### The providers

One fixed set, chosen so that only the LLM and the ASR cost anything per run:

- **The LLM: Claude Haiku 5.5** — `provider: "anthropic"`, `model: "claude-haiku-5-5"`, the key in `WICA_ANTHROPIC_API_KEY`. It is fast and cheap, and its provider extra is already in the dev group.
- **The ASR: Deepgram Flux** — `deepgram_v2`, `flux-general-en`, the key in `DEEPGRAM_API_KEY`. This is the production ASR. asr-engine's local `kyutai` module is ruled out: its backends pin `numpy<2.3` against vision-modules' `numpy>=2.5.3`, so declaring it makes the lock unsolvable ([project.md](project.md)).
- **The voices: phonon**, for both the app and the person. It is tts-engine's lightest local model: no PyTorch, no key, and a public English checkpoint of about 160 MB that runs many times faster than real time on a CPU. The dev group gains the `tts-phonon` extra. The app and the person use two different voices, so a transcript always tells them apart.
- **Everything is in English.** phonon's public checkpoint is English-only. Other languages are left to manual runs.

The production providers (the example configs' providers) are not this tier's concern. Running the same scenarios with them is a separate scheduled job, in [ci.md](ci.md).

### The configs

- **One committed config per embodiment**: `tests-e2e/configs/computer.json` and `tests-e2e/configs/reachy.json`. Each holds the providers above, with keys read from the environment, and the template's persona (`prompts/computer.md`, `prompts/reachy.md`).
- **The fixture fills in what the run controls**: the device names (below), the computer's camera file, and the robot's connection block (`robot_options(host, port)` from the bridge's harness, with `daemon.spawn: "never"`).
- **Each config is loaded through the embodiment's own `load_config`**, so a config the app would reject fails the tier at its first step.

### Replacing the devices

#### The virtual audio cable

The tier names its devices through three environment variables:

| Variable | What | macOS | Linux |
|---|---|---|---|
| `WICA_E2E_MIC_DEVICE` | the input device the app listens on: the cable's recording end | `BlackHole 2ch` | a remapped source over the cable sink's monitor |
| `WICA_E2E_PERSON_VOICE_DEVICE` | the output device the test plays the person's voice into: the cable's playback end. When unset, `WICA_E2E_MIC_DEVICE` | unset | the cable's null sink |
| `WICA_E2E_SPEAKER_DEVICE` | the output device the app speaks to. When unset, the default output, so the robot can be heard while iterating | unset, or a second virtual device | a second null sink |

**A cable has two ends, and on Linux they have two names.** A virtual cable is one output device that something plays into and one input device that records what was played. BlackHole shows both ends under one name, so on macOS `WICA_E2E_MIC_DEVICE` is enough. On Linux the playback end is a PulseAudio null sink and the recording end is a separate source, each with its own name, so the person's voice needs its own variable there.

On Linux the mic is a remapped source, not the monitor itself, because upstream `reachy-mini`'s device matcher skips monitor sources.

**The app's speaker is never the cable.** BlackHole loops its output back to its input, so an app speaking into the cable it listens on would hear itself. The echo scenario is the one exception, and it sets this up on purpose.

#### The computer embodiment

| Device | Replacement | Set through |
|---|---|---|
| Mic | the cable | `asr.engine.audio.device` = `WICA_E2E_MIC_DEVICE` |
| Speakers | `WICA_E2E_SPEAKER_DEVICE` | `tts.engine.player.device` |
| Webcam | one image file, rewritten to change what the camera sees | `camera.device` = the file's path |

**A file as the webcam needs no code change.** OpenCV opens a file path as a camera: it yields the image once, and the next read fails. `WebcamSource` records the failed read as a capture error and reopens the device, so the file is read again at the source's reopen rate. To change what the camera sees, the person replaces the file's contents atomically (writes a temporary file, then renames it over the path). "Nothing shown" is the `no_hand` photo.

#### The Reachy Mini embodiment

| Device | Replacement | Set through |
|---|---|---|
| Robot | the MuJoCo sim the bridge's harness runs | the harness's `live_daemon`; our embodiment builds its own bridge over it with `robot_options` |
| Robot's mic | the cable | `REACHY_MINI_E2E_AUDIO_INPUT` = `WICA_E2E_MIC_DEVICE` |
| Robot's speaker | `WICA_E2E_SPEAKER_DEVICE` | `REACHY_MINI_E2E_AUDIO_OUTPUT` |
| A hand in front of the robot | a hand photo as a prop, 0.36 m wide, at (0.45, 0, 0.20) m | `pytest_reachy_mini_scene_props` and `sim_scene.spawn(name=…)` |

- **The conftest copies our variables into the bridge's.** It sets `REACHY_MINI_E2E_AUDIO_INPUT` and `_OUTPUT` from `WICA_E2E_MIC_DEVICE` and `WICA_E2E_SPEAKER_DEVICE` before the harness spawns its daemon, so one set of names drives both embodiments.
- **The sim's audio devices are checked when the session starts.** The daemon reports the devices it opened on `GET /api/bridge/daemon`. A session fixture reads that report and fails the run when the input is not `WICA_E2E_MIC_DEVICE`. Without this check, a daemon borrowed from an earlier run, started without the cable, would leave the robot listening to the real mic, and every spoken scenario would time out with no clear cause.
- **The hand props.** The conftest's `pytest_reachy_mini_scene_props` declares one pool, `image_pool("hand", …, width=0.36)`: one prop per photo, `hand_1` … `hand_5`, hidden when the daemon boots. The photos are converted to PNG at session start with OpenCV, since props must be PNG. `Person.show` spawns a photo's prop by name at the position above. The tests are gated with `@pytest.mark.requires("camera", "props:hand")`.
- **The hand position is measured.** In a spike on 2026-11-09, the real gesture pipeline read the sim's render of all five photos correctly, with scores of 0.71–0.83, at every distance from 0.30 m to 0.60 m. Beyond that the detector loses the hand: the failure is "no hand", never a wrong sign. 0.45 m, the bridge's default prop position, is the middle of the reliable band. The props' material is emissive, so lighting is not a variable.

#### The hand photos

The five hand photos and the `no_hand` photo are copied from vision-modules' live-tier fixtures (`vision-modules/tests-e2e/fixtures/`, original photos under its MIT license) into `tests-e2e/fixtures/`, with a README that records where they came from. They are committed here, rather than read from a sibling checkout as the old live gesture test did, because CI has no sibling checkout.

| Photo | Sign delivered |
|---|---|
| `hand_stop.jpg` | `stop` |
| `hand_fist.jpg` | `stop` |
| `hand_palm.jpg` | `five` |
| `hand_one.jpg` | `one` |
| `hand_three2.jpg` | `three` |

### The person

A `Person` helper (`tests-e2e/person.py`) acts for the human in front of the app:

- **`say(text)`** speaks `text` into `WICA_E2E_PERSON_VOICE_DEVICE` with its own phonon engine and returns when playback ends.
- **`show(photo)`** puts a hand photo in front of the camera: it rewrites the camera file on the computer, or spawns the matching prop on the robot.
- **`hide()`** takes the hand away: it writes the `no_hand` photo on the computer, or clears the scene on the robot.

Each scenario's text is fixed in its test, so a failing run can be replayed with the same input. The speech itself still varies slightly from run to run, and the ASR copes with that the same way it copes with a person.

### The scenarios

Each scenario builds and starts the real system, lets the person act, and asserts on the agent's timeline through `wica.testing.TestRun(system.wica)`. The assertions are about behaviour, never exact strings ([testing.md](testing.md)).

| # | Scenario | Embodiment | The person | Expected |
|---|---|---|---|---|
| 1 | Startup | both | nothing | the system builds with nothing in `missing`; the agent, the ASR bridge and the gesture input run; on the robot, the bridge is connected |
| 2 | Spoken turn | both | "Hello, what is your name?" | a final `speech_input` containing "name", then a `say` issued and completed |
| 3 | Hand sign | both | nothing at first; then each photo in turn, hiding it between | no `hand_gesture_1` while nothing is shown; then `hand_gesture_1` equals each photo's sign; the agent reacts to each one |
| 4 | Emotion | robot | "Can you look happy?" | `play_emotion` issued with `happy` and completed, plus a `say` that does not overlap it |
| 5 | Interrupt | both | asks for a long story; then, while the app is speaking, says "stop" or shows `stop` | the running `say` is cancelled |
| 6 | Dictation | both | the dictation trigger, a passage, the end phrase | dictation starts, the passage arrives as one input, and dictation stops |
| 7 | Echo | computer | speaks while the app is speaking, with both voices on the cable | the app's own words never appear as a `speech_input` |

- **Scenario 1 is the cheapest**, and it catches most wiring breakage. Scenarios 2 and 3 are the core of the tier.
- **The vision check comes before the agent check.** Scenario 3 asserts the World's `hand_gesture_1` first and the agent's reaction second, as two separate expectations. A vision failure then shows up as one, and an LLM failure cannot hide it. This keeps the verdict the old live gesture test gave without any LLM.
- **The echo scenario wires the cable on purpose.** The app's speaker is `WICA_E2E_PERSON_VOICE_DEVICE`, the same cable it listens on, so echo cancellation is the only thing that keeps the app from hearing itself. The far-end reference is then exact.
- **Time limits are generous and asserted as timeouts**, through the `within=` windows of the `expect_*` calls. Latencies are recorded in the report but not asserted; `expect_latency` is not used.
- **The framework's checks run after every scenario.** `wica.testing.checks` holds rules that apply to any finished timeline: every input is answered, no input is met with a no-op, no reaction fails, no cancel collides with a running action. They run over each scenario's timeline through `apply_checks`, adding broad assertions at no extra cost.
- **The scenarios are plain pytest code, not WICA scenario files.** A scenario file's tester can only act through the fixed tester Commands, and its `set_<key>` Commands write the inputs straight into the World. That would bypass the ASR and the hand models, which are what this tier exists to test.

### Layout and fixtures

```
tests-e2e/
  conftest.py          # loads the bridge's pytest plugin; the env, device and prop setup; shared fixtures
  support.py           # require_env, and WICA_E2E_FAIL_ON_MISSING
  person.py            # Person
  configs/             # computer.json, reachy.json
  fixtures/            # the hand photos, no_hand, README
  computer/test_e2e.py # scenarios 1, 2, 3, 5, 6, 7
  reachy/test_e2e.py   # scenarios 1–6
```

- **One directory, one run.** `tests-e2e/conftest.py` loads the bridge's plugin (`reachy_mini_bridge.testing.fixtures`) for the whole tier. The computer's tests request none of its fixtures, so they never start a sim. `uv run pytest tests-e2e` runs both embodiments, and either subfolder can be run on its own.
- **`app_system(embodiment)`** builds the real system with `build_system` (no seams: the real ASR and the real hand models), wires a transcript collector, and starts it. It yields the system together with its `TestRun`, then shuts it down with `_shutdown` in a `finally`, as [testing.md](testing.md) requires of every `build_system`.
- **`person`** yields a `Person` bound to the embodiment's camera (file or scene).
- **The robot's session fixtures**: the audio-device check, and the PNG conversion of the photos for the props hook.

### Skips and failures

- **By default, a missing prerequisite skips**, as in the rest of the live tier: a missing key through `require_env`, a missing cable device when its first open fails, a missing sim capability through the bridge's `requires` gate. A local run with only part of the setup then passes on what it can run.
- **`WICA_E2E_FAIL_ON_MISSING=1` turns each of those skips into a failure.** In CI a skip would turn the job green with nothing tested: a secret not passed, a null sink not created, a sim that came up without its camera. With the variable set, the run fails and names what is missing. The conftest also sets the bridge's `REACHY_MINI_E2E_REQUIRED_CAPS` to the capabilities this tier needs (`camera`, `props:hand`), the bridge's own switch for the same rule. CI sets the variable; a local run usually does not.
- **A failed run is re-run before it is believed.** Retries are not built in, so the report shows every failure, flaky ones included.

### Evidence on failure

A failure in this tier is otherwise hard to diagnose, so each scenario writes its evidence to pytest's `tmp_path`:

- the transcript;
- the `TestRun` report (its timeline);
- the camera frames the gesture input acted on, through the app's existing snapshot writer.

Recording the cable is an open question (below).

### Platforms

The tests are the same on every platform; only the setup differs.

| | macOS (iteration) | Linux (CI) |
|---|---|---|
| Sim camera | the viewer window: `REACHY_MINI_E2E_SIM_VIEWER=1`, an unlocked GUI session, `mjpython` | headless, rendered through EGL |
| Cable | BlackHole 2ch | a PulseAudio null sink, plus a remapped source over its monitor |
| App's speaker | the default output, or a second virtual device | a second null sink |
| Permissions | microphone access for the terminal or editor | none |

- **The first viewer launch on macOS takes about 62 s** while GStreamer builds its plugin cache. The harness gives up after a fixed 45 s. The workaround is to start the sim daemon by hand once; the harness then borrows it. Later launches take about 8 s.
- **Windows is not part of this tier.** It would need VB-Audio Virtual Cable and a GUI session for the sim. A manual check on an x86-64 Windows machine validates the app there instead (below).

### Cost and cadence

- **Each run calls two paid services**, the LLM and the ASR. A full run makes a few dozen LLM reactions per embodiment, each over a persona and primer of several thousand tokens. The framework's token counts give the cost per run once the tier exists.
- **A run is slow**: the model downloads on the first run (about 340 MB of hand models plus the phonon checkpoint), the sim's startup, and speech at real-time speed. Expect 5 to 10 minutes per embodiment once the caches are warm.
- **A run is non-deterministic**: a misheard phrase, or the model choosing another action, can fail a run without a bug. The assertions stay behavioural and the time limits generous.
- **Where it runs** is the CI spec's decision ([ci.md](ci.md)): on Linux, on every pull request and push to `main`, but not as a required check until its history shows it stable.

### The manual Windows check

The fast tier proves the install and the device-free code on Windows ([ci.md](ci.md)). It cannot prove real audio, the webcam or the sim. One manual run on an x86-64 Windows machine does, following this checklist:

1. `uv sync` and the fast tier pass.
2. The computer embodiment starts with nothing in `missing`. A spoken turn through the real mic and speakers gets a spoken reply, a hand sign shown to the webcam reaches the agent, and echo cancellation keeps the app from hearing itself.
3. The Reachy embodiment starts against the viewer sim (`daemon.headless: false`). A spoken turn gets a reply through the sim's audio, and the agent plays an emotion on request.
4. Shutdown leaves no daemon or audio stream behind.

Windows on Arm (for example in Parallels on an Apple silicon Mac) is a different platform, where MediaPipe and onnxruntime may have no wheels, so a failure there says little about Windows itself.

## Relationship to the other specs

- **[testing.md](testing.md)**: the two tiers, and the rules this tier follows (skip without credentials, assert behaviour, shut down what you build).
- **[ci.md](ci.md)**: where and when this tier runs.
- **[app.md](app.md)**: `build_system`, `wire` and the lifecycle the fixture drives.
- **[computer-embodiment.md](computer-embodiment.md)**, **[reachy-embodiment.md](reachy-embodiment.md)**: the bodies whose devices are replaced.
- **[gesture-input.md](gesture-input.md)**: the sign vocabulary and the `hand_gesture_<n>` entries scenario 3 asserts.
- **The model benchmark** (planned): a later agent tier reuses the benchmark's bench embodiment and scenario files to test the agent's behaviour on the production model, with the devices bypassed. It is built after this tier.

## Open questions

1. **The file as the webcam.** It needs no code change, but the capture error at each reopen has not been watched through `GestureInput`. The sign must survive the 0.5 s hold, and the stall line must not fire. If it does not behave, the alternative is an injectable frame source in `ComputerEmbodiment`, which would replace real code in the test.
2. **The echo scenario on BlackHole.** It assumes one cable can carry both voices, and that the echo-cancellation coordinator, given its exact far-end reference, removes the app's voice. Confirm this on the Mac before the scenario is written.
3. **The phonon voices** for the app and the person: two of the public checkpoint's voices, chosen when the configs are written.
4. **Recording the cable.** A recording of what the mic heard would be the best evidence for a failed spoken scenario. The cost is one more input stream on the cable during the run.
5. **The harness's startup timeout.** It is fixed at 45 s, which a first macOS launch exceeds. This is raised with the bridge. Until it is fixed, the workaround is a daemon started by hand.
