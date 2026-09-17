---
code:
  - src/reachy_mini_interaction_wica/robot_control.py
tests:
  - tests/test_robot_control.py
---

# Robot control

**Status:** Implemented

## Purpose

The **actions** half of the app: the agent doesn't just talk, it *acts through the robot*. This module exposes a small set of WICA **Commands** wrapping [`reachy-mini-bridge`](../../reachy-mini-bridge)'s `ReachyMiniApi` verbs, so the model can express an emotion and manage the motors — the same first-class, observable, cancellable Command mechanism `say` uses for the voice (see [tts-output.md](tts-output.md)). `say` itself is *not* here — it is WICA's output Command; this module holds the **invokable action** commands the agent chooses among while reasoning.

The bridge's `ReachyMiniApi` is `Implemented` (its `tools` layer is not, and we don't need it — WICA has its own Command abstraction, so the app wraps the api verbs directly). All the robot state and orchestration live in the bridge; this module is a thin, **non-raising** adapter that turns each verb into an agent-friendly Command returning a readable string.

## Head tracking is not agent-facing

Earlier versions of this spec exposed `start_head_tracking` / `stop_head_tracking` as agent commands. Observed behavior showed the model treating idle "aliveness" as its own responsibility — proactively starting/stopping tracking (and reasoning about breathing/wobbling, which were never even exposed) as if the robot needed active management to look alive. It doesn't: the bridge's motion loop already keeps the robot continuously alive between verbs. As of the bridge's `motion` config consolidation, presence, breathing, wobbling, *and* tracking are all `MotionSettings` fields, default `true`, applied automatically — tracking is armed at session entry when the motors already read `enabled`, or on the next `set_motors_state("enabled")` otherwise, and is stopped again on exit (see [reachy-mini-bridge config.md](../../reachy-mini-bridge/specs/config.md) "`motion` block", [motion.md](../../reachy-mini-bridge/specs/motion.md)). So the fix here is not a prompt correction but a command removal: tracking is dropped from the agent-facing surface entirely, the same as presence/breathing/wobbling always were. The persona stays silent about all of it — see [prompts/assistant.md](../prompts/assistant.md).

## The commands (v1)

Chosen for a conversational, expressive presence — the robot talks, emotes, and can rest or engage its motors; it looks at you on its own, automatically, without the agent's involvement. **Camera/vision and manual movement/gaze are out of v1** (see [_overview.md](_overview.md)).

```python
class RobotControl:
    def __init__(
        self, api: ReachyMiniApi, *, emotions: Sequence[str] | None = None
    ) -> None: ...

    def commands(self) -> tuple[Command | Callable, ...]:
        """The Commands to register, in order: play_emotion (its description naming the valid
        emotions when `emotions` is known), get_motors_state, set_motors_state."""

    async def play_emotion(self, name: str) -> str:
        """Play a physical emotion on the robot — a short recorded motion, e.g. "happy" or
        "curious". Needs the motors enabled."""

    async def get_motors_state(self) -> str:
        """Report whether the motors are enabled, disabled, or in gravity-compensation (limp-but-held)."""

    async def set_motors_state(self, state: str) -> str:
        """Set the motors: "enabled" (ready to move), "disabled" (limp), or "gravity_compensation"
        (a gentle rest — the head holds position). Enable before playing an emotion."""
```

- **One shared `ReachyMiniApi`.** `RobotControl` holds the *same* api instance that backs `say` (see [tts-output.md](tts-output.md)) and the mic stream (see [asr-bridge.md](asr-bridge.md)), so there is a single robot connection and one media session. The app builds the api once and injects it into `VoiceOutput`, the `RobotMicSource`, and `RobotControl`.
- **Registered as Commands.** The app calls `wica.register_command(...)` for each item of `commands()`; a bound method's name/signature/docstring become what the model sees (the docstrings above are the descriptions), exactly as for `say` and the dictation commands. `play_emotion` is the one exception: when the emotion names are known, `commands()` wraps it as a `Command(play_emotion, description=…)` whose description **lists the valid names** — so the model reads them right where it picks the tool (see "The agent knows the emotion names from the start"). With `emotions=None` the docstring is used unchanged.
- **No `list_emotions` command.** Earlier versions exposed one; it is gone. The names are known at startup and given to the model up front, so a round-trip command would only add a tool to choose among (and a turn to spend) for information that never changes during a session — the bridge loads the emotions library once per connection.

- **Not independent of `say`.** `play_emotion` plays a sidecar sound and moves the head, so it collides with a `say` in flight; WICA runs a response's Commands concurrently, so the persona forbids issuing the two in one turn and sequences them across steps instead (see [app.md](app.md), "The persona's action rules"). This module adds no lock of its own in v1.

## Non-raising, like every Command

Each command maps the bridge's typed failures onto a readable string rather than raising, so the agent always gets feedback it can reason over:

