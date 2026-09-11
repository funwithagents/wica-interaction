# Implementation plans

Implementation plans for Reachy Mini Interaction WICA — each plan turns a settled part of a spec (see [specs/_index.md](../specs/_index.md)) into concrete, buildable steps. Plans are ordered by their date-time filename prefix (`YYYYMMDDHHmm_`).

> **Note.** The first four `Done` plans below built the app while it was still a plain vocal assistant (before it was retargeted onto the Reachy Mini robot). Their code exists (renamed into `reachy_mini_interaction_wica`), which is why they stay `Done`. The [bridge-integration plan](202609101700_bridge-integration.md) then routed speech through `reachy-mini-bridge` (one `ReachyMiniApi` behind `say` + the robot mic), flipping the configuration/tts-output/asr-bridge specs back to `Implemented`. The [robot-control plan](202609101715_robot-control.md) then added the robot *action* commands (`RobotControl`, registered with the agent), flipping [robot-control.md](../specs/robot-control.md) to `Implemented` and — since its command set was the last gap — [app.md](../specs/app.md) too.

## Plans

<!-- One row per plan, chronological by filename prefix. Keep the Status column in sync with each plan's `**Status:**` line. -->

| Plan | Description | Status |
|---|---|---|
| [202609041752_config-loader.md](202609041752_config-loader.md) | The composed `AppConfig` + `load_config`: split one JSON into three sections, delegate each to its engine's own loader | Done |
| [202609041803_asr-bridge.md](202609041803_asr-bridge.md) | The `AsrBridge`: ASR segments → `on_final_segment` (finals) + live display (interims), plus the dictation commands | Done |
| [202609041809_tts-output.md](202609041809_tts-output.md) | The `VoiceOutput`: `say` output Command wrapping `TTSTools(engine).say` — the agent's non-raising voice | Done |
| [202609041824_app.md](202609041824_app.md) | The Gradio app + `main()` wiring: three engines behind WICA on one app-owned loop, Conversation/ASR + debug panels, typed-text hatch, no-credentials degrade | Done |
| [202609101700_bridge-integration.md](202609101700_bridge-integration.md) | Route speech and control through `reachy-mini-bridge`: one `ReachyMiniApi`, `say` via the robot speaker, ASR over the robot mic stream (`RobotMicSource`), a `reachy` config section, drop the local `TTSEngine` + audio-processing | Done |
| [202609101715_robot-control.md](202609101715_robot-control.md) | The `RobotControl` commands: non-raising WICA wrappers over the bridge's `play_emotion` / `list_emotions` / head-tracking / motor-state verbs, registered with the agent | Done |
| [202609111030_app-degrade-and-teardown.md](202609111030_app-degrade-and-teardown.md) | App degrade paths + teardown: an unset TTS key degrades only the voice, honest robot-less wording (spec + banner), `asr_bridge.stop()` before `api.__aexit__` | Done |
| _(add plans here — see [_plan-template.md](_plan-template.md))_ | | |

## Status legend

- **Todo** — written, not yet started
- **In progress** — actively being implemented
- **Done** — implemented, verified (lint/type-check/tests pass), and merged
