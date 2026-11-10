---
code:
  - tests-e2e/conftest.py
  - tests-e2e/support.py
  - tests-e2e/person.py
  - tests-e2e/voice.py
  - tests-e2e/scenarios.py
  - tests-e2e/configs/computer.json
  - tests-e2e/configs/reachy.json
  - tests-e2e/fixtures/README.md
tests:
  - tests-e2e/test_computer.py
  - tests-e2e/test_reachy.py
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
- **The voices: phonon**, for both the app and the person. It is tts-engine's lightest local model: no PyTorch, no key, and a public English checkpoint of about 160 MB that runs many times faster than real time on a CPU. The dev group gains the `tts-phonon` extra. The app speaks as `Micah` and the person as `Harper`, so a transcript always tells them apart.
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

**The person's voice plays from a process of its own.** On macOS, a process that plays into the cable stalls its own capture of the cable: the capture delivers a chunk or two and then nothing, and reports no error, so the ASR waits in silence. The app's capture is PortAudio on the computer and the bridge's GStreamer on the robot; both stalled whenever the person's voice played from the app's process, whatever the open order or the cable's rate, and neither did once it played from another. So `Person` runs its phonon engine in a child process (`tests-e2e/voice.py`), which speaks each line it is sent and answers once it has been played, and which starts on the first `say`. The person is not inside the app either. (Measured on 2026-11-10; a first fix that set the cable to the app's 16 kHz recording rate only made the failure rarer.)

#### The computer embodiment

| Device | Replacement | Set through |
|---|---|---|
| Mic | the cable | `asr.engine.audio.device` = `WICA_E2E_MIC_DEVICE` |
| Speakers | `WICA_E2E_SPEAKER_DEVICE` | `tts.engine.player.device` |
| Webcam | one image file, rewritten to change what the camera sees | `camera.device` = the file's path |

**A file as the webcam.** OpenCV opens a file path as a camera: it yields the image once, and the next read returns no frame without raising. `WebcamSource` treats a device that delivered frames and then yields none as a stream that ended, and reopens it in the same poll ([computer-embodiment.md](computer-embodiment.md), "`WebcamSource`"), so the file is read again at every poll, with no gap and no capture error. Building this tier found that rule missing: the webcam source used to go silent after the file's one frame, with a clean status, as a real webcam unplugged mid-stream would have. To change what the camera sees, the person replaces the file's contents atomically (writes a temporary file, then renames it over the path). "Nothing shown" is the `no_hand` photo.

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

- **`say(text)`** speaks `text` into `WICA_E2E_PERSON_VOICE_DEVICE` with its own phonon engine, in a process of its own (above), and returns when playback ends.
- **`show(photo)`** puts a hand photo in front of the camera: it rewrites the camera file on the computer, or spawns the matching prop on the robot.
- **`hide()`** takes the hand away: it writes the `no_hand` photo on the computer, or clears the scene on the robot.

Each scenario's text is fixed in its test, so a failing run can be replayed with the same input. The speech itself still varies slightly from run to run, and the ASR copes with that the same way it copes with a person.

### The scenarios

Each scenario builds and starts the real system, lets the person act, and asserts on the agent's timeline through `wica.testing.TestRun(system.wica)`. The assertions are about behaviour, never exact strings ([testing.md](testing.md)).

| # | Scenario | Embodiment | The person | Expected |
|---|---|---|---|---|
| 1 | Startup | both | nothing | the system builds with nothing in `missing`; the agent, the ASR bridge and the gesture input run; on the robot, the bridge is connected |
| 2 | Spoken turn | both | "Hello, what is your name?" | a final `speech_input` containing "name", then a `say` issued and completed |
| 3 | Hand sign | both | nothing at first; then each photo in turn, hiding it between | no `hand_gesture_1` while nothing is shown; then `hand_gesture_1` equals each photo's sign, and a reaction of the agent has it among its triggers |
| 4 | Emotion | robot | "Can you show me that you are happy?" | `play_emotion` issued and completed, and a `say`; a `say` running at the same time as the emotion is recorded in the report, not failed on |
| 5 | Interrupt | both | asks for a long story; then, while the app is speaking, says "Stop, please. That's enough." (one test) or shows `stop` (another) | the running `say` is cancelled |
| 6 | Dictation | both | asks to dictate a note; a second after dictation starts, two sentences with a pause between them, then, after a clear pause, "Send.", a trigger word (both configs end a passage on one, as both templates do) | `start_dictation` completes and dictation is on; the passage arrives as one `speech_input` holding both sentences; dictation is then off |
| 7 | Echo | computer | speaks while the app is speaking, with both voices on the cable | the app's own words never appear as a `speech_input` |

- **Scenario 1 is the cheapest**, and it catches most wiring breakage. Scenarios 2 and 3 are the core of the tier.
- **The vision check comes before the agent check.** Scenario 3 asserts the World's `hand_gesture_1` first and the agent's reaction second, as two separate expectations. A vision failure then shows up as one, and an LLM failure cannot hide it. This keeps the verdict the old live gesture test gave without any LLM.
- **The echo scenario wires the cable on purpose.** The app's speaker is `WICA_E2E_PERSON_VOICE_DEVICE`, the same cable it listens on, so echo cancellation is the only thing that keeps the app from hearing itself. The far-end reference is then exact.
- **Time limits are generous and asserted as timeouts**, through the `within=` windows of the `expect_*` calls. Latencies are recorded in the report but not asserted; `expect_latency` is not used.
- **What the inputs delivered is read from the World.** The harness's timeline records a trigger for each write but not its value, and `expect_world` only sees an entry's current value, which a TTL clears a moment later. An `InputLog` (a World listener on `speech_input` and the `hand_gesture_<n>` entries) records each value written, so a scenario asserts what the ASR heard and which sign the hand models delivered.
- **The agent may ignore a sign.** The persona lets it decide whether a sign was meant for it, so a count can be met with `noop`. Scenario 3 asserts that the agent reacted (a reaction triggered by the sign), not what it did. `stop` is the exception the persona fixes: it is answered out loud.

- **The agent settles before the next input.** WICA drops a trigger that arrives while a reaction is in flight (the framework's open concurrency question): a sign shown while the agent still reasons about the completion of its last reply is lost. Scenario 3 hit it on a second `stop` shown about a second after the first reply ended. So between inputs a scenario waits until no reaction is in flight and no Command runs, for a second in a row, as a person waits for the app to finish. A person who does not wait loses that input: a limit of the framework, recorded here, not worked around.
- **The framework's checks run after every scenario**, over the whole timeline, through `wica.testing.run_check`: no reaction fails (`no_failed_reactions`), no cancel collides with a running action (`no_colliding_cancel`), and no spoken input is met with a no-op alone (`no_noop_on_input`, over `speech_input` only, since a sign may be ignored). `answers_each_input` is left out: it counts a turn from an `input_written` event, which the harness records only for the tester's own writes, and here the app's inputs write the World, so it would pass over zero turns.
- **The scenarios are plain pytest code, not WICA scenario files.** A scenario file's tester can only act through the fixed tester Commands, and its `set_<key>` Commands write the inputs straight into the World. That would bypass the ASR and the hand models, which are what this tier exists to test.

### Layout and fixtures

```
tests-e2e/
  conftest.py       # the bridge's plugin, the sim's devices and props; the devices, the keys, each embodiment's fixture
  support.py        # require_env, require_device, WICA_E2E_FAIL_ON_MISSING, InputLog, the checks
  person.py         # Person, FileCamera, SceneStage, the photos and their signs
  voice.py          # the person's voice, a process of its own
  scenarios.py      # the scenarios, written once, run by each embodiment's file
  configs/          # computer.json, reachy.json
  fixtures/         # the hand photos, no_hand, README
  test_computer.py  # scenarios 1, 2, 3, 5, 6, 7 (1–3, 5, 6 so far)
  test_reachy.py    # scenarios 1–6
```

- **One directory, one run.** `tests-e2e/conftest.py` loads the bridge's plugin (`reachy_mini_bridge.testing.fixtures`) for the whole tier. The computer's tests request none of its fixtures, so they never start a sim. `uv run pytest tests-e2e` runs both embodiments, and either file can be run on its own. The files sit flat in `tests-e2e/`, which is not a package, so they import `support` and `person` as the type checker resolves them: from their own directory.
- **One fixture per embodiment** (`computer`, `reachy`) builds the real system with `build_system` (no seams: the real ASR and the real hand models) from its committed config with the run's values filled in, asserts nothing is in `missing`, wires a transcript collector, and starts it. It yields an `E2E`: the system, its `TestRun`, its `InputLog` and the `Person` in front of it. It writes the evidence, then shuts the system down with `_shutdown` in a `finally`, as [testing.md](testing.md) requires of every `build_system`.
- **`devices`** and **`keys`** (session) resolve the devices and the keys, skipping, or failing under `WICA_E2E_FAIL_ON_MISSING`, when one is missing.
- **The robot's fixture** builds its own bridge over the harness's daemon (`live_daemon`, with `robot_options` for the connection) and takes `sim_scene` for the hand props, clearing the scene before and after. Its config turns face detection and head tracking off, so no face in the scene pulls the head away from the hand props; presence, breathing and wobbling stay on, as in the template. The plugin is registered from the conftest's `pytest_configure` (a conftest below the rootdir cannot declare `pytest_plugins`), and the props are declared through `pytest_reachy_mini_scene_props`, the photos converted to PNG once per run.
- **The robot's session fixture `sim_audio`** is the audio-device check above, failing (never skipping) on a daemon that does not listen on the cable.
- **The robot's devices on Linux.** The bridge and the sim's daemon find a named device through upstream's GStreamer listing, which on a PulseAudio host without PipeWire only accepts a virtual device through its `device.string`; the CI job sets one ([ci.md](ci.md)). The bridge in the test's process still logs that it finds no `wica_mic` card and falls back to the default source, which the job makes the cable's: the robot hears the person either way, and the daemon's report, which the session fixture checks, names the cable.
- **Building it found a crash in the app.** On Linux, MediaPipe's first import, once the robot's GStreamer media ran, killed the process inside matplotlib's FreeType extension (a segmentation fault, or an abort). Importing it on the main thread but after the body was entered crashed the same way; importing it before the body does not. `build_system` now imports it first ([gesture-input.md](gesture-input.md), "App integration"). The tier's process imports it at session start as well: the harness's own bridge sessions (its capability probe) run GStreamer media there before any app is built.
- **Each scenario is written once** (`scenarios.py`) and run by each embodiment's file on its own fixture; the emotion is the robot's alone.

### Skips and failures

- **By default, a missing prerequisite skips**, as in the rest of the live tier: a missing key through `require_env`, a missing cable device when its first open fails, a missing sim capability through the bridge's `requires` gate. A local run with only part of the setup then passes on what it can run.
- **`WICA_E2E_FAIL_ON_MISSING=1` turns each of those skips into a failure.** In CI a skip would turn the job green with nothing tested: a secret not passed, a null sink not created, a sim that came up without its camera. With the variable set, the run fails and names what is missing. It covers this tier's own prerequisites, the keys and the devices. CI sets it; a local run usually does not.
- **The sim's capabilities keep the bridge's own switch.** CI sets `REACHY_MINI_E2E_REQUIRED_CAPS=camera,props:hand` itself, so a sim that comes up without its camera or the hand props fails the robot's tests. The conftest does not derive it from `WICA_E2E_FAIL_ON_MISSING`: each switch belongs to the code that checks it, and the workflow shows both. The bridge's capabilities cannot hold our keys and devices, because it learns capabilities by probing a running sim, and the computer's tests never start one.
- **A failed run is re-run before it is believed.** Retries are not built in, so the report shows every failure, flaky ones included.

### Evidence on failure

A failure in this tier is otherwise hard to diagnose, so each scenario writes its evidence to pytest's `tmp_path`:

- the `TestRun` report (its timeline), as `report.json` and `report.md`;
- what the inputs delivered (`inputs.json`);
- the config the run was built from, with its values filled in, and, on the computer, the camera file as it last stood.

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

1. **The echo scenario on BlackHole.** It needs the app to speak into the cable it listens on, which is what stalls a process's capture of the cable on macOS ("The person's voice plays from a process of its own"): the app cannot be moved out of its own process. Possible ways: two cables (BlackHole 16ch's channels, or a second BlackHole) with the app's voice routed to the mic's cable by an aggregate device; or Linux only, where PulseAudio shows no such stall. To decide before the scenario is written.
2. **Recording the cable.** A recording of what the mic heard would be the best evidence for a failed spoken scenario. The cost is one more input stream on the cable during the run.
3. **The model and the persona's rules.** The full tier fixes one model (Claude Haiku 5.5) to test the assembled product, and some of what it asserts is that model's behaviour, not the product's wiring. Two cases so far: asked for an emotion, Haiku issued `say` and `play_emotion` at once in 2 runs of 3, against the persona's one-body rule (the emotion scenario records it rather than failing); and it met a dictated note with `noop`, on the robot (twice, on the Mac and in CI) and on the computer (5 runs of 5 on the Mac), which the framework's `no_noop_on_input` check fails, as it does any such answer, in every scenario. Whether such rules fail this tier, are recorded in its report, or are left to the later agent tier on the production model is to decide; until then both dictation tests are expected failures.
4. **Speaking over the computer, with echo cancellation on.** While the app speaks, the person's voice can be suppressed: a spoken "stop" did not reach the agent in three runs of three on the Mac, and in one run of two on Linux in CI. With the computer's echo cancellation off it is heard, and the robot, whose named devices run without software echo cancellation, hears it. On the cable there is no echo at all — the reference is the app's voice, the mic carries only the person — so whether barge-in fails in a room too, or only in this setup (the reference's delay, a canceller that has not converged two seconds into the story), is to find out. Until then the computer's spoken-stop test is an expected failure.
5. **Dictation on the computer.** In `timeout` mode the passage should end after a few seconds of silence, but on the Mac it closed right after the first of two sentences, about a second after the person started (2 runs of 3; the first run delivered both). It then reached WICA while the agent was still reacting to the completion of its own "go ahead", so WICA dropped it: the note never reached the agent. On Linux, in CI, the passage closed empty instead: the dictation's 10 s initial-silence timeout ran out while the scenario waited for the agent to settle. Why the passage closes early (asr-engine's segmentation, or Flux's own end of turn) is to find out. Decided on 2026-11-10: both templates, and so both e2e configs, end a passage on a trigger word, which a pause inside a note cannot cut short; `timeout` mode stays offered but is not exercised by this tier, and its early close is left to asr-engine. On the computer the passage then arrived whole, closed by the trigger word, in 4 runs of 5 (in the fifth Flux heard only "Morning." of the second sentence). Two findings for asr-engine: the final holding the trigger word closes the passage and its own text is dropped, so a sentence Flux joins to "Send." is lost (the scenario pauses 3 s before it; with 1 s the second sentence was lost in every run); and the substring match makes short words such as "go" fire inside others ("good", "ago").
6. **The harness's startup timeout.** It is fixed at 45 s, which a first macOS launch exceeds. This is raised with the bridge. Until it is fixed, the workaround is a daemon started by hand.
