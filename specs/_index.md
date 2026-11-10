# WICA Interaction

The design of WICA Interaction, one spec per concept: an interaction application on the **WICA** agentic framework in which a person **speaks** to the agent and it **speaks (and acts) back**, through whichever **embodiment** it is launched with — the computer it runs on, or a Reachy Mini robot. These specs are the normative design: every guarantee with its rationale and its open questions. To *use* the app, start at the [README](../README.md) and the [documentation by task](../docs/index.md) instead.

> **New here?** Read [_overview.md](_overview.md) first for the architecture — what the app is, the end-to-end data flow, the embodiments, and how the specs below fit together.

## Specs

<!-- One row per concept spec. Keep the Status column in sync with each spec's `**Status:**` line. -->

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, the `wica-interaction` package with one subpackage per embodiment, the base + extras dependency shape, the sibling GitHub dependencies, one entry point per embodiment | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split mirroring the package (embodiment subfolders), shared fakes, the lifecycle tested once over a fake embodiment, skip-without-credentials live tier | Implemented |
| [configuration.md](configuration.md) | The one composed config JSON: the shared `wica`/`asr`/`vision` sections (`AppConfig`, each delegated to its owner's loader) plus the sections each embodiment's own loader adds and validates | Implemented |
| [embodiment.md](embodiment.md) | The `Embodiment` protocol the lifecycle is written against — enter/exit, voice, mic, camera, commands, prompt addendum — member by member: who calls it, when, what each body returns | Implemented |
| [computer-embodiment.md](computer-embodiment.md) | The agent on the machine it runs on: `TTSEngine` voice through the speakers, the local mic (optionally through audio processing), a webcam for hand signs, no actions; the `wica-interaction-computer` entry point and persona | Implemented |
| [reachy-embodiment.md](reachy-embodiment.md) | The agent in a Reachy Mini: `say`/mic through one `ReachyMiniBridge`, its camera feed (`bridge.camera`) as the hand stage's upstream, the bridge's opt-in face detection + head tracking configured in the `reachy` section, the robot action commands (`RobotControl`), the emotion names seeded into the prompt, the robot persona's action rules; the `wica-interaction-reachy` entry point | Implemented |
| [audio-processing.md](audio-processing.md) | The computer embodiment's mic processing: one WebRTC coordinator doing echo cancellation (TTS far-end reference) and noise suppression, independently, opt-in and degrading gracefully | Implemented |
| [asr-bridge.md](asr-bridge.md) | Speech-in: the ASR engine over the embodiment's mic → World `speech_input` (final only), live partial view, capture health from the embodiment, and the agent's dictation command | Implemented |
| [gesture-input.md](gesture-input.md) | Vision-in: the embodiment's camera feed (a latest-value slot: the bridge's `camera` feed (`bridge.camera`), or a `StreamFeed` over a webcam) through `vision-modules`' hand + gesture pipeline → a hand-sign vocabulary (`stop`, `like`, `dislike`, `ok`, `one`…`five`) held for `hold_s` before it counts, as one one-shot `hand_gesture_<n>` entry per hand, triggering the agent whenever a sign is shown; the "Hand signs" persona section seeded at startup | Implemented |
| [app.md](app.md) | The Gradio app: the embodiment-agnostic build → UI → wire → start → close lifecycle (`system.py`), the transient inputs (a TTL and an omitted `None` on `speech_input` and the hand entries), the panels, the typed-text funnel, degraded start, one thin entry point per embodiment | Implemented |
| [ci.md](ci.md) | Continuous integration on GitHub's hosted runners: the static gate on Linux, the fast tier on Linux, macOS and Windows, and the end-to-end tier's two jobs (the computer, the robot in the headless sim) on Linux; the gate and the fast tier on every pull request and push to `main`, the end-to-end jobs on pushes to `main` and by hand; the same tier on Windows in a workflow started by hand | Updated |
| [e2e.md](e2e.md) | The end-to-end tier: every module real (ASR, the LLM, TTS, the hand models, the robot software and sim), only the devices replaced — a virtual audio cable for the person's voice, hand photos as the camera file or sim props; Claude Haiku, Deepgram Flux and phonon; a room that puts the app's voice back on the cable as echo; three focused scenarios and one conversation per body, asserted through WICA's `TestRun` and checks; replaces the live gesture test | Implemented |
| _(add concept specs here — see [_spec-template.md](_spec-template.md))_ | | |

Each spec also opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs — the spec → code/tests mapping the spec-drift checks use to scope what they compare. Keep it current when files move, and see [AGENTS.md](../AGENTS.md) ("Spec frontmatter") for the full convention. A spec that is not yet `Implemented` may name the files it *will* govern.

### Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being brainstormed/defined, contains open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), **ready to implement but not necessarily implemented yet**. This is the design-review gate, before code is written.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec (design and code in sync)
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches it; a new implementation plan is needed (or in progress) to catch up. Returns to **Implemented** once that plan is `Done`.
