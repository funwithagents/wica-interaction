---
code:
  - .github/workflows/ci.yml
tests:
---

# Continuous integration

**Status:** Updated

## Purpose

The verification gate of [AGENTS.md](../AGENTS.md) — lint, format, type check, tests — runs on GitHub's hosted runners for every pull request and every push to `main`, so it is a machine's verdict on each change and not only a local command. The end-to-end tier, which pays two live services per run, runs on pushes to `main` and by hand only. Like [testing.md](testing.md) this is a cross-cutting practice, not a runtime concept: nothing here ships in the package, and the one file that implements it is the workflow.

This spec covers the **static gate, the fast tier and the end-to-end tier's two jobs**, one per embodiment ([e2e.md](e2e.md)). A nightly run with the production providers joins it later.

The model is the sibling bridge's CI (`reachy-mini-bridge/specs/testing/ci.md`): the same runners, the same system packages for the robot SDK, the same shape of jobs.

## Decided

### The runners

- **GitHub-hosted runners, the free tier.** The repository is public, so every runner is free. No self-hosted runner.
- **Images pinned by name** — `ubuntu-24.04`, `macos-15`, `windows-2025` — never `*-latest`, so the system packages the jobs were validated against move only when a pin is bumped on purpose.
- **One Python**, 3.12: the floor of `requires-python`, the version `.python-version` names. uv installs it on the runner.
- **The fast tier on three platforms**: Linux x86_64, macOS on Apple silicon, Windows x86_64. The fast tier needs no device, no network and no key ([testing.md](testing.md)), so it runs the same on all three, and each is where that platform's install and code paths meet a real machine: the lock's environment markers, wheels, paths, clocks and the event loop.
- **Not Linux arm64.** MediaPipe ships no wheel for it, so the `vision` extra does not install there, and neither does the `dev` group that pulls it ([docs/reference/install.md](../docs/reference/install.md), "Platforms"). An arm64 entry joins when MediaPipe publishes a wheel, or when the dev group can be installed without hand signs.
- **Not Intel macOS.** torch, MediaPipe and onnxruntime ship no wheel for it, so the dev group does not install there either.
- **CI is the project's Linux and Windows target.** Development happens on macOS. A Linux-only or Windows-only failure is a bug to fix, not a reason to drop the platform.

### System packages

Installed before the sync on the Linux runner only; the workflow is the one place that lists them, the user-facing list being [docs/reference/install.md](../docs/reference/install.md) ("System packages"):

- **The robot SDK.** Upstream `reachy-mini`, pulled by the bridge, depends on PyGObject on Linux, which is sdist-only in the locked range and builds against the girepository and cairo headers with `pkg-config`; and on the system GStreamer: its introspection data and the base, good and bad plugin sets. The same set as the bridge's CI.
- **Audio.** `libportaudio2`, which `sounddevice` loads when asr-engine and tts-engine import it.
- **Echo cancellation.** A C++ toolchain, `build-essential` and `cmake`, since `aec-audio-processing` builds from source on Linux (on macOS too, with the toolchain the runner image ships: its 1.0.1 wheels are Windows-only).
- **Hand signs.** `libgles2` and `libegl1`, which MediaPipe loads.

macOS and Windows need none: GStreamer comes with the `reachy-mini` wheels, and the audio and echo-cancellation packages ship wheels.

### The workflow

One file, `.github/workflows/ci.yml`. It triggers on `pull_request`, on `push` to `main` and on `workflow_dispatch`. The two end-to-end jobs skip pull requests (`if: github.event_name != 'pull_request'`): each run calls the LLM and the ASR, so they run on `main` after a merge, and on a branch when started by hand from the Actions tab. Concurrency is one run per ref: a newer push cancels the older run still in flight.

| Job | Runners | What |
|---|---|---|
| `check` | `ubuntu-24.04` | `uv sync --locked`, then `ruff check .`, `ruff format --check .`, `pyright` |
| `fast-tier` | `ubuntu-24.04`, `macos-15`, `windows-2025` | `uv sync --locked`, then `pytest` |
| `e2e-computer` | `ubuntu-24.04` | `uv sync --locked`, the virtual cable, then `pytest tests-e2e/test_computer.py` |
| `e2e-reachy` | `ubuntu-24.04` | `uv sync --locked`, the virtual cable, upstream's webrtc plugin, then `pytest tests-e2e/test_reachy.py` against the headless sim |

