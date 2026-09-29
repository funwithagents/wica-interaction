---
code:
  - src/wica_interaction/reachy/embodiment.py
  - src/wica_interaction/reachy/robot_control.py
  - src/wica_interaction/reachy/config.py
  - src/wica_interaction/reachy/app.py
  - prompts/reachy.md
tests:
  - tests/reachy/test_embodiment.py
  - tests/reachy/test_robot_control.py
  - tests/reachy/test_config.py
---

# Reachy Mini embodiment

**Status:** Implemented

## Purpose

The agent embodied in a **[Reachy Mini](https://github.com/pollen-robotics/reachy_mini)**: it speaks out of the robot's speaker, listens through the robot's echo-cancelled microphone, sees through the robot's camera, and acts — plays emotions, manages its motors — through [`reachy-mini-bridge`](../../reachy-mini-bridge)'s `ReachyMiniApi`. One implementation of the [Embodiment](embodiment.md) protocol, gathering what used to be spread over `tts_output.py`, `robot_control.py`, the `RobotMicSource` in `asr_bridge.py` and the `RobotCameraSource` in `gesture_input.py`. Its persona is the robot, [prompts/reachy.md](../prompts/reachy.md). It runs as `wica-interaction-reachy --config <file>`.

## Why the robot owns audio

Audio on Reachy Mini is not a pair of laptop `sounddevice` streams: it is a daemon-owned pipeline with an XMOS **XVF3800** voice processor doing echo cancellation, noise suppression, AGC and beamforming in firmware. Echo cancellation works only if playback goes **through** that pipeline, so speech must be played through the robot, and the mic must be read **from** it (the post-processed stream). That is why this embodiment plays and listens through the bridge and needs none of the computer embodiment's [audio processing](audio-processing.md) — the hardware (and the simulator) deliver a clean signal. See [reachy-mini-bridge audio.md](../../reachy-mini-bridge/specs/audio/audio.md).

## Configuration

The file carries the shared sections (`wica`, `asr`, optional `vision` — [configuration.md](configuration.md)) plus one of this embodiment's own, parsed by `reachy/config.py`'s `load_config(path) -> ReachyConfig`:

```json
{
  "wica": { "agent": { "…": "…", "system_prompt_file": "prompts/reachy.md" } },
  "asr": { "engine": { "…": "…" } },
  "reachy": {
    "backend": "sim",
    "robot": {},
    "daemon": { "spawn": "auto", "headless": false, "camera": { "source": "webcam", "device": null } },
    "tts": { "module": { "type": "pocket", "voice": "george", "device": "auto" } },
    "audio": { "xvf3800": null },
    "faces": { "detector": "yunet", "detection": true },
    "motion": { "presence": true, "idle": "breathing", "wobbling": true, "tracking": true }
  },
  "vision": { "enabled": true, "threshold": 0.55, "fps": 5, "max_hands": 2, "hold_s": 0.5, "device": null }
}
```

```python
@dataclass
class ReachyConfig:
    app: AppConfig  # the shared sections
    reachy: ReachyMiniConfig
```

**The `reachy` section is a verbatim bridge config**, handed whole to `ReachyMiniConfig.from_dict` — every field, default and validation rule is the bridge's own ([reachy-mini-bridge config.md](../../reachy-mini-bridge/specs/core/config.md)):

- `backend` — `"real"` (default), `"sim"` (the upstream MuJoCo mockup), or `"fake"` (the bridge's in-package stand-in: no daemon, no hardware — the offline/test path).
- `robot` — upstream `ReachyMini(...)` kwargs, forwarded verbatim.
- `daemon` — how the bridge brings up the daemon it talks to (`spawn`, `headless`, `scene`, `camera`, `preload_datasets`, `startup_timeout`). Only `fake` has no daemon, so `spawn` other than `"never"` is rejected there.
- `tts` — **the voice**: a tts-engine `engine` block whose `module.type` names the provider; the bridge builds the synthesizer itself from it. The provider's extra must be installed — the same `tts-elevenlabs` / `tts-gradium` / `tts-pocket` extras the computer embodiment uses ([project.md](project.md), "Extras"). The committed default is `pocket` (the local model, voice `george`): no key, no network at synthesis time, weights downloaded on first run. Absent ⇒ no voice (`say` unavailable, everything else unaffected).
- `audio.xvf3800` — the tuned XVF3800 profile; `null`/absent keeps the firmware defaults.
- `faces` — the bridge's **opt-in face detection** ([reachy-mini-bridge user_perception.md](../../reachy-mini-bridge/specs/vision/user_perception.md)): `detector` names what finds the faces in the camera feed's frames — `null` (the default: nothing is detected, no model is loaded), `"yunet"` (the bridge's shipped detector, upstream's own model; nothing to install, its weights a Hugging Face download on first use) or `"custom"` (a detector registered from code; not used here) — and `detection` (default `false`) whether the detection loop runs from session entry for the caller's sake (`api.faces`, the observable face report). Detection needs no motors.
- `motion` — presence, the idle mode (`"breathing"` by default, else `"hold"` / `"custom"`), wobbling and tracking: the bridge's own aliveness, none of it agent-facing (below). `presence` and `wobbling` default `true`; **`tracking` defaults `false` and needs a detector** — the bridge's own head tracker is a client of the detection loop, so `tracking: true` with `faces.detector` `null` is a bridge `ConfigError` (surfacing as `ValueError` from `load_config`, the file named). The committed template turns tracking on with `yunet`, so the robot follows the person in front of it; a deployment that wants a still-headed robot sets `tracking` to `false` and can drop `faces` altogether.

Absent `reachy` section ⇒ `ReachyMiniConfig()` (the `real` backend, upstream defaults, no voice). Its validation errors are the bridge's `ConfigError`, a `ValueError` subclass, so `load_config`'s one error type holds. The section is the one place that imports `reachy_mini` at load (lazily, to validate `robot` keys against upstream's signature). The ASR config's `audio.device` is irrelevant here (no local device is opened); the ASR module must be configured for **16 kHz mono linear16** to match `api.mic_sample_rate`. Any top-level key outside `wica`/`asr`/`vision`/`reachy` — a `tts`, `audio_processing` or `camera` section — is rejected.

## `ReachyEmbodiment`

```python
class ReachyEmbodiment:
    display_name = "Reachy Mini"

    def __init__(
        self,
        config: ReachyConfig,
        *,
        api: ReachyMiniApi | None = None,  # test seam
    ) -> None: ...
```

Construction does nothing but keep the config (and the injected fake, if any). `enter()`, awaited on the app loop:

1. **The one `ReachyMiniApi`.** `ReachyMiniApi(config.reachy)` (the bridge builds the voice from its `tts` block at construction, degrading to no voice rather than raising), then `await api.__aenter__()` — the daemon (when configured), the robot connection and the media session come up, and every async verb binds to this loop. Construction never connects; only `__aenter__` does, so **any** exception here (an upstream `reachy_mini` error, the bridge's `DaemonError`/`BridgeError`) propagates out of `enter()` — that is the "no embodiment" degrade in [embodiment.md](embodiment.md): the banner reads `Reachy Mini: …`, the agent runs on typed text with nothing to speak or move through. **The offline hint rides that message**: on a backend that needs something outside the process (`real`, `sim`), the failure is re-raised as a `RuntimeError` whose text is the original message plus how to develop with no robot (`reachy.backend` `"fake"` with no `daemon` block, or `"sim"`) — the banner shows it verbatim, and only this embodiment knows its backends, so the shared page carries no robot wording. On `fake` the error propagates untouched. The enter is awaited without an app-side timeout, so a `sim` daemon that never becomes ready fails only after the bridge's `daemon.startup_timeout` (45 s by default). This single instance backs `say`, the mic stream, the camera and the action commands: one robot connection, one media session.
2. **The voice's health.** After a successful enter (and only when no `api` was injected — a fake carries its own `say`): no `reachy.tts` block ⇒ `missing` gets `TTS: no reachy.tts block configured (say is unavailable)`; else `api.synthesizer_error` set ⇒ `TTS: <error>`. The robot is up either way; `voice` is still the `say` below, which then returns `"TTS error: …"` when called (the bridge's `say` raises `BridgeError` without a synthesizer).
3. **The emotion names.** `await api.list_emotions()` once — a snapshot the session keeps (the bridge loads its library once per connection, so nothing changes mid-session). A failure (on the real backend, the library is a HuggingFace dataset read) degrades to `missing` `Robot: emotions unavailable: …` and no names: the prompt then carries no emotion section and `play_emotion` keeps its plain docstring.
4. **The mic.** `RobotMicSource(api)`, below. Nothing to do for the camera: `api.camera` is the session's own feed, started with the media session and alive until exit.

The members:

- **`voice`** — the bound `say` below, always (the robot came up).
- **`mic`** — the `RobotMicSource`; **`mic_error`** — its `capture_error`.
- **`camera(fps=…)`** — `RobotCameraFeed(api)`, below; `fps` is ignored (the feed runs at the daemon's rate, the gesture stages pace themselves).
- **`commands()`** — `RobotControl(api, emotions=names).commands()`, below.
- **`prompt_addendum`** — `emotions_prompt_section(names)` when names were fetched, else `""`.
- **`exit()`** — `await api.__aexit__(None, None, None)`: closes the media session, the camera feed (its `latest()` reads `None` from then on) and the robot connection (and the managed daemon). The lifecycle runs it after ASR and vision are stopped, so the mic pump is cancelled and the hand stage joined while the media session and the feed are still live ([app.md](app.md), "Wiring and lifecycle").

### `say`

```python
async def say(self, text: str) -> str:
    """Speak `text` aloud to the person, out of the robot's speaker.

    This is your voice: whatever you want the person to hear, say it here.
    Returns "OK" when the utterance finished playing.
    """
```

A thin, non-raising wrapper over `api.say`: an empty/whitespace string returns `"TTS error: text must not be empty"` without touching the robot; any exception from `api.say` (no synthesizer, a synthesis or media failure) maps to `"TTS error: <message>"`; success returns `"OK"`. It awaits `api.say`, which completes when the utterance has finished playing on the robot, so the Command stays `running` for the real duration of the speech. Cancelling the Command's task is how the bridge stops speech in v1 ([reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/core/api.md)).

### `RobotMicSource`

The [`AudioSource`](../../asr-engine/specs/audio-source.md) the ASR engine drains, over the bridge's mic tap:

```python
class RobotMicSource:
    def __init__(self, api: ReachyMiniApi, *, maxsize: int = 200) -> None: ...
    def start(
        self,
    ) -> asyncio.Queue[
        bytes
    ]: ...  # cancel any live pump, spawn a fresh one, return its queue
    def stop(self) -> None: ...  # cancel the pump task
    @property
    def capture_error(self) -> str | None: ...
```

- `start()`/`stop()` are **synchronous** — the protocol the engine drives its source through: it calls `start()` (not awaited) from inside its start coroutine to obtain the queue and `stop()` on teardown. `start()` grabs the running loop and `create_task`s the pump (the `async for chunk in api.audio_input()` loop lives there), returning the queue immediately. It must be **restartable**: each `start()` resets its queue, task and `capture_error`, and first cancels any pump still live from a `start()` with no intervening `stop()`, so no pump is orphaned.
- `api.audio_input()` yields **int16 LE mono PCM at 16 kHz** — the streaming-ASR contract — so the pump is "iterate and enqueue", no resampling. The audio is already echo-cancelled by the XVF3800.
- **Bounded, drop-oldest queue.** At most `maxsize` chunks (200 ≈ 2 s of the robot's 10 ms blocks); when the engine falls behind, the oldest chunk is dropped — stale speech is useless, memory stays bounded.
- **A dead stream is visible, not silent.** If `audio_input()` raises, or ends on its own (the media session closed under it), the pump logs it and records `capture_error` (`"mic stream failed: …"` / `"mic stream ended"`); the engine would otherwise keep reporting `running` while hearing nothing. A deliberate `stop()` records nothing.

### `RobotCameraFeed`

The `CameraFeed` ([gesture-input.md](gesture-input.md), "Frames come from the embodiment") over the bridge's camera feed, `api.camera` ([reachy-mini-bridge camera.md](../../reachy-mini-bridge/specs/vision/camera.md)) — the **one reader** of the robot's camera: a thread the session owns, looping upstream's one-shot `get_frame()` and publishing the newest `CameraFrame` (`frame_id`, `ts`, the BGR `HxWx3` `uint8` `image`, `head_pose`) for any number of consumers — the bridge's face-detection loop, this app's hand stage — to sample without stealing frames from one another. `CameraFrame` is a `FrameLike` by shape, so the hand stage consumes it as it stands:

```python
class RobotCameraFeed:
    def __init__(self, api: ReachyMiniApi) -> None: ...
    def open(self) -> None: ...  # nothing: the feed is the session's, started at enter
    def close(self) -> None: ...  # nothing: never stops the bridge's feed
    def latest(self) -> CameraFrame | None: ...  # api.camera.latest()
    @property
    def published_count(self) -> int: ...  # api.camera.published_count
    @property
    def capture_error(self) -> str | None: ...  # always None (below)
```

- **A thin adapter, deliberately.** `latest()` and `published_count` forward to `api.camera`; nothing is copied, paced or re-numbered — the frame the hand stage reads is the very object the bridge's detector read, at the daemon's rate (10 fps on a local daemon; the stages sample at `vision.fps`). Reading the feed is an instant, thread-safe call from the hand stage's own thread: no hop to the app loop, no timeout, nothing async — the loop the api was entered on is not needed here.
- **`open()` / `close()` are no-ops.** The feed's lifecycle is the session's: `__aenter__` starts it after the media session, `__aexit__` stops it and resets `latest()` to `None`. The adapter never calls `api.camera.start()` / `stop()` — stopping it would blind the bridge's own face detection and head tracking, which sample the same feed. (The adapter exists for exactly this reason: the bridge's feed has `start`/`stop`, and the gesture module's protocol has `open`/`close`, so it cannot be handed over by accident.)
- **`capture_error` is always `None`.** The feed has no failure of its own to report — a read that keeps failing is logged by the bridge and the last frame stays published; "no frame" is simply `latest()` not advancing. The gesture module's stall detection covers that (`no camera frame for 3 s` on the Vision line — the headless sim's absent camera, a daemon with no camera, the macOS camera draw the bridge's launcher works around), so a dead camera is visible without the adapter inventing an error.
- No mirroring: the robot's view is not a selfie. The frame is shared and read-only by the feed's rule; the hand stage only reads it (the crop it publishes is a copy, the library's rule too).

## `RobotControl` — the action commands

The agent does not just talk, it *acts through the robot*: a small set of WICA **Commands** wrapping `ReachyMiniApi` verbs, the same first-class, observable, cancellable mechanism `say` uses. The bridge holds all robot state and orchestration; this is a thin, **non-raising** adapter.

```python
def emotions_prompt_section(
    names: Sequence[str],
) -> str: ...  # "## Your emotions …", appended to the persona


class RobotControl:
    def __init__(
        self, api: ReachyMiniApi, *, emotions: Sequence[str] | None = None
    ) -> None: ...

    def commands(self) -> tuple[Command | Callable[..., Any], ...]:
        """play_emotion (a Command whose description names the valid emotions, when known),
        get_motors_state, set_motors_state."""

    async def play_emotion(self, name: str) -> str:
        """Play a physical emotion on the robot — a short recorded motion, e.g. "happy" or
        "curious". The motors are on by default: just play it — only if the reply says they are
        off, enable them and play it again."""

    async def get_motors_state(self) -> str:
        """Report whether the motors are enabled, disabled, or in gravity-compensation
        (limp-but-held). Assume they are enabled; only read them back when a move has just
        reported them off or moved in an unexpected way."""

    async def set_motors_state(self, state: str) -> str:
        """Set the motors: "enabled" (ready to move), "disabled" (limp), or "gravity_compensation"
        (a gentle rest — the head holds position). They are enabled by default, so call this only
        to recover after a move reported them off, or to rest them."""
```

- **Registered as Commands** by `wire`, through `commands()`: a bound method's name/signature/docstring is what the model sees, except `play_emotion`, which — when the names are known — is wrapped as `Command(play_emotion, description=…)` listing the valid names right where the model picks the tool. With no names the docstring is used unchanged.
- **Non-raising, like every Command.** `play_emotion` **moves the robot**, so the bridge raises `MotorsNotEnabledError` when the motors are not `"enabled"`: caught and returned as `"Can't do that: the motors are off. Call set_motors_state('enabled') first."` — the agent decides to enable and retry; the app never silently auto-enables (the bridge's fail-fast stance). An unknown emotion name or motor state (`ValueError`), or any other `BridgeError`/connection error, returns `"<verb> failed: <message>"` — for emotions followed by `Valid names: …` when known. Success returns a short confirmation or the state read back.
- **The motors are assumed enabled; their state is checked only on failure.** Enabled is the normal state of a running robot, and a state check before every move costs a turn (and, under the no-collision rule below, a whole extra step) to learn what is almost always already true. So every description — the two motor docstrings, `play_emotion`'s, and the persona's "How you move" — tells the model to just move, and to reach for `set_motors_state("enabled")` **only** after a move has come back with the motor-precondition message, then retry. `get_motors_state` stays registered, as the read-back for that recovery (and for an unexpected move), not as a pre-flight check. This is prompt/description guidance, not a guard: the bridge's precondition is unchanged and still the thing that reports the truth.
- **No `list_emotions` command.** The names are known at startup and given to the model up front (the prompt section through `prompt_addendum`, the tool description through `commands()`), both in the model's cached prefix; a round-trip command would spend a turn on information that never changes during a session.
- **Head tracking, presence, breathing, wobbling are not agent-facing.** Observed behaviour showed the model treating idle aliveness as its responsibility when tracking was exposed; it is not — the bridge's motion loop keeps the robot alive between verbs, driven by the `motion` config, and the persona stays silent about all of it.
- **Not independent of `say`.** `play_emotion` plays a sidecar sound and moves the head, so it collides with a `say` in flight; WICA runs a response's Commands concurrently, so the persona's action rules (below) sequence them across steps. No lock in v1 (open question 1).

## Entry point

`wica_interaction.reachy.app:main` — `wica-interaction-reachy` in `[project.scripts]`:

```python
def _load(path: str) -> tuple[AppConfig, Embodiment]:
    config = load_config(path)
    return config.app, ReachyEmbodiment(config)


def main() -> None:
    run_app(description="Talk to a WICA-driven Reachy Mini.", load=_load)
```

`run_app` ([app.md](app.md)) requires `--config`: the committed `config.reachy.example.json` is a template (a daemon the bridge spawns itself, `pocket` voice, the shipped `yunet` face detector with detection and head tracking on, vision on) to copy and point at what you drive — `"real"` for the physical robot, `"sim"` for the MuJoCo simulator, `"fake"` (with no `daemon` block) for offline development.

## Persona

[prompts/reachy.md](../prompts/reachy.md): the voice and mind of a Reachy Mini in a spoken, physical interaction. Beyond the shared voice/dictation rules (how a dictated passage ends comes from `start_dictation`'s reply, not from the file — [asr-bridge.md](asr-bridge.md); an empty passage means nothing came through), it tells the model how it moves — `play_emotion` with the names listed "further down" (the seeded section), the motors assumed on with `set_motors_state("enabled")` kept for recovering from a move that reported them off, resting in `gravity_compensation` — and never to narrate movements; it says nothing about tracking/presence/breathing. It carries the three **action rules** WICA's generic primer cannot know, because these Commands share one body and one speaker:

1. **No colliding Commands in one turn.** `say` with `play_emotion` (the emotion's sidecar sound plays over the speech and its motion fights the talking head), two emotions, a move with a motor-state change, a `cancel_command` with the action replacing what it cancels: issue the first, then the next in a later step once the first is observed complete. Commands touching different things may go together. A prompt rule, not a guard.
2. **Order matters.** Dependent actions (enable → move; stop one thing → start the next; speak → then the emotion that goes with it) are issued in that order across turns.
3. **Spoken feedback for every request.** Acting is not answering: when asked to start, stop, change or cancel something, do it, then `say` what was done once it is observed done — a request never ends in a silent action. This rides WICA's completion re-trigger.

Like the computer persona, it says nothing about hands: the "Hand signs" section (with the `stop` rule) is appended at startup when vision is on ([gesture-input.md](gesture-input.md)). These rules are persona text, verified by reading; the tests drive the scripted `fake` provider and never exercise the model's compliance.

## Testing

`tests/reachy/` — the bridge's `fake` backend or a fake `ReachyMiniApi` (no daemon, no hardware, no cloud):

- **Config:** `load_config` yields `AppConfig` + a `ReachyMiniConfig`; the `reachy` section absent ⇒ the bridge's defaults; a bridge validation error (`daemon.spawn` on `fake`, an unknown key, `motion.tracking: true` with no `faces.detector`) surfaces as `ValueError`; a `faces` block reaches `config.reachy.faces` (detector and switch); a `tts` or `camera` top-level section is rejected; **both committed reachy templates load**.
- **Enter / members:** with a fake api, `enter()` enters it once, `voice` is the `say`, `mic` is a `RobotMicSource` over it, `commands()` are the three actions with `play_emotion` described by the fetched names, `prompt_addendum` names every emotion and the `play_emotion` command; `raise_on_enter` propagates out of `enter()`, carrying the offline hint on a non-`fake` backend and untouched on `fake`; a failing `list_emotions` degrades to `Robot: emotions unavailable` with the plain docstring and an empty addendum; `synthesizer_error` (or no `tts` block) yields the `TTS:` line while the voice stays wired; `exit()` exits the api.
- **`say`:** `"hello"` reaches `api.say` once and returns `"OK"`; `""` returns the empty-text error without calling it; an exception maps to `"TTS error: …"`.
- **`RobotMicSource`:** `start()` returns a queue receiving the api's chunks in order; `stop()` cancels the pump; a raising or ending stream sets `capture_error`, a restart clears it; undrained, only the newest `maxsize` chunks are kept; a second `start()` without `stop()` leaves one stream open.
- **`RobotCameraFeed`:** over a fake api carrying a fake `camera` (a settable frame published under an advancing `frame_id`, `start`/`stop` calls recorded): `latest()` is the api's frame object itself and `published_count` its count; `open()` / `close()` call neither `start()` nor `stop()` on the api's feed; `capture_error` is `None`; `camera(fps=…)` on an entered embodiment returns one over its api. Through `GestureInput` with the scripted detector/classifier, a sign on the fake feed's frame is delivered — the robot path end to end, no `StreamProvider` involved.
- **`RobotControl`:** `play_emotion(<known>)` records the move; `<unknown>` returns a non-raising failure naming the valid names; with motors disabled the motor-precondition message (pinned on the `fake`, which honours it — the sim daemon ignores motor-state changes); `get_motors_state()` reflects `set_motors_state(...)`; `commands()` yields no `list_emotions`.

A live test (the `sim` or a real robot actually moving and speaking) belongs in `tests-e2e/`.

## Open questions

1. **Enforcing the no-collision rule.** If the model keeps issuing `say` + `play_emotion` together, the enforcement is a shared `asyncio.Lock` held by `say` and `play_emotion` (the two speaker/head-spanning verbs) so the second waits for the first, both still `running` in the World. Deferred until observed.
2. **Compact-intent → emotion resolution.** The bridge keeps `play_emotion(name)` literal; friendlier intent mapping ("be happy" → a curated move) stays out.
3. **Motors: agent-facing or lifecycle.** `set_motors_state`/`get_motors_state` stay exposed, with the agent told to assume them enabled and check only on failure; whether real hardware honours motor-state changes, and whether the *lifecycle* should enable them at `enter()` (making the assumption true by construction rather than by convention), is deferred to hardware validation.
4. **Robot state in the UI.** Connection / motor state on a panel of the embodiment's own ([embodiment.md](embodiment.md), open question 1).
5. **Faces as an agent input.** The bridge now reports who is in front of the robot — `api.faces`, an `Observable[FaceReport]` waking a subscriber when the number of faces changes, and `api.attention` (`"engaged"` / `"watching"` / `None`) — which is a ready-made World input: a `people_in_view` count entry fed from `faces.changes()` on the app loop, triggering when someone appears, and an attention line on the UI. Deferred to its own spec section once the camera-feed catch-up is in; the persona already says the robot follows faces, so nothing in the prompt is waiting on it.
