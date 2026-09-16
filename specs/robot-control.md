---
code:
  - src/reachy_mini_interaction_wica/robot_control.py
tests:
  - tests/test_robot_control.py
---

# Robot control

**Status:** Implemented

## Purpose

The **actions** half of the app: the agent doesn't just talk, it *acts through the robot*. This module exposes a small set of WICA **Commands** wrapping [`reachy-mini-bridge`](../../reachy-mini-bridge)'s `ReachyMiniApi` verbs, so the model can express an emotion, follow the person's face, and manage the motors — the same first-class, observable, cancellable Command mechanism `say` uses for the voice (see [tts-output.md](tts-output.md)). `say` itself is *not* here — it is WICA's output Command; this module holds the **invokable action** commands the agent chooses among while reasoning.

The bridge's `ReachyMiniApi` is `Implemented` (its `tools` layer is not, and we don't need it — WICA has its own Command abstraction, so the app wraps the api verbs directly). All the robot state and orchestration live in the bridge; this module is a thin, **non-raising** adapter that turns each verb into an agent-friendly Command returning a readable string.

## The commands (v1)

Chosen for a conversational, expressive presence — the robot talks, emotes, looks at you, and can rest or engage its motors. **Camera/vision and manual movement/gaze are out of v1** (see [_overview.md](_overview.md)).

```python
class RobotControl:
    def __init__(self, api: ReachyMiniApi) -> None: ...

    async def list_emotions(self) -> str:
        """List the emotions you can play (names for play_emotion)."""

    async def play_emotion(self, name: str) -> str:
        """Play a physical emotion on the robot — a short recorded motion, e.g. "happy" or
        "curious". Call list_emotions first if unsure of the names. Needs the motors enabled."""

    async def start_head_tracking(self) -> str:
        """Make the robot autonomously look at and follow the person's face. Needs the motors
        enabled."""

    async def stop_head_tracking(self) -> str:
        """Stop following the person's face (the head holds its current position)."""

    async def get_motors_state(self) -> str:
        """Report whether the motors are enabled, disabled, or in gravity-compensation (limp-but-held)."""

    async def set_motors_state(self, state: str) -> str:
        """Set the motors: "enabled" (ready to move), "disabled" (limp), or "gravity_compensation"
        (a gentle rest — the head holds position). Enable before playing an emotion or head-tracking."""
```

- **One shared `ReachyMiniApi`.** `RobotControl` holds the *same* api instance that backs `say` (see [tts-output.md](tts-output.md)) and the mic stream (see [asr-bridge.md](asr-bridge.md)), so there is a single robot connection and one media session. The app builds the api once and injects it into `VoiceOutput`, the `RobotMicSource`, and `RobotControl`.
- **Registered as Commands.** The app calls `wica.register_command(...)` for each bound method; the method name/signature/docstring become what the model sees (the docstrings above are the descriptions), exactly as for `say` and the dictation commands.

- **Not independent of `say`.** `play_emotion` plays a sidecar sound and moves the head, so it collides with a `say` in flight; WICA runs a response's Commands concurrently, so the persona forbids issuing the two in one turn and sequences them across steps instead (see [app.md](app.md), "The persona's action rules"). This module adds no lock of its own in v1.

## Non-raising, like every Command

Each command maps the bridge's typed failures onto a readable string rather than raising, so the agent always gets feedback it can reason over:

- **Motor precondition.** `play_emotion` and `start_head_tracking` **move the robot**, so the bridge raises `MotorsNotEnabledError` (a `BridgeError` subclass, see [reachy-mini-bridge errors](../../reachy-mini-bridge/specs/api.md)) when the motors aren't `"enabled"`. The command catches it and returns a message that tells the model what to do — e.g. `"Can't do that: the motors are off. Call set_motors_state('enabled') first."` — so the agent can enable and retry (it decides; the app does **not** silently auto-enable, matching the bridge's fail-fast stance).
- **Bad input.** An unknown emotion name or motor state raises `ValueError` in the bridge; the command returns `"<verb> failed: <message>"` (for emotions, ideally naming `list_emotions`).
- **Everything else.** Any other `BridgeError` (or connection error) is caught and returned as `"<verb> failed: <message>"`. Success returns a short confirmation (`"OK"`, the state read back, or the emotion list).

## Helping the agent pick valid names

`list_emotions()` gives the model the valid `play_emotion` names on demand. So the model doesn't have to guess or call it every turn, the app **may** call `api.list_emotions()` once at startup and fold the names into the system prompt (a persona/prompt concern, see [app.md](app.md) wiring and [prompts/assistant.md](../prompts/assistant.md)). Whether v1 seeds the prompt or relies on the command is an open question below.

## Testing

Drive `RobotControl` against the bridge's **`fake`** backend (or a fake `ReachyMiniApi`) — no daemon, no hardware — asserting behavior, not exact strings:

- `list_emotions()` returns the fake's emotion names; `play_emotion(<known>)` succeeds and the fake records the move; `play_emotion(<unknown>)` returns a non-raising failure string.
- With motors disabled, `play_emotion` / `start_head_tracking` return the motor-precondition message (and do **not** raise); after `set_motors_state("enabled")` they succeed. (Note the sim daemon ignores motor-state changes — see [reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/api.md) open question 4 — so the *enforced-precondition* behavior is pinned on the `fake`, which honors it.)
- `get_motors_state()` reflects the state set via `set_motors_state(...)`.

A live test (the `sim` or a real robot actually moving) belongs in the opt-in `tests-e2e/` tier.

## Open questions (v1 resolutions)

All four are settled as deferrals — v1 ships the command set above; each of these is a post-v1 refinement, not a load-bearing unknown.

1. **Prompt-seeding vs. live `list_emotions`.** **Resolved for v1: rely on the command.** The agent calls `list_emotions` on demand (always current), and [prompts/assistant.md](../prompts/assistant.md) already tells it to when unsure of a name. Prompt-seeding is deferred: the system prompt is a static file read inside `Wica.init`, with no append seam to fold startup-fetched names into — adding one is the future optimization (fewer tool calls, at the cost of a static snapshot).
2. **Compact-intent → emotion resolution.** Deferred. The bridge keeps `play_emotion(name)` literal; whether the app/agent layer adds friendlier intent mapping (e.g. "be happy" → a curated move, random fallback within a mood) — which the bridge explicitly leaves to the caller — stays out of v1.
3. **Should motor management be agent-facing at all, or app-lifecycle?** **Resolved for v1: agent-facing.** All six commands (including `set_motors_state`) are exposed, so the model can enable to move and rest the robot when idle. Whether real hardware honors state changes (the bridge's own open question) — and whether an app-lifecycle default enable is friendlier — is deferred to hardware validation.
4. **Camera / "seeing".** Deferred post-v1. `get_camera_frame` exists in the bridge but is intentionally unwired here; a perception command (frame → the World, or a describe-what-you-see verb) is the obvious extension.
