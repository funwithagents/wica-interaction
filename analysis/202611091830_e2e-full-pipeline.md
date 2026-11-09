# End-to-end tests over the full pipeline — analysis (2026-11-09)

A working document, not a spec: it has no frontmatter and governs no code. It records what it takes to test `wica-interaction` **end to end with every module real** — ASR and vision as inputs, the WICA agent live, TTS as output, the robot's commands — on both embodiments, iterating on macOS first and then on Linux CI. It feeds a future `specs/e2e.md` and its plan.

## 1. The short version

- **Every module is real; only the physical devices are replaced.** The person's voice reaches the mic through a virtual audio cable, a hand reaches the camera as a photo (a prop in the robot's MuJoCo scene, a file for the computer's webcam), the robot is the MuJoCo sim. ASR, LLM, TTS and the hand models are the production providers.
- **Every building block now exists.** `reachy-mini-bridge` shipped what this needs (2026-11-09, commit `dcb55de`): image props of any kind in the sim scene, declared by a consumer; a capability per prop kind; a consumer's own bridge over the harness's daemon; the sim robot's audio devices chosen on its daemon and reported by it. `wica.testing.TestRun` asserts on the agent's timeline, and `AppSystem.wica` exposes the agent to it.
- **The hand-render spike passed.** Our real gesture pipeline reads all five hand photos rendered in the sim, correctly and with scores of 0.71–0.83, from 0.30 m to 0.60 m (§4).
- **Mac first, Linux CI second** is a setup difference, not a test difference (§9). The tests are the same.
- **The tier runs on every pull request and push to `main`, on Linux,** with phonon as the voices, Deepgram Flux as the ASR and Claude Haiku 5.5 as the LLM, plus a nightly run with the production providers (§10, §14). It is not a required check until it has proven stable.
- **It replaces `tests-e2e/test_gesture_input_live.py`**: the hand scenario covers the same photos through the real camera paths (§8).
- **This repo has no CI yet.** The static gate and the fast tier on Linux, macOS and Windows come first, independent of the e2e work (§14).
- **A second tier comes later: the agent tested through the framework's tester and scenario files**, with the peripherals bypassed (§13). The full tier is built first.

## 2. Where this tier sits