- **The jobs run side by side**, each on its own runner, and none waits on another. Gating the fast tier behind the static gate would only save a run on a commit that fails lint, which costs nothing on a public repository.
- **`fail-fast: false`** on the fast tier's matrix, so one platform's failure leaves the others running and the run shows every platform's verdict.
- **`--locked`.** The sync fails when `uv.lock` does not match `pyproject.toml`, so a dependency edit lands with its relock or not at all.
- **The sync installs the `dev` group**, the default: the same environment as a contributor's `uv sync`, so pyright and the tests see every module.
- **The format check is the local one**: `ruff format --check .` over the same scope as the `ruff format .` of [AGENTS.md](../AGENTS.md) ("Commands").
- **The fast tier runs as locally**: `uv run pytest`, collecting `tests/` only (`testpaths`).
- **Real-clock margins are sized for the hosted Mac.** The `macos-15` runner is a virtual machine whose short sleeps run several times long (the bridge measured a 10 ms sleep taking 34). A fast-tier test that asserts something has *not yet* happened inside a window — a hold, a stall — keeps that window at 0.5 s or more, so the check lands inside it there too.
- **Paths are compared as POSIX.** A test that compares file paths with Markdown or frontmatter text builds them with `as_posix()`, so the Windows entry sees the same strings.
- **Caches.** The uv setup action caches uv's cache, which its CI prune reduces to the wheels uv built itself. The ones that cost minutes are the sdist-only packages — `aec-audio-processing` on Linux and macOS (about two minutes), PyGObject and pycairo on Linux — so the cache is **keyed on their locked entries**, not on the whole `uv.lock`: a sibling bump, which changes the lock and nothing they build from, keeps the cache, and pull requests reuse `main`'s. The workflow names the packages in one pattern; a new sdist-only dependency joins it, or it rebuilds on every run.
- **The end-to-end jobs on Linux only.** The tier's devices are virtual: a PulseAudio null sink is the cable the person plays into, a source remapped over its monitor is the app's mic, a second null sink is the app's speaker, and its monitor is the echo device the room records the app's voice from, to put it back on the cable ([e2e.md](e2e.md), "The room"). PortAudio, under `sounddevice`, lists ALSA devices only, so each gets an ALSA name (`wica_cable`, `wica_mic`, `wica_speaker`, `wica_echo`) through the ALSA pulse plugin (`libasound2-plugins`), written to `~/.asoundrc`; the jobs set the tier's `WICA_E2E_*_DEVICE` variables to them. The robot's job also makes each device findable by the bridge and the sim's daemon, and the mic and the speaker the defaults — the robot opens the default mic, unnamed, so that the daemon's software echo canceller is on: they find devices through upstream's GStreamer listing, which matches PulseAudio's description, skips monitor sources (hence the remapped source), and on a host without PipeWire takes a virtual device only through its `device.string` (no `udev.id` or profile, as hardware has); so the mic and the speaker carry a description and a `device.string` equal to their name, which is both their PulseAudio and their ALSA name. It is the bridge's own live job otherwise (`reachy-mini-bridge/.github/workflows/ci.yml`, `e2e-sim`): Mesa's EGL renders the sim's camera offscreen, `gstreamer1.0-pulseaudio` and the ICE library serve the daemon's media, upstream's pinned action installs the Rust webrtc plugin no Ubuntu archive ships, and `REACHY_MINI_E2E_REQUIRED_CAPS=audio,camera,props:hand` fails a sim that comes up without them. macOS is the development machine, with BlackHole, and Windows would need VB-Audio's cable, so neither runs the job ([e2e.md](e2e.md), "Platforms"). The Hugging Face cache (the hand models, the phonon voice) is kept across runs, keyed on `uv.lock` with a fallback to the last one. On a failure the scenarios' evidence is uploaded as an artifact.
- **Timeouts.** Each job carries a `timeout-minutes` well under GitHub's default, so a hung job fails in minutes.

### Secrets and protection

- **Two secrets**, `WICA_ANTHROPIC_API_KEY` and `DEEPGRAM_API_KEY`, for the end-to-end jobs: the tier's LLM and ASR. The other jobs call no provider. Since the jobs never run on a pull request, a fork's lack of secrets never reaches them: they set `WICA_E2E_FAIL_ON_MISSING=1` and require the sim's capabilities always, so a missing key, device or capability fails the job rather than turning it green with nothing tested.
- **The status checks to require on `main`** are `check` and the three `fast-tier` entries. `e2e-computer` and `e2e-reachy` are not required until their history shows them stable: it calls live services and is not deterministic. Requiring them is a repository setting on GitHub, outside the repository.

## Relationship to the other specs

- **[testing.md](testing.md):** the two tiers and what a good test is. CI is the hosted run of that strategy, with no rule of its own about what a test does.
- **[project.md](project.md):** the dependency groups CI installs, and the repository shape that holds `.github/workflows/`.

## Open questions

1. **A nightly run with the production providers**: the example configs' providers instead of the tier's.
2. **Linux arm64.** Waits on MediaPipe, or on a dev group that installs without hand signs.
