# WICA Interaction

An interaction application built on the **WICA** agentic framework, which handles multimodal interaction by serializing the multimodal context into text for an LLM to reason over, then connecting that reasoning to commands/tools for output. A person **speaks** to the agent and it **speaks (and acts) back**, through whichever **embodiment** the app is launched with: the **computer** it runs on (a microphone, speakers, optionally a webcam — the former `vocal-interaction-wica` app) or a **Reachy Mini** robot (speaking, listening and seeing through the robot, playing emotions, managing its motors). Speech in is an **ASR** engine over the embodiment's mic; reasoning is WICA; hand signs come from the sibling [`vision-modules`](../../vision-modules) pipeline over the embodiment's camera; speech out and actions are the embodiment's. The underlying packages (`wica`, `asr-engine`, `tts-engine`, `reachy-mini-bridge`, `vision-modules`) are local sibling packages consumed as path dependencies, selected by extras.

> **New here?** Read [_overview.md](_overview.md) first for the global vision — what the app is, the end-to-end data flow, the embodiments, and how the specs below fit together.

## Specs

<!-- One row per concept spec. Keep the Status column in sync with each spec's `**Status:**` line. -->

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, the `wica-interaction` package with one subpackage per embodiment, the base + extras dependency shape, the sibling path dependencies, one entry point per embodiment | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split mirroring the package (embodiment subfolders), shared fakes, the lifecycle tested once over a fake embodiment, skip-without-credentials live tier | Implemented |
| [configuration.md](configuration.md) | The one composed config JSON: the shared `wica`/`asr`/`vision` sections (`AppConfig`, each delegated to its owner's loader) plus the sections each embodiment's own loader adds and validates | Implemented |
| [embodiment.md](embodiment.md) | The `Embodiment` protocol the lifecycle is written against — enter/exit, voice, mic, camera, commands, prompt addendum — member by member: who calls it, when, what each body returns | Implemented |
| [computer-embodiment.md](computer-embodiment.md) | The agent on the machine it runs on: `TTSEngine` voice through the speakers, the local mic (optionally through audio processing), a webcam for hand signs, no actions; the `wica-interaction-computer` entry point and persona | Implemented |
| [reachy-embodiment.md](reachy-embodiment.md) | The agent in a Reachy Mini: `say`/mic/camera through one `ReachyMiniApi`, the robot action commands (`RobotControl`), the emotion names seeded into the prompt, the robot persona's action rules; the `wica-interaction-reachy` entry point | Implemented |
| [audio-processing.md](audio-processing.md) | The computer embodiment's mic processing: one WebRTC coordinator doing echo cancellation (TTS far-end reference) and noise suppression, independently, opt-in and degrading gracefully | Implemented |
| [asr-bridge.md](asr-bridge.md) | Speech-in: the ASR engine over the embodiment's mic → World `speech_input` (final only), live partial view, capture health from the embodiment, and the agent's dictation command | Implemented |
| [gesture-input.md](gesture-input.md) | Vision-in: the embodiment's camera through `vision-modules`' hand + gesture pipeline → a hand-sign vocabulary (`stop`, `like`, `dislike`, `ok`, `mute`, `one`…`five`) as one `hand_gesture_<n>` entry per hand, triggering the agent whenever a sign is shown; the "Hand signs" persona section seeded at startup | Implemented |
| [app.md](app.md) | The Gradio app: the embodiment-agnostic build → UI → wire → start → close lifecycle (`system.py`), the panels, the typed-text funnel, degraded start, one thin entry point per embodiment | Implemented |
| _(add concept specs here — see [_spec-template.md](_spec-template.md))_ | | |

Each spec also opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs — the spec → code/tests mapping the spec-drift checks use to scope what they compare. Keep it current when files move, and see [AGENTS.md](../AGENTS.md) ("Spec frontmatter") for the full convention. A spec that is not yet `Implemented` may name the files it *will* govern.

### Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being brainstormed/defined, contains open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), **ready to implement but not necessarily implemented yet**. This is the design-review gate, before code is written.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec (design and code in sync)
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches it; a new implementation plan is needed (or in progress) to catch up. Returns to **Implemented** once that plan is `Done`.