- **Motor precondition.** `play_emotion` **moves the robot**, so the bridge raises `MotorsNotEnabledError` (a `BridgeError` subclass, see [reachy-mini-bridge errors](../../reachy-mini-bridge/specs/api.md)) when the motors aren't `"enabled"`. The command catches it and returns a message that tells the model what to do — e.g. `"Can't do that: the motors are off. Call set_motors_state('enabled') first."` — so the agent can enable and retry (it decides; the app does **not** silently auto-enable, matching the bridge's fail-fast stance).
- **Bad input.** An unknown emotion name or motor state raises `ValueError` in the bridge; the command returns `"<verb> failed: <message>"` — for emotions, followed by `Valid names: …` when the startup snapshot is known.
- **Everything else.** Any other `BridgeError` (or connection error) is caught and returned as `"<verb> failed: <message>"`. Success returns a short confirmation (`"OK"` or the state read back).

## The agent knows the emotion names from the start

The model must only ever call `play_emotion` with a name the library actually holds, and it should not have to spend a turn finding out which. So the names are **seeded at startup**, in two places that both live in the model's cached prefix (see [wica agent.md](../../wica-framework/specs/agent.md), "System prompt composition"):

1. **The system prompt.** After the robot is entered, the app awaits `api.list_emotions()` once on the loop and appends a short section — rendered by this module's `emotions_prompt_section(names)` — to the resolved persona *before* `Wica.init` sees it (the app hands WICA an inline-prompt variant of its config; see [app.md](app.md), build step 4). The persona file itself stays static and generic: it tells the model the names are listed further down, never the names.
2. **The `play_emotion` tool description** — via `commands()`, above.

The startup snapshot is not a compromise: the bridge's emotions library is a static dataset loaded once per connection, so nothing can change the list during a session. The cost is startup latency on a cold cache (on the real backend the library load is a HuggingFace dataset read) — but the same load happened on the first `play_emotion` before, so it merely moves earlier and warms the first move.

**When the names cannot be fetched** (the library load fails), the app degrades rather than crashing: no section in the prompt, `play_emotion` registered with its plain docstring (the model may still guess a name and get a readable failure), and a `Robot: emotions unavailable: …` banner line (see [app.md](app.md), "No credentials, no robot"). With no robot at all there are no names and no robot commands, so nothing is seeded.

## Testing

Drive `RobotControl` against the bridge's **`fake`** backend (or a fake `ReachyMiniApi`) — no daemon, no hardware — asserting behavior, not exact strings:

- `play_emotion(<known>)` succeeds and the fake records the move; `play_emotion(<unknown>)` returns a non-raising failure string, which names the valid names when `RobotControl` was built with them.
- `commands()` yields no `list_emotions`; `play_emotion`'s bound tool description names the given emotions, and falls back to the docstring without them.
- `emotions_prompt_section(names)` names every emotion and the `play_emotion` command.
- With motors disabled, `play_emotion` returns the motor-precondition message (and does **not** raise); after `set_motors_state("enabled")` it succeeds. (Note the sim daemon ignores motor-state changes — see [reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/api.md) open question 4 — so the *enforced-precondition* behavior is pinned on the `fake`, which honors it.)
- `get_motors_state()` reflects the state set via `set_motors_state(...)`.

A live test (the `sim` or a real robot actually moving) belongs in the opt-in `tests-e2e/` tier.

## Open questions (v1 resolutions)

All four are settled as deferrals — v1 ships the command set above; each of these is a post-v1 refinement, not a load-bearing unknown.

1. **Prompt-seeding vs. live `list_emotions`.** **Resolved: seed at startup, drop the command** (see "The agent knows the emotion names from the start"). The earlier deferral assumed WICA offered no seam to append startup-fetched text to the prompt; it does — `resolve_system_prompt` is public and prefers an inline `system_prompt`, and the frozen configs derive variants with `dataclasses.replace` — so the app seeds without touching the framework. The "static snapshot" cost turned out not to exist (the library is loaded once per connection).
2. **Compact-intent → emotion resolution.** Deferred. The bridge keeps `play_emotion(name)` literal; whether the app/agent layer adds friendlier intent mapping (e.g. "be happy" → a curated move, random fallback within a mood) — which the bridge explicitly leaves to the caller — stays out of v1.
3. **Should motor management be agent-facing at all, or app-lifecycle?** **Resolved for v1: agent-facing for motors, bridge-lifecycle for tracking.** `set_motors_state`/`get_motors_state` stay exposed, so the model can enable to move and rest the robot when idle. Head tracking flipped the other way (see "Head tracking is not agent-facing" above): it needs no agent decision, so it moved to the bridge's own lifecycle, alongside presence/breathing/wobbling — all four are now `motion` config fields, default `true` ([reachy-mini-bridge config.md](../../reachy-mini-bridge/specs/config.md)). Whether real hardware honors motor-state changes (the bridge's own open question) — and whether an app-lifecycle default enable for motors is friendlier — is deferred to hardware validation.
4. **Camera / "seeing".** Deferred post-v1. `get_camera_frame` exists in the bridge but is intentionally unwired here; a perception command (frame → the World, or a describe-what-you-see verb) is the obvious extension.
