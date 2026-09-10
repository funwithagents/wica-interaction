# Implementation plans

Implementation plans for Reachy Mini Interaction WICA — each plan turns a settled part of a spec (see [specs/_index.md](../specs/_index.md)) into concrete, buildable steps. Plans are ordered by their date-time filename prefix (`YYYYMMDDHHmm_`).

> **Note.** The four `Done` plans below built the app while it was still a plain vocal assistant (before it was retargeted onto the Reachy Mini robot). Their code exists (renamed into `reachy_mini_interaction_wica`), which is why they stay `Done` — but the specs they implemented are now `Updated` (see [specs/_index.md](../specs/_index.md)). The two `Todo` plans at the bottom close that gap: routing speech and control through `reachy-mini-bridge`, and adding the robot commands.

## Plans

<!-- One row per plan, chronological by filename prefix. Keep the Status column in sync with each plan's `**Status:**` line. -->

| Plan | Description | Status |
|---|---|---|
| [202609041752_config-loader.md](202609041752_config-loader.md) | The composed `AppConfig` + `load_config`: split one JSON into three sections, delegate each to its engine's own loader | Done |
| [202609041803_asr-bridge.md](202609041803_asr-bridge.md) | The `AsrBridge`: ASR segments → `on_final_segment` (finals) + live display (interims), plus the dictation commands | Done |
| [202609041809_tts-output.md](202609041809_tts-output.md) | The `VoiceOutput`: `say` output Command wrapping `TTSTools(engine).say` — the agent's non-raising voice | Done |
| [202609041824_app.md](202609041824_app.md) | The Gradio app + `main()` wiring: three engines behind WICA on one app-owned loop, Conversation/ASR + debug panels, typed-text hatch, no-credentials degrade | Done |
| [202609101700_bridge-integration.md](202609101700_bridge-integration.md) | Route speech and control through `reachy-mini-bridge`: one `ReachyMiniApi`, `say` via the robot speaker, ASR over the robot mic stream (`RobotMicSource`), a `reachy` config section, drop the local `TTSEngine` + audio-processing | Todo |
| [202609101715_robot-control.md](202609101715_robot-control.md) | The `RobotControl` commands: non-raising WICA wrappers over the bridge's `play_emotion` / `list_emotions` / head-tracking / motor-state verbs, registered with the agent | Todo |
| _(add plans here — see [_plan-template.md](_plan-template.md))_ | | |

## Status legend

- **Todo** — written, not yet started
- **In progress** — actively being implemented
- **Done** — implemented, verified (lint/type-check/tests pass), and merged
