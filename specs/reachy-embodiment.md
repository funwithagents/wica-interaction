---
code:
  - src/wica_interaction/reachy/embodiment.py
  - src/wica_interaction/reachy/robot_control.py
  - src/wica_interaction/reachy/users.py
  - src/wica_interaction/reachy/view.py
  - src/wica_interaction/reachy/config.py
  - src/wica_interaction/reachy/app.py
  - src/wica_interaction/reachy/default/config.json
  - src/wica_interaction/reachy/default/reachy.md
tests:
  - tests/reachy/test_embodiment.py
  - tests/reachy/test_robot_control.py
  - tests/reachy/test_users.py
  - tests/reachy/test_view.py
  - tests/reachy/test_config.py
  - tests/reachy/test_app.py
---

# Reachy Mini embodiment

**Status:** Updated

## Purpose

The agent embodied in a **[Reachy Mini](https://github.com/pollen-robotics/reachy_mini)**: it speaks out of the robot's speaker, listens through the robot's echo-cancelled microphone, sees through the robot's camera, and acts — plays emotions, manages its motors, follows a person with its head — through `reachy-mini-bridge`'s `ReachyMiniBridge`. With a face detector configured it also **perceives the users in front of it** — who arrives, who leaves, who is present, who is inside its personal space, whom it follows — and tells the agent through an input of its own ("Users: `UsersInput`"). One implementation of the [Embodiment](embodiment.md) protocol, gathering what used to be spread over `tts_output.py`, `robot_control.py`, the `RobotMicSource` in `asr_bridge.py` and the `RobotCameraSource` in `gesture_input.py`. Its persona is the robot, [prompts/reachy.md](../prompts/reachy.md). It runs as `wica-interaction-reachy --config <file>`.

## Why the robot owns audio

Audio on Reachy Mini is not a pair of laptop `sounddevice` streams: it is a daemon-owned pipeline with an XMOS **XVF3800** voice processor doing echo cancellation, noise suppression, AGC and beamforming in firmware. Echo cancellation works only if playback goes **through** that pipeline, so speech must be played through the robot, and the mic must be read **from** it (the post-processed stream). That is why this embodiment plays and listens through the bridge and needs none of the computer embodiment's [audio processing](audio-processing.md) — the hardware (and the simulator) deliver a clean signal. See `reachy-mini-bridge/specs/audio/audio.md`.

## Configuration

The config carries the shared sections (`wica`, `asr`, optional `vision` — [configuration.md](configuration.md)) plus one of this embodiment's own, parsed by `reachy/config.py`'s `load_config(data, *, base_dir) -> ReachyConfig`. On the Setup tab the `reachy` section is described by the bridge's own contract (`reachy_mini_bridge.setup`), which delegates its `tts` block to tts-engine's ([setup.md](setup.md)):

```json
{
  "wica": { "agent": { "…": "…", "system_prompt_file": "reachy.md" } },
  "asr": { "module": { "…": "…" }, "audio_source": { "type": "custom" } },
  "reachy": {
    "backend": "sim",
    "robot": {},
    "daemon": { "spawn": "auto", "headless": false, "camera": { "source": "webcam", "device": null } },
    "tts": { "module": { "type": "phonon", "voice": "Micah" } },
    "audio": { "xvf3800": null },
    "face_detection": { "detector": "yunet", "enabled": true, "width": 640, "score_threshold": 0.6, "target_fps": null },
    "motion": { "presence": true, "idle": "breathing", "wobbling": true, "tracking": { "enabled": false } }
  },
  "vision": { "enabled": true, "threshold": 0.55, "fps": 10, "max_hands": 2, "hold_s": 0.3, "release_hold_s": 0.5, "device": null }
}
```

```python
@dataclass
class ReachyConfig:
    app: AppConfig  # the shared sections
    reachy: ReachyMiniConfig
```

**The `reachy` section is a verbatim bridge config**, handed whole to `ReachyMiniConfig.from_dict(…, base_dir=base_dir)` (a relative scene or voice file is the folder's) — every field, default and validation rule is the bridge's own (`reachy-mini-bridge/specs/core/config.md`):

- `backend` — `"real"` (default), `"sim"` (the upstream MuJoCo mockup), or `"fake"` (the bridge's in-package stand-in: no daemon, no hardware — the offline/test path).
- `robot` — upstream `ReachyMini(...)` kwargs, forwarded verbatim.
- `daemon` — how the bridge brings up the daemon it talks to (`spawn`, `headless`, `scene`, `camera`, `preload_datasets`, `startup_timeout`). Only `fake` has no daemon, so `spawn` other than `"never"` is rejected there.
- `tts` — **the voice**: a tts-engine `engine` block whose `module.type` names the provider; the bridge builds the synthesizer itself from it. The provider's extra must be installed — the same `tts-<name>` extras the computer embodiment uses (`tts-elevenlabs`, `tts-gradium`, `tts-pocket`, …) ([project.md](project.md), "Extras"). The committed default is `pocket` (the local model, voice `george`): no key, no network at synthesis time, weights downloaded on first run. Absent ⇒ no voice (`say` unavailable, everything else unaffected).
- `audio.xvf3800` — the tuned XVF3800 profile; `null`/absent keeps the firmware defaults.
- `face_detection` — the bridge's **opt-in face detection** (`reachy-mini-bridge/specs/vision/user_perception.md`): `detector` names what finds the faces in the camera feed's frames — `null` (the default: nothing is detected, no model is loaded), `"yunet"` (the bridge's shipped detector, upstream's own model; nothing to install, its weights a Hugging Face download on first use) or `"custom"` (a detector registered from code; not used here) — `enabled` (default `false`) whether the detection loop runs from session entry for the caller's sake (`bridge.faces`, the observable face report), `score_threshold` (default `null` = upstream's own, 0.6 today; the confidence the shipped detector keeps a face from, strictly between 0 and 1 — the default config states `0.6` so the knob is in front of whoever tunes it: raised against posters and reflections, lowered to catch faces farther away), and the detector's cost knobs: `width` (default `640`, the width the shipped detector subsamples each frame to; `null` = the full frame) and `target_fps` (default `null` = once per new camera frame; a ceiling on detections per second — the wireless robot's lever). Detection needs no motors. **With a detector configured, this embodiment runs detection for the whole session whatever `enabled` says** (`enter()` step 5): the users the agent is told about are the bridge's users present (`bridge.users`), which exist only while detection runs, and tracking alone would not keep it running — `stop_head_tracking()` with detection off stops the loop and every user leaves.
- `motion` — presence, the idle mode (`"breathing"` by default, else `"hold"` / `"custom"`), wobbling — the bridge's own aliveness, none of it agent-facing — and tracking, whose switch and target are the agent's (below). `presence` and `wobbling` default `true`; **`tracking` is an object, off by default (`enabled: false`), and needs a detector** — its other fields are the bridge's tracking posture (mirroring, approach, personal space, frame delay), left at the bridge's defaults — the bridge's own head tracker is a client of the detection loop, so `tracking.enabled: true` with `face_detection.detector` `null` is a bridge `ConfigError`, as is a `tracking` written as a boolean (surfacing as `ValueError` from `load_config`, the file named). **Tracking is the agent's, so the default config starts it off** (`tracking.enabled: false`) with `yunet` configured: the agent starts and stops it through `start_tracking` / `stop_tracking` (below), choosing whom to follow. `tracking`'s other fields — the posture — stay the config's: the agent says *whether* and *whom*, the config *how*. A config with `tracking.enabled: true` still loads and starts the session tracking the bridge's automatic choice, until the agent says otherwise. A deployment with no `face_detection.detector` has neither the users input nor the tracking Commands.

Absent `reachy` section ⇒ `ReachyMiniConfig()` (the `real` backend, upstream defaults, no voice). Its validation errors are the bridge's `ConfigError`, a `ValueError` subclass, so `load_config`'s one error type holds. The section is the one place that imports `reachy_mini` at load (lazily, to validate `robot` keys against upstream's signature). The ASR config's `audio_source` is `{"type": "custom"}` here (no local device is opened; the mic is the embodiment's); the ASR module needs no matching rate, since the mic source declares `bridge.mic_sample_rate` and the engine converts. Any top-level key outside `wica`/`asr`/`vision`/`reachy` — a `tts`, `audio_processing` or `camera` section — is rejected.

## `ReachyEmbodiment`

```python
class ReachyEmbodiment:
    display_name = "Reachy Mini"

    def __init__(
        self,
        config: ReachyConfig,
        *,
        bridge: ReachyMiniBridge | None = None,  # test seam
    ) -> None: ...
```

Construction does nothing but keep the config (and the injected fake, if any). `enter()`, awaited on the app loop:

1. **The one `ReachyMiniBridge`.** `ReachyMiniBridge(config.reachy)` (the bridge builds the voice from its `tts` block at construction, degrading to no voice rather than raising), then `await bridge.start()` — the daemon (when configured), the robot connection, the media session, the mic feed, the camera feed and the motion loop come up, and every async verb binds to this loop. The bridge's session is the async pair `start()` / `stop()` plus a `running` property, with `async with` as sugar over the pair; this embodiment drives the pair, since the protocol's `enter` / `exit` are exactly the "host with lifecycle hooks of its own" the pair exists for (`reachy-mini-bridge/specs/core/bridge.md`, "Lifecycle"). Construction never connects; only `start()` does, so **any** exception here (an upstream `reachy_mini` error, the bridge's `DaemonError`/`BridgeError`) propagates out of `enter()` — that is the "no embodiment" degrade in [embodiment.md](embodiment.md): the banner reads `Reachy Mini: …`, the agent runs on typed text with nothing to speak or move through. **The offline hint rides that message**: on a backend that needs something outside the process (`real`, `sim`), the failure is re-raised as a `RuntimeError` whose text is the original message plus how to develop with no robot (`reachy.backend` `"fake"` with no `daemon` block, or `"sim"`) — the banner shows it verbatim, and only this embodiment knows its backends, so the shared page carries no robot wording. On `fake` the error propagates untouched. A failed `start()` has already unwound whatever it brought up, so `enter()` has nothing to stop. The enter is awaited without an app-side timeout, so a `sim` daemon that never becomes ready fails only after the bridge's `daemon.startup_timeout` (45 s by default). This single instance backs `say`, the mic stream, the camera and the action commands: one robot connection, one media session.
2. **The voice's health.** After a successful enter (and only when no `bridge` was injected — a fake carries its own `say`): no `reachy.tts` block ⇒ `missing` gets `TTS: no reachy.tts block configured (say is unavailable)`; else `bridge.synthesizer_error` set ⇒ `TTS: <error>`. The robot is up either way; `voice` is still the `say` below, which then returns `"TTS error: …"` when called (the bridge's `say` raises `BridgeError` without a synthesizer).
3. **The emotion names.** `await bridge.list_emotions()` once — a snapshot the session keeps (the bridge loads its library once per connection, so nothing changes mid-session). A failure (on the real backend, the library is a HuggingFace dataset read) degrades to `missing` `Robot: emotions unavailable: …` and no names: the prompt then carries no emotion section and `play_emotion` keeps its plain docstring.
4. **The mic.** `RobotMicSource(bridge)`, below. Nothing to start for either feed: `bridge.mic` and `bridge.camera` are the session's own, started with the media session and alive until exit — a consumer subscribes to them, never starts or stops them.
5. **The users**, only with `reachy.face_detection.detector` set: detection on (`await bridge.set_face_detection(True)` when the config left it off — logged at `INFO`, since it overrides the file), then `UsersInput(bridge)` built, not started (the lifecycle starts it once the agent runs). A failure here — the detector's build failing — degrades to `missing` `Users: …` with no users input and no tracking Commands; the rest of the robot is up.

The members:

- **`voice`** — the bound `say` below, always (the robot came up).
- **`mic`** — the `RobotMicSource`; **`mic_error`** — its `capture_error`.
- **`camera(fps=…)`** — `RobotCameraFeed(bridge)`, below; `fps` is ignored (the feed runs at the daemon's rate, the gesture stages pace themselves).
- **`commands()`** — `RobotControl(bridge, emotions=names, tracking=<users up>).commands()`, below.
- **`inputs()`** — `(UsersInput,)` when step 5 built it, else `()`.
- **`view()`** — `robot_view(bridge)`: the camera with the faces, the head line and the users ("The page view", below).
- **`prompt_addendum`** — `emotions_prompt_section(names)` when names were fetched, then `users_prompt_section()` when the users input is up; `""` with neither.
- **`exit()`** — `await bridge.stop()`: stops the motion loop, the camera feed (its `latest()` reads `None` from then on), the mic feed (ending every `audio_input()` subscriber), the media session and the robot connection (and the managed daemon); a no-op on a bridge that never started. The lifecycle runs it after ASR and vision are stopped, so the mic pump is cancelled and the hand stage joined while the media session and the feed are still live ([app.md](app.md), "Wiring and lifecycle").

### `say`

```python
async def say(self, text: OutputStream) -> str:
    """Your voice: spoken aloud to the person, out of the robot's speaker."""
```

A thin, non-raising wrapper over `bridge.say_stream` (`reachy-mini-bridge/specs/audio/audio.md`, "`say_stream`"). WICA calls it at the `<say>` tag's first words with an `OutputStream` of the tag's body, and the bridge's tts-engine synthesizer speaks from those first words while the model is still writing. It hands the bridge the tag's body cleaned into sayable text, from its first words (`speakable`, [embodiment.md](embodiment.md), "What reaches a TTS is sayable"): a tag that ends with nothing to say — empty, whitespace, or only punctuation, markup or emoji — returns `"TTS error: text must not be empty"` without touching the robot. Any exception from `bridge.say_stream` (no synthesizer, a synthesis or media failure) maps to `"TTS error: <message>"`; success returns `"OK"`. `say_stream` completes when the utterance has been heard on the robot, so the output stays `running` for the real duration of the speech. Cancelling the output's task is how speech stops: the bridge flushes the speaker and closes the text stream. Like `say`, one utterance plays at a time and the newest wins. `OutputStream.heard` is not set: after a cut the model's history keeps the whole text it wrote.

### `RobotMicSource`

The `AudioSource` (`asr-engine/specs/audio-source.md`) the ASR engine drains — **one subscriber** of the bridge's mic feed, `bridge.mic` (`reachy-mini-bridge/specs/audio/microphone.md`). The feed is the **one reader** of the robot's microphone: a thread the session owns, looping upstream's one-shot `get_audio_sample()` into a 2 s ring, and every `audio_input()` call is an independent subscriber with its own cursor, receiving every chunk in order. So this source never splits the capture with another consumer — a wake-word detector or a level meter added later subscribes beside it and both hear everything — and nothing in this app calls `get_audio_sample()`:

```python
class RobotMicSource:
    def __init__(self, bridge: ReachyMiniBridge, *, maxsize: int = 200) -> None: ...
    def start(
        self,
    ) -> asyncio.Queue[
        bytes
    ]: ...  # cancel any live pump, spawn a fresh one, return its queue
    def stop(self) -> None: ...  # cancel the pump task
    @property
    def capture_error(self) -> str | None: ...
```

- `start()`/`stop()` are **synchronous** — the protocol the engine drives its source through: it calls `start()` (not awaited) from inside its start coroutine to obtain the queue and `stop()` on teardown. `start()` grabs the running loop and `create_task`s the pump (the `async for chunk in bridge.audio_input()` loop lives there), returning the queue immediately. It must be **restartable**: each `start()` resets its queue, task and `capture_error`, and first cancels any pump still live from a `start()` with no intervening `stop()`, so no pump is orphaned.
- `bridge.audio_input()` (the defaults: `mono=True`, `preroll_s=0.0` — the capture from the pump's start on) yields **int16 LE mono PCM at 16 kHz**, so the pump is "iterate and enqueue", no resampling. The source declares that as its `audio_format` (mono linear16 at `bridge.mic_sample_rate`, read at construction — the source is built once the bridge runs), and the ASR engine converts it for a module fed another rate. The audio is already echo-cancelled by the XVF3800.
- **Bounded, drop-oldest queue.** At most `maxsize` chunks (200 ≈ 2 s of the robot's 10 ms blocks); when the engine falls behind, the oldest chunk is dropped — stale speech is useless, memory stays bounded. This queue, not the bridge's ring, is where the engine's lag is absorbed: the pump only enqueues, so it always keeps up with its subscription and the bridge's lapped-subscriber gap warning never fires for it.
- **A dead stream is visible, not silent.** If `audio_input()` raises, or ends on its own (the media session closed under it), the pump logs it and records `capture_error` (`"mic stream failed: …"` / `"mic stream ended"`); the engine would otherwise keep reporting `running` while hearing nothing. A deliberate `stop()` records nothing.

### `RobotCameraFeed`

The `CameraFeed` ([gesture-input.md](gesture-input.md), "Frames come from the embodiment") over the bridge's camera feed, `bridge.camera` (`reachy-mini-bridge/specs/vision/camera.md`) — the **one reader** of the robot's camera: a thread the session owns, looping upstream's one-shot `get_frame()` and publishing the newest `CameraFrame` (`frame_id`, `ts`, the BGR `HxWx3` `uint8` `image`, `head_pose`) for any number of consumers — the bridge's face-detection loop, this app's hand stage — to sample without stealing frames from one another. `CameraFrame` is a `FrameLike` by shape, so the hand stage consumes it as it stands:

```python
class RobotCameraFeed:
    def __init__(self, bridge: ReachyMiniBridge) -> None: ...
    def open(self) -> None: ...  # nothing: the feed is the session's, started at enter
    def close(self) -> None: ...  # nothing: never stops the bridge's feed
    def latest(self) -> CameraFrame | None: ...  # bridge.camera.latest()
    @property
    def published_count(self) -> int: ...  # bridge.camera.published_count
    @property
    def capture_error(self) -> str | None: ...  # always None (below)
```

- **A thin adapter, deliberately.** `latest()` and `published_count` forward to `bridge.camera`; nothing is copied, paced or re-numbered — the frame the hand stage reads is the very object the bridge's detector read, at the daemon's rate (10 fps on a local daemon; the stages sample at `vision.fps`). Reading the feed is an instant, thread-safe call from the hand stage's own thread: no hop to the app loop, no timeout, nothing async — the loop the bridge was entered on is not needed here.
- **`open()` / `close()` are no-ops.** The feed's lifecycle is the session's: the bridge's `start()` starts it after the media session, its `stop()` stops it and resets `latest()` to `None`. The adapter never calls `bridge.camera.start()` / `stop()` — stopping it would blind the bridge's own face detection and head tracking, which sample the same feed. (The adapter exists for exactly this reason: the bridge's feed has `start`/`stop`, and the gesture module's protocol has `open`/`close`, so it cannot be handed over by accident.)
- **`capture_error` is always `None`.** The feed has no failure of its own to report — a read that keeps failing is logged by the bridge and the last frame stays published; "no frame" is simply `latest()` not advancing. The gesture module's stall detection covers that (`no camera frame for 3 s` on the Vision line — the headless sim's absent camera, a daemon with no camera, the macOS camera draw the bridge's launcher works around), so a dead camera is visible without the adapter inventing an error.
- No mirroring: the robot's view is not a selfie. The frame is shared and read-only by the feed's rule; the hand stage only reads it (the crop it publishes is a copy, the library's rule too).

## `effects()`

What the latency view takes as each Command's effect ([latency-view.md](latency-view.md), "`EffectStart`"): `say` ends at `first_push` on the bridge's `reachy_mini_bridge.say` span — the first audio handed to the robot's speaker; the bridge does not measure the delay from there to the sound — and `play_emotion` at `motion_started` on `reachy_mini_bridge.play_emotion`, the robot beginning to move. The names are the constants `reachy_mini_bridge.tracing` exports.

## Users: `UsersInput`

The robot's own perception, delivered to the agent through `inputs()` ([embodiment.md](embodiment.md), "`inputs()` is the mirror of `commands()`"): the **users in front of it**, read from the bridge's users present and head tracking (`reachy-mini-bridge/specs/vision/user_perception.md` "The users present", `specs/motion/head_tracking.md`). The word is the bridge's: a *user* is a person the bridge holds present. The bridge decides who that is — a face confirmed over 0.3 s, kept while a move of the robot's own looks away, dropped after 2 s missing where the camera looks or 20 s unseen — so this module only *words* it: it owns which changes wake the agent, what each says, and the standing picture of who is there.

**Only what concerns whom the robot attends to wakes the agent.** Two changes, no other (2026-11-18): **the tracked user leaving** — the user the head follows, or the one the agent pinned — and **a user arriving while nobody is tracked**. Everything else — another user arriving while the head follows someone, a user it does not follow leaving, the personal space — wakes nothing and is **not an event**: it changes only the standing entries (`users_in_view`, `personal_space`), where the next step, whatever wakes it, reads who is there as background. Writing it to `user_event` as well would hand it, for the entry's 2 s, to any step another input woke — a finished command — as something that just happened, and the model, told it may greet an arrival, greets someone it never meant to address (observed 2026-11-18). Why: every wake is a model call, and one that lands mid-sentence or mid-emotion can derail what the robot is doing; on the real robot, an emotion sweeps the camera across the room, and each face it catches on the way is a genuine arrival to the bridge, which woke the agent for people the person in front of it never saw. Telling the model to ignore those in the persona would keep every call and leave the choice to the model's compliance; the gate is in the code, and the persona says what the two wakes mean.

```python
Side = Literal["left", "front", "right"]


@dataclass(frozen=True)
class UserChange:  # one of user_event's: what just happened to a user
    kind: Literal["arrived", "present", "left"]  # present: already in view at start
    user: int
    side: Side | None = None
    distance_m: float | None = None
    # wakes the agent: the tracked user leaving, or an arrival with nobody tracked
    wakes: bool = False


@dataclass(frozen=True)
class UserInView:  # one of users_in_view's
    user: int
    side: Side
    distance_m: float | None
    in_view: bool = True  # False: the camera is not framing where they were


@dataclass(frozen=True)
class PersonalSpace:  # personal_space's value while judged
    user: int | None  # the user inside; None: nobody
    distance_m: float | None = None
    # what just happened, for SPACE_CHANGE_S: "came_in" (user entered) or "stepped_out" (`left` left)
    change: Literal["came_in", "stepped_out"] | None = None
    left: int | None = None


@dataclass(frozen=True)
class TrackedUser:  # tracked_user's value
    active: bool  # tracking on
    user: int | None = None  # the user followed
    pinned: int | None = None  # the user the agent chose; None: the automatic target
    pinned_present: bool = False


class UsersInput:  # an `Input` (embodiment.py)
    def __init__(
        self,
        bridge: ReachyMiniBridge,
        *,
        clock: Callable[[], float] = time.monotonic,
        refresh_s: float = STANDING_REFRESH_S,
    ) -> None: ...
    @property
    def entries(
        self,
    ) -> Sequence[
        InputEntry
    ]: ...  # user_event, users_in_view, personal_space, tracked_user
    async def start(
        self, deliver: Deliver
    ) -> None: ...  # the start-up snapshot, then the watchers
    async def stop(self) -> None: ...  # cancels the watchers


def users_prompt_section() -> (
    str
): ...  # "## The users around you …", appended to the persona
```

**One entry per concept, each holding what was perceived.** The World holds objects — `user_event` a tuple of `UserChange`, `users_in_view` a tuple of `UserInView`, `personal_space` a `PersonalSpace`, `tracked_user` a `TrackedUser` — and each entry's `serialize_fn` words them for the prompt, its `display_fn` for the transcript, as `speech_input` holds the transcript and its serializer says *heard*. So the World panel shows the objects, the prompt the words. Positions are kept as coarsely as they are said — a side, a distance to 0.1 m (0.5 m past a metre) — so a user swaying changes no value and writes nothing.

| Entry | Kind | Wakes the agent | Rendered (examples) | In the transcript |
|---|---|---|---|---|
| `user_event` | transient, `tuple[UserChange, ...]`: `ttl` `USER_EVENT_TTL` (2 s), `None` renders nothing — like `speech_input` | when a write brings a change that `wakes` (`user_event_wakes`): the tracked user leaving, a user arriving (or present at start) while nobody is tracked; other changes are not written to it (only the standing entries change) | `A user came into view: user 3, about 0.7 m away, in front of you.` · `User 3, whom your head was following, left your view.` · `A user is in view as you start: user 3, …` | `👤 <the event>` |
| `users_in_view` | **standing**, `tuple[UserInView, ...]`: no TTL | never | `In view: user 3 (about 0.7 m away, in front of you), user 5 (about 1.5 m away, to your left; out of your sight right now).` · `Nobody is in view.` · omitted while detection is not running | `👥 <who is present>` |
| `personal_space` | **standing**, a `PersonalSpace`, judged only while the head follows someone (the bridge judges it for the followed user); omitted with tracking off | never (2026-11-18: it did, on who is inside changing; the bridge judges it from the face's size, which a move of the robot's own blurs and skews) | just changed: `User 3 just came into your personal space: they are very close to you (about 0.3 m away).` · `User 3 just stepped back out of your personal space; nobody is in it now.` — settled: `User 3 is in your personal space, very close to you (about 0.3 m away).` · `Nobody is in your personal space.` | `📏 <the line>` |
| `tracked_user` | **standing**, a `TrackedUser` | never | `Your head is following user 3, as you chose.` · `Your head is following user 3.` (the automatic target) · `Your head is turning to follow user 3, as you chose.` (between a pin and that user's next sighting: the bridge reports the pin from the call, the follow from the sighting) · `You chose to follow user 3, who is not in view; your head is waiting for them.` · `Your head will follow whoever comes in front of you; it follows nobody yet.` · `You are not following anyone with your head.` | `🎯 <the line>` |

The standing entries — `users_in_view`, `tracked_user` and `personal_space` — are the app's exception to "no mirrored embodiment state" ([app.md](app.md), "World, Commands, persona"): the agent holds the tracking Commands, and to follow user 3 it must know, in the step where it decides, that user 3 is present and whom it follows already.

**What each bridge change becomes.** Two watchers on the app loop, each reading every published report in order (`changes(every=True)`, so a busy loop misses no arrival):

| Bridge change | `user_event` | The standing entries |
|---|---|---|
| `bridge.users`: `arrived` | while nobody else is tracked, one line per user: `A user came into view: user <n>, <where>.`, waking; otherwise nothing | re-rendered |
| `bridge.users`: `left`, reason `"left"` (missing where the camera looked) | the tracked user: `User <n>, whom your head was following, left your view.`, waking; anyone else: nothing | re-rendered |
| `bridge.users`: `left`, reason `"unseen"` (the 20 s net) or `"stopped"` (detection stopped) | nothing: nobody was seen walking away | re-rendered |
| `bridge.users`: `recognised`, `active` | nothing (no recognising detector yet) | re-rendered |
| `bridge.head_tracking`: any change (tracking on or off, the followed user, the pin, the personal space) | nothing | re-rendered; the tracked users noted (below) |

- **Where a user is, in words.** The side from the face's `x` at its last sighting — `to your left` below −⅓, `to your right` above ⅓, `in front of you` between (the robot's camera is not mirrored: the image's right is the robot's right) — and the distance from its `distance_m`, `about 0.7 m away` (to 0.1 m below 1 m, to 0.5 m from there; left out when the bridge has none). Both are relative to where the head looked: good for "look at the user on your left" in the moment, not a map.
- **The standing entries are kept current, cheaply.** They are re-rendered on every change above and once a second (`STANDING_REFRESH_S`), and each is written only when its value changed — so a user moving a little rewrites nothing, and one stepping from one side to the other is seen within a second.
- **Who is tracked, as the module last saw it.** *Tracked* is the user the head follows (`track_id`) and the user the agent pinned (`pinned_track_id`), while tracking is on. The two watchers are separate tasks, so the head tracking report that drops a departed user may be read before the users report saying they left — and, with the automatic target, the one that grabs a newcomer before the report saying they arrived. So the module keeps its own set of tracked users, refreshed from `bridge.head_tracking` on every re-render, where a user dropped from tracking **because they are no longer present** stays until their departure has been judged. A departure wakes when the user was in that set; an arrival wakes when the set holds nobody but the users arriving in that same report (a newcomer the tracker grabbed first is not *somebody tracked*). The start-up snapshot follows the arrival rule.
- **A wake is judged per change, not per write.** Each `UserChange` carries `wakes`, decided when it is produced; `user_event_wakes(old, new)` wakes on a write bringing a change that wakes and was not in the previous value — so a non-waking change appended to a waking one (below) does not wake twice.
- **The personal space says what just changed.** A change is written with what happened (`change`: `came_in`, or `stepped_out` with the user who `left`) and rewritten settled `SPACE_CHANGE_S` (2 s) later, so a step that happens to run within those seconds reads that it just happened. It wakes no one: the persona no longer asks for a reaction to it.
- **Events that land together are written together.** A `user_event` write within `EVENT_JOIN_S` (0.5 s) of the previous one appends its changes to it rather than replacing them, so two arrivals on one coalescing window both reach the step they wake; later, a write starts afresh. Whether the joined write wakes is judged on the changes it adds (above).
- **An event is written inside a perception span** ([latency-view.md](latency-view.md), "The perception span"): a root span from the event's origin — for an arrival, the first sighting of the arriving user's track (`User.first_seen_ts`), with the bridge's confirmation as a wait step (`confirming the person`); otherwise the report's frame — so the reaction it wakes is measured from it. The bridge's stamps are converted with its `tracing.wall_clock`.
- **The start-up snapshot.** The bridge's detection starts with the session, before the agent runs, and a subscription sees only what is published after it. So `start` reads who is already present first, in the same loop step as subscribing, delivers the standing entries, and — when someone is there — one `user_event`, `A user is in view as you start: …`, so the robot greets whoever stands in front of it at launch (waking, unless tracking already follows someone else).
- **What it never claims.** A number is a track, not an identity: the same person coming back after a break gets a new one, and the wording never says *the same person* or a name.
- **A non-raising producer.** A watcher that fails is logged and ends; the robot keeps running, the entries stop changing. `stop` cancels both watchers and the refresher and waits for them.

## The page view

What the page's camera and Vision section show of the robot ([app.md](app.md), "What the user sees"), from `reachy/view.py`. One read of the bridge's published reports per tick, no round trip to the robot:

```python
def robot_view(bridge: ReachyMiniBridge) -> EmbodimentView: ...
def draw_faces(
    frame: np.ndarray,  # RGB
    positions: Sequence[tuple[float, float]],  # normalised, [-1, 1], x right, y down
    target: int | None = None,  # the index drawn thicker: the face the head follows
    *,
    labels: Sequence[str] = (),  # per face, "#<id>"
    mirror: bool = False,
) -> np.ndarray: ...
def users_markdown(users: UsersReport, tracking: HeadTrackingReport) -> str: ...
```

- **The frame** — `bridge.camera.latest()` through `display_frame`, then `draw_faces` with the faces of `bridge.faces.value`: a green square on each (side 15 % of the frame's height), the one whose `track_id` the head follows (`bridge.head_tracking.value.track_id`) drawn thicker, the frame flipped (`mirror=True`), and each face's `#<track_id>` stamped above its square — below it when there is no room above — in green on a black plate, **after** the flip, so it reads the right way round; a 3×5 bitmap font of the digits and `#`, scaled with the frame's height. Plain numpy, no vision code. No frame (before the first, after the session) ⇒ none.
- **The status** — `users_markdown`, under a `Users` subheading: a **head line** — `**Head:** follows #4` (`, pinned` when the agent pinned them), `watching` (`, waiting for #3` while a pinned user is not followed), or `tracking off` — then a **users table**, one row per user present (`bridge.users.value`, in `track_id` order): `#id` (`●` on the one the head follows), `seen`, `in view`, `missing` (s), where (the side and distance as the agent is told them, `UsersInput`'s wording — the camera's own left and right, not the mirrored picture's), with `_No detector is looking._` while detection is not running and `_Nobody present._` when it is and nobody is. The faces on the frame are who is in the picture; the users are who is present — through an emotion that turns the head away, a user stays in the table, `in view` no, under the same number.
- **No detector configured** ⇒ the frame without faces and no status.

## `RobotControl` — the action commands

The agent does not just talk, it *acts through the robot*: a small set of WICA **Commands** wrapping `ReachyMiniBridge` verbs, the same first-class, observable, cancellable mechanism `say` uses. The bridge holds all robot state and orchestration; this is a thin, **non-raising** adapter.

```python
def emotions_prompt_section(
    names: Sequence[str],
) -> str: ...  # "## Your emotions …", appended to the persona


class RobotControl:
    def __init__(
        self,
        bridge: ReachyMiniBridge,
        *,
        emotions: Sequence[str] | None = None,
        tracking: bool = False,
    ) -> None: ...

    def commands(self) -> tuple[Command | Callable[..., Any], ...]:
        """play_emotion (a Command whose description names the valid emotions, when known),
        get_motors_state, set_motors_state, and — with `tracking` — start_tracking, stop_tracking."""

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

    async def start_tracking(self, user: int | None = None) -> str:
        """Follow a user with your head, to show whom you are paying attention to. Give their
        number from the users in view to follow that user only; leave it out to follow whoever is
        in front of you. Your head keeps following until you call stop_tracking."""

    async def stop_tracking(self) -> str:
        """Stop following users with your head; it goes back to its idle movement."""
```

- **Registered as Commands** by `wire`, through `commands()`: a bound method's name/signature/docstring is what the model sees, except `play_emotion`, which — when the names are known — is wrapped as `Command(play_emotion, description=…)` listing the valid names right where the model picks the tool. With no names the docstring is used unchanged.
- **Non-raising, like every Command.** `play_emotion` **moves the robot**, so the bridge raises `MotorsNotEnabledError` when the motors are not `"enabled"`: caught and returned as `"Can't do that: the motors are off. Call set_motors_state('enabled') first."` — the agent decides to enable and retry; the app never silently auto-enables (the bridge's fail-fast stance). An unknown emotion name or motor state (`ValueError`), or any other `BridgeError`/connection error, returns `"<verb> failed: <message>"` — for emotions followed by `Valid names: …` when known. Success returns a short confirmation or the state read back.
- **The motors are assumed enabled; their state is checked only on failure.** Enabled is the normal state of a running robot, and a state check before every move costs a turn (and, under the no-collision rule below, a whole extra step) to learn what is almost always already true. So every description — the two motor docstrings, `play_emotion`'s, and the persona's "How you move" — tells the model to just move, and to reach for `set_motors_state("enabled")` **only** after a move has come back with the motor-precondition message, then retry. `get_motors_state` stays registered, as the read-back for that recovery (and for an unexpected move), not as a pre-flight check. This is prompt/description guidance, not a guard: the bridge's precondition is unchanged and still the thing that reports the truth.
- **No `list_emotions` command.** The names are known at startup and given to the model up front (the prompt section through `prompt_addendum`, the tool description through `commands()`), both in the model's cached prefix; a round-trip command would spend a turn on information that never changes during a session.
- **Tracking is the agent's: whom it attends to.** `start_tracking(user)` is `bridge.start_head_tracking(track_id=user)` — that user alone, pinned (`user` left out: the bridge's automatic choice, the biggest face in view) — and `stop_tracking()` is `bridge.stop_head_tracking()`. Registered only with the users input up (a detector configured), since the numbers come from `users_in_view`. Non-raising: a user no longer present (the bridge's `ValueError`) answers `"Can't follow user <n>: they are not in view."`, any other failure `"<verb> failed: <message>"`, success `"OK — your head now follows user <n>."` / `"… whoever is in front of you."` / `"OK — your head no longer follows anyone."`. A pinned user who leaves leaves the head waiting on them (the bridge keeps the pin): the agent hears the departure and pins someone else, returns to automatic or stops. Tracking moves the head through the motion loop's gaze layer, not as a primary — an emotion plays over it and the gaze blends back — so it collides with neither `say` nor `play_emotion`.
- **Presence, breathing, wobbling are not agent-facing.** Observed behaviour showed the model treating idle aliveness as its responsibility when tracking was exposed as a switch (plan 202609161700 removed it); it is not — the bridge's motion loop keeps the robot alive between verbs, driven by the `motion` config, and the persona stays silent about all of it. Tracking came back as *whom to attend to*, worded so in the descriptions and the persona, with the users to choose from in front of the model.
- **Not independent of `say`.** `play_emotion` plays a sidecar sound and moves the head, so it collides with a `say` in flight; WICA runs a response's Commands concurrently, so the persona's action rules (below) sequence them across steps. No lock in v1 (open question 1).

## Entry point

`wica_interaction.reachy.app:main` — `wica-interaction-reachy` in `[project.scripts]`. The module declares the app ([setup.md](setup.md)) and runs it:

```python
SECTIONS = (
    Section("wica", wica.setup, title="Agent"),
    Section("asr", asr_engine.setup, title="Speech recognition"),
    Section("reachy", reachy_mini_bridge.setup, title="Reachy Mini"),
    Section("vision", VISION, title="Hand signs"),
)
APP = App(
    name="wica-interaction-reachy",
    title="WICA Interaction — Reachy Mini",
    host="wica-interaction",
    sections=SECTIONS,
    default_config=DEFAULT_CONFIG,  # reachy/default/config.json
    autostart=True,
)


def main() -> None:
    run_app(
        APP,
        load=load_config,
        embodiment=ReachyEmbodiment,
        ensure_persona=ensure_persona,
    )
```

`run_app` ([app.md](app.md)) runs it from its folder. **The default config** (`reachy/default/config.json`) is the Hugging Face Hub agent, the local `mlx-audio` recognizer and `phonon` voice, a simulator daemon the bridge spawns itself with the webcam as its camera, the shipped `yunet` face detector with detection on, every motion switch shown at its default — head tracking off, the agent's to start —, vision on; the `reachy` section keeps to the fields that matter here and leaves the rest at the bridge's defaults. Pointing it at what you drive is a change on the Setup tab: `"real"` for the physical robot, `"sim"` for the MuJoCo simulator, `"fake"` (with no `daemon` block) for offline development. What a backend needs on the machine (the `reachy-sim` extra, the face detector's weights, the emotions library, the voice's model) shows there as items to install or prepare. With autostart on, a robot's app launched at boot starts its session by itself ([session.md](session.md), "Autostart").

## Persona

`reachy.md` (shipped as `reachy/default/reachy.md`, copied into the app's folder at first launch and edited there — [setup.md](setup.md)): the voice and mind of a Reachy Mini in a spoken, physical interaction. Beyond the shared voice/dictation rules (how a dictated passage ends comes from `start_dictation`'s reply, not from the file — [asr-bridge.md](asr-bridge.md); an empty passage means nothing came through), it tells the model how it moves — `play_emotion` kept for a real moment, a few times in a conversation at most (most replies have none; the tool description repeats it), with the names listed "further down" (the seeded section), the motors assumed on with `set_motors_state("enabled")` kept for recovering from a move that reported them off, resting in `gravity_compensation` — and never to narrate movements; it says nothing about presence/breathing/wobbling, and says that following people with its head is its own to start and stop, described further down (the "The users around you" section, when there is one). It carries the shared **self-knowledge rules** ("What you can do, and what you know" — [app.md](app.md), "World, Commands, persona"): the robot can do only what its commands do — no command looks left or nods, so a request for one is declined out loud, never pretended — and it knows its own state only from the commands it called, their results, and the World: it is not following anyone unless `start_tracking` succeeded or `tracked_user` says so. It carries the three **action rules** WICA's generic primer cannot know, because these Commands share one body and one speaker:

1. **No colliding actions in one turn.** Speaking (a `<say>` tag) with `play_emotion` (the emotion's sidecar sound plays over the speech and its motion fights the talking head), two emotions, a move with a motor-state change, a `cancel_command` with the action replacing what it cancels: do the first, then the next in a later step once the first is observed complete. Things touching different parts may go together. A prompt rule, not a guard.
2. **Order matters.** Dependent actions (enable → move; stop one thing → start the next) are done in that order across turns. The end of speech wakes no step by itself (WICA's output wakes only through the model's `<next>` note), so speak → then act (stop following, an emotion already decided on) is one response: the tag, and a `<next>` note naming what remains. The example is deliberately not "the emotion that goes with it": worded that way, a first session showed the model treating every reply as speech plus an emotion; most replies carry no `<next>` note.
3. **Spoken feedback for every request.** Acting is not answering: when asked to start, stop, change or cancel something, do it, then say what was done once it is observed done — a request never ends in a silent action. This rides WICA's completion re-trigger: an action Command's completion, `cancel_command`'s included, wakes a step.

Like the computer persona, it says nothing about hands: the "Hand signs" section (with the `stop` rule) is appended at startup when vision is on ([gesture-input.md](gesture-input.md)). Nor does it describe the users it sees: `users_prompt_section()` is appended through `prompt_addendum` when the users input is up — what the four entries say; that a number is a label, never said aloud, and the same person may come back under another; that it is woken by its users only twice — someone arriving while it follows nobody, and the user it follows leaving — and that other comings and goings show only in who is in view, and who is very close in the personal space, for it to know, not to react to; that an arrival may be greeted and the followed user seen off — or neither, without breaking off a conversation; and how to attend: follow a user who comes into view when the head follows nobody, the one it talks with when several are there; when the followed user leaves, follow another user in view if there is one (automatic tracking may already have turned to them: `tracked_user` says), stop when nobody is left — tracking going together with speech in one turn, since it collides with nothing. These rules are persona text, verified by reading; the tests drive the scripted `fake` provider and never exercise the model's compliance.

## Testing

`tests/reachy/` — the bridge's `fake` backend or a fake `ReachyMiniBridge` (no daemon, no hardware, no cloud):

- **Config:** `load_config` yields `AppConfig` + a `ReachyMiniConfig`; the `reachy` section absent ⇒ the bridge's defaults; a bridge validation error (`daemon.spawn` on `fake`, an unknown key, `motion.tracking.enabled: true` with no `face_detection.detector`, a boolean `motion.tracking`) surfaces as `ValueError`; a `face_detection` block reaches `config.reachy.face_detection` (detector, switch, score threshold and cost knobs); a `score_threshold` outside (0, 1) surfaces as `ValueError`; a `tts` or `camera` top-level section is rejected; a relative `daemon.scene` resolves against `base_dir`; **the default config loads**.
- **Enter / members:** with a fake bridge, `enter()` starts it once (`running` reads `True`), `voice` is the `say`, `mic` is a `RobotMicSource` over it, `commands()` are the three actions with `play_emotion` described by the fetched names, `prompt_addendum` names every emotion and the `play_emotion` command; a `start()` that raises propagates out of `enter()`, carrying the offline hint on a non-`fake` backend and untouched on `fake`; a failing `list_emotions` degrades to `Robot: emotions unavailable` with the plain docstring and an empty addendum; `synthesizer_error` (or no `tts` block) yields the `TTS:` line while the voice stays wired; `exit()` stops the bridge (`running` reads `False`).
- **`say`:** WICA reads the voice as a streaming output Command; a tag's pieces reach `bridge.say_stream` and `say` returns `"OK"`; the first words reach the bridge before the tag closes and `say` returns only after it; an empty, `""`, whitespace-only or punctuation-only tag returns the empty-text error without calling it; an exception maps to `"TTS error: …"`.
- **`RobotMicSource`:** `start()` returns a queue receiving the bridge's chunks in order; `stop()` cancels the pump; a raising or ending stream sets `capture_error`, a restart clears it; undrained, only the newest `maxsize` chunks are kept; a second `start()` without `stop()` leaves one stream open.
- **`RobotCameraFeed`:** over a fake bridge carrying a fake `camera` (a settable frame published under an advancing `frame_id`, `start`/`stop` calls recorded): `latest()` is the bridge's frame object itself and `published_count` its count; `open()` / `close()` call neither `start()` nor `stop()` on the bridge's feed; `capture_error` is `None`; `camera(fps=…)` on an entered embodiment returns one over its bridge. Through `GestureInput` with the scripted detector/classifier, a sign on the fake feed's frame is delivered — the robot path end to end, no `StreamProvider` involved.
- **`UsersInput`:** over a real `ReachyMiniBridge` on the `fake` backend, its detector a scripted `FaceDetector` registered through `face_detection.face_detector` (`custom`) — faces shown and hidden as `PixelFace` boxes on the fake's frames, so the bridge's own confirmation, tracks, departures, pin and personal space run unmodified: the four entries and which wake; a face shown → one `user_event` naming its number, side and distance, and `users_in_view` listing it; the face hidden, nobody following it → no event, `users_in_view` empty once the bridge drops it; a user replacing another nobody follows → only the arrival is an event; a face at start-up → the start-up event; two faces at once → one write carrying both lines; detection stopping → no event, `users_in_view` omitted; `tracked_user` through the pin, the automatic target and tracking off; `personal_space` omitted with tracking off, nobody once tracking starts, the followed user inside when their face fills the frame and nobody again when it shrinks — the World woken by neither; which events wake, through a real World: an arrival with nobody tracked wakes, a second arrival while the head follows the first and a user not followed leaving are no event at all (only `users_in_view` changes), the followed (or pinned) user leaving does, with the following wording; no write for a face that merely moves; `stop` ending the watchers. The wordings, `personal_space` and `user_event_wakes` on their own, as pure functions.
- **The page view:** over the `fake` bridge with a scripted face: the frame is the fake's camera frame, mirrored, with the face's square in green where the mirrored face is and its `#<id>` label, read the right way round; the users table lists that user, `●` once the head follows them and the head line `follows #<id>, pinned` after a pin; `_Nobody present._` before the face; `draw_faces` marking a face's pixels, the followed one thicker, and stamping a label below its square at the frame's top edge, as a pure function.
- **Enter with a detector:** detection turned on when the config left it off; `inputs()` the users input and `commands()` the five actions with tracking; `prompt_addendum` carrying the users section; with no detector, none of the three.
- **`RobotControl`:** `play_emotion(<known>)` records the move; `<unknown>` returns a non-raising failure naming the valid names; with motors disabled the motor-precondition message (pinned on the `fake`, which honours it — the sim daemon ignores motor-state changes); `get_motors_state()` reflects `set_motors_state(...)`; `commands()` yields no `list_emotions`, and the tracking pair only with `tracking=True`; over the `fake` bridge with a scripted face, `start_tracking(<present>)` pins it (the head tracking report's `pinned_track_id`), `start_tracking()` returns to the automatic target, `start_tracking(<gone>)` answers the not-in-view reply without raising, `stop_tracking()` turns tracking off.

A live test (the `sim` or a real robot actually moving and speaking) belongs in `tests-e2e/`.

## Open questions

1. **Enforcing the no-collision rule.** If the model keeps issuing `say` + `play_emotion` together, the enforcement is a shared `asyncio.Lock` held by `say` and `play_emotion` (the two speaker/head-spanning verbs) so the second waits for the first, both still `running` in the World. Deferred until observed.
2. **Compact-intent → emotion resolution.** The bridge keeps `play_emotion(name)` literal; friendlier intent mapping ("be happy" → a curated move) stays out.
3. **Motors: agent-facing or lifecycle.** `set_motors_state`/`get_motors_state` stay exposed, with the agent told to assume them enabled and check only on failure; whether real hardware honours motor-state changes, and whether the *lifecycle* should enable them at `enter()` (making the assumption true by construction rather than by convention), is deferred to hardware validation.
4. **Robot state in the UI.** Connection / motor state on a panel of the embodiment's own ([embodiment.md](embodiment.md), open question 1).
5. ~~**Faces as an agent input.**~~ Answered on 2026-11-13 by "Users: `UsersInput`", over the bridge's users present (`bridge.users`), which its count-based face report could not be: a person replacing another was invisible to it, and a move of the robot's own read as a departure. The study is in `analysis/` (local).
6. **A busy room.** Partly answered on 2026-11-18: only the tracked user leaving and an arrival with nobody tracked wake the agent, so once it follows someone the comings and goings around them are silent. With nobody followed every arrival still wakes it; a distance gate on those (the face's `distance_m`), if observed.
7. **People on the page.** Who is present, followed and very close, as a line on the ASR + Vision panel, needs the per-embodiment UI member ([embodiment.md](embodiment.md), open question 1). Deferred: the World panel already shows both entries.
8. **Recognition.** With a recognising detector, the bridge's `User.identity` and `UsersReport.recognised` would let the agent greet someone by name and keep them across a broken track; the wording then may say who, where today it only says *a person*.
