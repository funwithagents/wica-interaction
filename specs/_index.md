# Reachy Mini Interaction WICA

An interaction application for the **Reachy Mini** robot, built on the **WICA** agentic framework. WICA handles multimodal interaction by serializing the multimodal context into text for an LLM to reason over, then connecting that reasoning to commands/tools for output. This project wires WICA to the robot so a person can talk to Reachy Mini and it talks (and acts) back: speech in via an **ASR** engine running on the robot's echo-cancelled microphone, agent reasoning via WICA, and speech out plus robot actions (emotions, head tracking, motors) through the **[`reachy-mini-bridge`](../../reachy-mini-bridge)** interaction API. The bridge (`ReachyMiniApi`) owns the robot; the app runs its own ASR over the bridge's mic stream. The underlying packages (`wica`, `reachy-mini-bridge`, and `asr-engine`; `tts-engine` arrives transitively through the bridge's `[tts]` extra) are local sibling packages consumed here as path dependencies.

> **New here?** Read [_overview.md](_overview.md) first for the global vision — what the app is, the end-to-end data flow, the two modes, and how the specs below fit together.

## Specs

<!-- One row per concept spec. Keep the Status column in sync with each spec's `**Status:**` line. -->

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, layout conventions, the sibling path dependencies | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split, functional-test philosophy, skip-without-credentials live tier | Implemented |
| [configuration.md](configuration.md) | The one composed app config JSON (`wica`/`asr` + `reachy` robot-backend sections, the voice living at `reachy.tts`), each section delegated to its own loader | Implemented |
| [asr-bridge.md](asr-bridge.md) | Speech-in: the app's ASR over the robot's echo-cancelled mic stream → World `speech_input` (final only), live partial view, and the agent's dictation command | Implemented |
| [tts-output.md](tts-output.md) | Speech-out: the `say` output Command — the agent's voice, spoken through the robot speaker via the bridge | Implemented |
| [robot-control.md](robot-control.md) | Robot commands: WICA commands wrapping `reachy-mini-bridge` verbs — play/list emotions, get/set motor state (head tracking is bridge-managed, not agent-facing) | Implemented |
| [app.md](app.md) | The Gradio app: data flow, WICA's contrib observability panels + the app's ASR panel, the build → UI → wire → start composition of ASR/robot behind WICA on one event loop | Implemented |
| _(add concept specs here — see [_spec-template.md](_spec-template.md))_ | | |

Each spec also opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs — the spec → code/tests mapping the spec-drift checks use to scope what they compare. Keep it current when files move, and see [AGENTS.md](../AGENTS.md) ("Spec frontmatter") for the full convention.

### Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being brainstormed/defined, contains open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), **ready to implement but not necessarily implemented yet**. This is the design-review gate, before code is written.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec (design and code in sync)
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches it; a new implementation plan is needed (or in progress) to catch up. Returns to **Implemented** once that plan is `Done`.