| Tier | What is real | What is replaced | Runs |
|---|---|---|---|
| Fast (`tests/`) | the app's own code | every provider, device and body (fakes, the `fake` LLM provider) | every change |
| Live gesture (`tests-e2e/test_gesture_input_live.py`, exists; replaced by the full tier's hand scenario) | the hand models | the camera (a scripted source) | on demand |
| Benchmark ([specs/benchmark.md](../specs/benchmark.md), Draft) | the app and the LLM under test | speech in and out, hand signs, the body (`BenchEmbodiment`) | on demand, to compare models |
| **Full e2e (this analysis)** | **everything: app, ASR, LLM, TTS, hand models, robot software, sim** | **the mic, the speaker, the camera's scene, the robot's hardware** | **every pull request and push to `main` (phonon TTS); nightly (production providers)** |
| Agent scenarios (later, §13) | the app and one fixed LLM | speech in and out, hand signs, the body (the benchmark's `BenchEmbodiment`) | on demand, nightly |
| Desk run | everything, including the hardware and the room's acoustics | nothing | manual |

The benchmark and the full tier answer different questions. The benchmark asks *which model* drives the interaction best, so it removes everything but the model. The full tier asks *does the assembled product work*: the cables between modules, the timings, the formats, the startup and teardown, the degrade paths. It keeps one model fixed.

## 3. What exists (verified 2026-11-09)

### The app

- `build_system(config, embodiment, asr_engine_factory=, gesture_models=)` builds the real system when the two seams are left at their defaults; `wire(system, transcript)` connects the voice; `_start` / `_serve` / `_shutdown` run and stop it ([specs/app.md](../specs/app.md)).
- `AppSystem.wica` is the running agent, so `wica.testing.TestRun(system.wica)` observes it: `expect_world(key, equals=)`, `expect_action(name, where=, state=, result_contains=)`, `expect_text`, `expect_no_action`, `expect_latency`.
- **Computer embodiment.** The mic is `asr.engine.audio.device`; with echo cancellation on, the coordinator opens that same device and plays to `tts.engine.player.device`. The camera is `camera.device`, an `int` or a `str`.
- **Reachy embodiment.** The `reachy` section is a verbatim bridge config. `enter()` starts the bridge; the voice is the bridge's own TTS; the mic is the bridge's `mic` feed; the camera is the bridge's `camera` feed.

### The bridge's test harness (`reachy_mini_bridge.testing`)

- **Props.** A consumer's `conftest.py` implements `pytest_reachy_mini_scene_props()` and returns `ImagePlane`s, usually from `image_pool(kind, images, width=…)`. The sim daemon the harness spawns carries them next to its own face pool. Images must be PNG; one dimension is given and the other follows from the image's aspect ratio.
- **Capabilities.** Each prop kind becomes `props:<kind>`, probed true when the daemon's scene holds a body of that kind; `@pytest.mark.requires(...)` gates on it. A consumer adds its own capabilities with `pytest_reachy_mini_capabilities()`.
- **Our own bridge.** The `live_daemon` fixture yields `(host, port)` and `robot_options(host, port)` gives the connection block, so our embodiment builds and starts its own bridge (with `daemon.spawn: "never"`).
- **Scene control.** `SimSceneClient(host, port)` — `spawn(pos, kind=)`, `place(name, pos, duration=)`, `despawn`, `clear`, `bodies`, `wait_still` — through the `sim_scene` fixture.
- **Audio devices.** `REACHY_MINI_E2E_AUDIO_INPUT` / `_OUTPUT` set `daemon.audio` on the sim daemon the harness spawns. The daemon reports them on `GET /api/bridge/daemon`, and every bridge on `local` media opens the devices that report names. A name matching no device fails loudly; it never falls back to the real mic. A named device skips upstream's software echo cancellation.

### The surrounding libraries

- **asr-engine** opens a device by name, and ships recorded speech fixtures (`sample_*` WAVs).
- **tts-engine** plays to a device by name (`player.device`), which is how a second engine can act as the person's voice.
- **vision-modules** ships the hand photos used below (`tests-e2e/fixtures/hand_*.jpg`, 1440×960).

## 4. The hand-render spike (2026-11-09)

The five hand photos, converted to PNG, were declared as 0.36 m wide props (0.24 m tall). Each was placed straight ahead of the robot at head height (z = 0.20 m), at five distances. The eye camera's frame was captured from the bridge's `camera` feed at each pose, then the app's own `GestureInput` classified it with the production models.

| Distance | stop | fist | palm | one | three2 |
|---|---|---|---|---|---|
| 0.30 m | stop 0.82 | stop 0.80 | five 0.73 | one 0.71 | three 0.80 |
| 0.45 m | stop 0.83 | stop 0.80 | five 0.71 | one 0.73 | three 0.80 |
| 0.60 m | stop 0.82 | stop 0.79 | five 0.75 | one 0.77 | three 0.79 |
| 0.80 m | — | — | five 0.75 | — | — |
| 1.00 m | — | — | five 0.76 | — | three 0.79 |

Each cell is the sign delivered and the top score; "—" means no hand was detected. The empty scene delivered nothing.

- **Up to 0.60 m every sign is right**, with scores well above the 0.55 threshold.
- **Beyond, the hand detector loses the hand.** No wrong label appeared at any distance; the failure mode is "no hand", not a wrong sign.
- **Tests place hands at 0.45 m** (the bridge's default prop position), the middle of the reliable band.
- **Renders are sharp and the colours are unshaded**, because the prop's material is emissive. Lighting is not a variable.

Two harness findings went to the bridge's maintainers:

- The first viewer launch on macOS took about 62 s (GStreamer building its plugin cache); the harness gives up after a fixed 45 s with no way to raise it. Later launches took about 8 s. Starting the daemon by hand and letting the harness borrow it works around it.
- The run summary labels a borrowed viewer daemon "sim headless".

## 5. Replacing the devices

### The Reachy Mini embodiment

| Device | Replacement | How |
|---|---|---|
| Robot | the MuJoCo sim | the harness's `live_daemon`, our own bridge over it |
| Robot's mic | a virtual cable the person's voice is played into | `REACHY_MINI_E2E_AUDIO_INPUT` sets it on the daemon; our bridge follows the daemon's report |
| Robot's speaker | the default output (hear the robot while iterating) or a virtual sink (silent, CI) | `REACHY_MINI_E2E_AUDIO_OUTPUT` |
| A hand in front of the robot | a hand photo prop, spawned at 0.45 m | `pytest_reachy_mini_scene_props` + `sim_scene.spawn(kind=…)` |
| A face in front of the robot | the bridge's own face pool | `sim_scene.spawn(kind="face")`, gated on `props:face` |

### The computer embodiment

| Device | Replacement | How |
|---|---|---|
| Mic | the virtual cable | `asr.engine.audio.device` |
| Speakers | the default output, or a virtual sink | `tts.engine.player.device` |
| Webcam | a file: a still photo or a short video | `camera.device` set to the file's path (see below) |

**A file as the webcam works with no code change, with one wrinkle.** OpenCV opens a file path like a camera. A still image yields one frame and then fails; a video plays through and then fails. `WebcamSource` records a failed read as a capture error and reopens the device at its poll rate, so the file loops, with a brief capture error at each end. Sign holds of 0.5 s survive this for a video, and probably for a still. The alternatives are a virtual camera (OBS on macOS, `v4l2loopback` on Linux), which is heavy, or a small injectable source in `ComputerEmbodiment`, which replaces real code. The file is the first thing to try.

### Echo and the virtual cable

- **The robot's mic and speaker must be different devices.** BlackHole loops its output back to its input, so a robot speaking into the cable it listens on would hear itself.
- **The computer embodiment's echo cancellation can be tested deliberately.** Send the app's speech and the person's voice into the same cable, and the far-end reference is exact. That is a scenario of its own (§7), not the default setup.

## 6. The person

The test needs someone to talk to the app. Two sources, both played into the cable's output:

- **Committed WAV files.** Deterministic audio, no provider. Fine for fixed phrases (asr-engine's `sample_*` fixtures, or our own recordings).
- **A second TTS engine.** Any text, generated per scenario. One tts-engine instance whose `player.device` is the cable. The text-to-speech output is itself non-deterministic, but the ASR on the other side copes with that the same way it copes with a person.

A `Person` helper owns this: `say(text)` speaks into the cable and returns when playback ends; `show(sign)` spawns the matching hand prop (robot) or swaps the camera file (computer); `hide()` removes it.

**Recommendation:** the second TTS engine as the default, with a cheap and fast provider; WAVs for the few scenarios that must not vary.

## 7. Scenarios

Each scenario starts the real system, lets the person act, and asserts on the agent's timeline through `TestRun`. Behaviour, never exact strings ([specs/testing.md](../specs/testing.md)). Every scenario is in English: phonon's public checkpoint is English-only, and the other languages are left to manual runs.

| # | Scenario | Person does | Expect |
|---|---|---|---|
| 1 | Startup | nothing | system built with nothing in `missing`; agent, ASR bridge, gesture input running; robot: bridge connected |
| 2 | Spoken turn | "Hello, what is your name?" | a final `speech_input` containing "name"; then `say` issued and completed within a budget |
| 3 | Hand sign | nothing first; then shows each photographed sign (`stop`, fist, palm, `one`, `three2`), hiding it between | no sign while nothing is shown; then `hand_gesture_1` = each sign, then `None`; the agent reacts (an action within a budget) |
| 4 | Emotion (robot) | "Can you look happy?" | `play_emotion` issued with `happy` and completed; `say` too, not overlapping it |
| 5 | Interrupt | asks for a long story, then says "stop" or shows `stop` mid-speech | the running `say` is cancelled within a budget |
| 6 | Dictation | the trigger phrase, a passage, the end phrase | dictation starts, the passage arrives as one input, dictation stops |
| 7 | Echo (computer) | speaks while the app speaks, on one cable | the app's own words never show up as `speech_input` |

Scenario 1 is cheap and catches most wiring breakage; scenarios 2 and 3 are the core of the tier.

**The framework's checks run after every scenario.** `wica.testing.checks` holds primer-level rules that apply to any finished timeline: every input is answered (`answers_each_input`), no input is met with a no-op (`no_noop_on_input`), no reaction fails (`no_failed_reactions`), no cancel collides with a running action (`no_colliding_cancel`). Running them over each scenario's `TestRun` timeline (`apply_checks`) adds broad assertions on top of the scenario's own expectations, at no extra cost.

**The scenarios are plain pytest code, not the framework's scenario files.** A scenario file's tester acts only through the fixed tester Commands, and its `set_<key>` Commands write the app's inputs straight into the World. That bypasses ASR and the hand models, which is what this tier exists to test, and the loader rejects any other Command name.

## 8. Shape of the tier

- **Location.** `tests-e2e/`, with `computer/` and `reachy/` subfolders.
- **It replaces `tests-e2e/test_gesture_input_live.py`.** The hand scenario puts the same vision-modules photos through the real camera paths: the webcam source reading a file on the computer, the sim's render on the robot. Two things carry over from the old test:
  - **The negative case.** The scenario first asserts that no sign is delivered while nothing is shown.
  - **A vision verdict that needs no LLM.** The scenario asserts the World's `hand_gesture_1` before the agent's reaction, as two separate expectations. A vision failure then reads as such, and an LLM failure does not hide it.
- **A per-embodiment e2e config**, committed: real providers with keys from the environment, device names from the environment (§9). Values the test controls (the daemon address, `daemon.spawn: "never"`) are filled in by the fixture.
- **Fixtures.**
  - `app_system(embodiment)`: builds the real system, wires a transcript collector, starts it, yields it with a `TestRun`, shuts it down in a `finally`.
  - `person`: the `Person` above.
  - **A device check at session start.** The daemon reports its audio devices, so a session fixture reads the report and fails the run when its input is not the cable the person speaks into. Without it, a borrowed daemon started without the cable would leave the robot on the real mic, and the test would time out with no clear cause.
  - For the robot: `pytest_reachy_mini_scene_props` declaring the hand pool (photos converted to PNG at session start with OpenCV, which MediaPipe already brings), and `@pytest.mark.requires("camera", "props:hand")`.
- **Skips and failures.** Missing keys, a missing cable device or a missing sim capability skip, like the rest of the tier. A run that sets a "required" variable (the bridge's `REACHY_MINI_E2E_REQUIRED_CAPS` pattern) fails instead, which is what CI sets.
- **Evidence on failure.** Each scenario keeps its transcript, its WICA timeline (`TestRun`'s report), the camera frames it acted on and, ideally, a recording of the cable. A failure in this tier is otherwise hard to diagnose.

## 9. macOS first, Linux CI second

| | macOS (iteration) | Linux (CI) |
|---|---|---|
| Sim camera | the viewer window: `REACHY_MINI_E2E_SIM_VIEWER=1`, an unlocked GUI session, `mjpython` | headless, rendered through EGL |
| Virtual mic | BlackHole 2ch | a PulseAudio null sink plus a remapped source over its monitor (upstream's device matcher skips monitor sources) |
| Robot speaker | the default output (audible) or a second virtual device | a second null sink |
| Permissions | microphone access for the terminal or editor | none |
| First sim launch | about 62 s (see §4) | to measure |
| Everything else | the same tests, models, providers and assertions | the same |

The bridge's own CI already runs its sim tier headless on Linux with EGL; our workflow mirrors that recipe and adds the null sinks and the provider secrets (§14).

## 10. Cost and cadence

- **Each scenario calls paid services**: ASR streaming, the LLM, the app's TTS and the person's TTS. A full run is a few dozen LLM reactions per embodiment, over a persona and primer of several thousand tokens each.
- **It is slow**: model downloads on first run (about 340 MB of hand models), sim start-up, real speech at real-time speed. An estimated 5 to 10 minutes per embodiment with warm caches, against about 4 for the bridge's live job.
- **It is non-deterministic**: a phrase misheard by the ASR, or a model that chooses another action, fails a run without a bug.

**Decided: every pull request and push to `main`, plus nightly.**

- **Phonon in the CI config**, for both the app's voice and the person's voice: tts-engine's lightest local model, with no PyTorch, no key and a public English checkpoint of about 160 MB that runs many times faster than real time on a CPU. The bridge's CI runs it for the same reason. Only the LLM and the ASR are paid on each push.
- **Deepgram Flux as the ASR** (`deepgram_v2`, `flux-general-en`), the production one. A local ASR was considered: asr-engine's `kyutai` module is on-device and keyless, but its backends pin `numpy<2.3` against vision-modules' `numpy>=2.5.3`, so adding them makes this project's lock unsolvable (re-checked 2026-11-09). It is also too heavy for a hosted runner's real-time budget, which is why asr-engine's own CI keeps it a local run.
- **Claude Haiku 5.5 as the LLM** (`provider: "anthropic"`, `model: "claude-haiku-5-5"`): fast and cheap, through a provider extra the dev group already installs. The cost per run is to be measured with the framework's token counts.
- **A nightly run with the production config** (the example configs' providers) catches provider-specific breakage the local voices cannot.
- **Not a required check at first.** A few percent of flaky runs would block merges. The job becomes required once its history shows it stable. Assertions stay behavioural, with generous budgets.
- **Runner minutes cost nothing**: the repository is public. The constraints are provider quotas and rate limits; the workflow's one-run-per-branch concurrency keeps parallel calls down.
- A failing scenario is re-run before it is believed. Retries are not built in.

## 11. Open questions

1. **Providers for the tier.** Decided (§10): phonon, Deepgram Flux and Claude Haiku 5.5 on each push; the production providers nightly.
2. **Budgets.** Latency budgets per scenario: assert them, or only record them.
3. **The person's voice provider**, and whether its text is varied per run.
4. **The computer's camera.** Does the looping file behave well enough through `WebcamSource`, or is an injectable source worth it?
5. **Scenario 7 (echo).** It needs one cable carrying both voices; confirm BlackHole and the coordinator handle it as expected.
6. **Two embodiments, one run.** The robot's tier needs the bridge's pytest plugin and a sim; the computer's needs neither. One directory with gates, or two separate invocations.

## 12. Next steps

1. **CI, first part**: `specs/ci.md` and its plan for the static gate and the fast tier on four platforms (§14, jobs 1 and 2). It does not depend on the e2e work, and its Windows entry validates the installs early.
2. **The e2e spec**: `specs/e2e.md` (Draft) from this analysis, and [specs/testing.md](../specs/testing.md) updated to name the new tier and drop the live gesture test.
3. **The e2e build, in plans**: the computer embodiment first (no sim, quickest loop on the Mac), scenarios 1–3, deleting `test_gesture_input_live.py` in the same change; then the robot, scenarios 1–4; then the rest.
4. **CI, second part**: the e2e job on Linux (§14, job 3) and the nightly production run, with `specs/ci.md` updated.
5. **The one-off Windows run** (§14).
6. Later, the agent scenario tier (§13), once the benchmark is built.

## 13. Later: the agent tested through the framework's tester

A second tier, built after the full one, tests the agent's behaviour rather than the assembled product. It is the benchmark's machinery used as a test suite.

- **The system under test** is the real app with the benchmark's `BenchEmbodiment` ([specs/benchmark.md](../specs/benchmark.md)): the real persona, Commands and dictation, with speech, hand signs and the body bypassed.
- **The person is the framework's scripted tester**, driven by scenario files (`wica.testing.scenario`): it writes `speech_input` and `hand_gesture_<n>` through its `set_<key>` Commands, waits, and asserts with its `expect_*` Commands. The framework's checks and this app's persona checks run over the finished timeline.
- **The difference from the benchmark is the verdict.** The benchmark runs a matrix of models and reports scores with no thresholds. This tier runs one fixed model, the production one, and turns each scenario into a pass or a fail.
- **It reuses the benchmark's scenario files**, so a behaviour pinned once is both measured across models and tested on the production model.
- **It is cheaper and faster than the full tier**: only LLM calls, no audio at real-time speed, no sim. It is still paid and non-deterministic, so it runs on demand and nightly too.

**A possible convergence, to decide then.** The full tier could run the same scenario files if its person layer performed the two input-writing Commands through the devices: spoken text into the cable, a sign as a hand prop. That needs a framework hook letting a target decide how `set_<key>` is carried out, and looser expectations on `speech_input`, since the ASR will not return the exact scripted text.

## 14. Continuous integration

This repository has no CI today. The bridge's (`reachy-mini-bridge/.github/workflows/ci.yml`, specified in `reachy-mini-bridge/specs/testing/ci.md`) is the model: GitHub's hosted runners, images pinned by name, `uv sync --locked`, one run per branch with newer pushes cancelling older ones, `fail-fast: false` matrices, required capabilities so a sim that comes up incomplete fails instead of skipping, and an expected-skips table that makes any other skip a regression.

### The jobs

| Job | Runners | When | What |
|---|---|---|---|
| 1. `check` | `ubuntu-24.04` | every pull request and push to `main` | `ruff check`, `ruff format --check`, `pyright` |
| 2. `fast-tier` | `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `windows-2025` | the same | `uv run pytest`, no key, no device |
| 3. `e2e` | `ubuntu-24.04`, one entry per embodiment | the same, with the CI config (phonon TTS) | the full tier against the headless sim and PulseAudio |
| 4. `e2e-nightly` | `ubuntu-24.04` | a schedule, and on manual trigger | job 3 with the production config |

- **The fast tier on four platforms is the cross-platform check.** It needs no device, so it runs the same everywhere, and each entry is where that platform's install and code paths meet a real machine. The Windows entry will show early whether every dependency has a Windows wheel: the TTS providers, the WebRTC echo-cancellation binding, MediaPipe. A lock that resolves for Windows does not prove it.
- **The e2e jobs run on Linux only.** The sim's camera needs EGL, which only Linux offers without a window; hosted macOS and Windows runners have no reliable GUI session. The computer embodiment alone could run on them, at the cost of installing a virtual audio driver and granting microphone access on a hosted Mac, for little gain: macOS is the development machine and runs the tier locally on every iteration.
- **The e2e job's environment** is the bridge's live job plus what the person needs: the system GStreamer, PulseAudio, Mesa's EGL, upstream's pinned WebRTC plugin action; two null sinks (the robot's or the app's speaker, and the cable) and a remapped source over the cable's monitor as the mic; the Hugging Face and MediaPipe model caches.
- **Secrets.** `WICA_ANTHROPIC_API_KEY` and `DEEPGRAM_API_KEY` are repository secrets for job 3; job 4 adds whatever the production configs name. GitHub does not pass them to pull requests from forks, so those runs skip the key-gated scenarios. Same-repository pull requests and pushes set the required variables, so a missing key or capability fails the job there.
- **Not required at first** (§10): jobs 1 and 2 are required checks on `main` from the start; job 3 becomes one once its history shows it stable; job 4 is never required.

### The one-off Windows run

The fast tier proves the install and the device-free code on Windows; it cannot prove real audio, the webcam or the viewer sim. One manual run on an **x86-64** Windows machine does. Windows on Arm, as in Parallels on an Apple silicon Mac, is a different platform where MediaPipe and onnxruntime may have no wheels, so a failure there says little about Windows itself.

The checklist:

1. `uv sync` and the fast tier pass.
2. The computer embodiment starts with nothing in `missing`; a spoken turn through the real mic and speakers gets a spoken reply; a hand sign shown to the webcam reaches the agent; echo cancellation keeps the app from hearing itself.
3. The Reachy embodiment starts against the viewer sim (`daemon.headless: false`); a spoken turn gets a reply through the sim's audio; the agent plays an emotion on request; a hand prop or a webcam relay shows a sign.
4. Startup and shutdown leave no daemon or audio stream behind.

The automated tier on Windows would also need VB-Audio Virtual Cable as the cable; it is not worth it for a one-off.
