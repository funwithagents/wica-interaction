---
code:
  - src/wica_interaction/gesture_input.py
  - src/wica_interaction/system.py
tests:
  - tests/test_gesture_input.py
  - tests/test_system.py
  - tests-e2e/test_gesture_input_live.py
---

# Gesture input

**Status:** Implemented

## Purpose

The **vision-in** half of the app — the first perception besides speech. The embodiment's camera — the robot's own, or a webcam on the computer — is run through the `vision-modules` hand pipeline (hand detection + HaGRID gesture classification, up to `max_hands` hands per frame), and each hand's gesture is turned into a WICA Input: a small, app-owned **hand-sign vocabulary** — `stop`, `like`, `dislike`, `ok`, and the counts `one` … `five` — one World entry **per hand**. Showing a sign wakes the agent, which decides for itself whether (and how) it matters in the current interaction; only `stop` carries an explicit persona rule (stop whatever it is doing). No hand, an unconfident classification, or a HaGRID class outside the vocabulary is "no sign" — and a value has to **hold** for `hold_s` (default 0.5 s) before it counts, so a one-frame flicker of the classifier never reaches the agent.

`vision-modules` *perceives* and this module *decides*: the library reports a label per frame with a score; this module owns the threshold, the label → sign reduction, the per-hand hold filter and change detection, and the rule for which changes wake the agent. It is the one place that knows how a stream of per-frame classifications maps onto "the person is showing a sign", so the rest of the app (the WICA agent, the UI) never touches the vision pipeline directly.

Two consumers read from it, as for speech ([asr-bridge.md](asr-bridge.md)):

- **The WICA World** — the sign a hand just showed is its `hand_gesture_<n>` entry: a hand *showing a new sign* triggers a reasoning step, and the entry is a one-shot observation — cleared by the app's TTL a second later and omitted from the prompt while it holds nothing ([app.md](app.md), "Inputs are transient").
- **The Gradio UI** — each published hand's current label/confidence and the pipeline's health are displayed (see [app.md](app.md), "Vision line").

## Decided

### Frames come from the embodiment

The video source is whatever camera the embodiment looks through ([embodiment.md](embodiment.md), `camera(fps=…)`): the robot's camera through the bridge's camera feed, `bridge.camera`, on the Reachy Mini ([reachy-embodiment.md](reachy-embodiment.md), `RobotCameraFeed`), a local webcam through `vision-modules`' `OpenCVSource` on the computer ([computer-embodiment.md](computer-embodiment.md), `WebcamSource` behind a `StreamFeed`). `GestureInput` takes the feed, it never builds one. What it requires of a feed is a **latest-value frame slot** — the shape the library's stages sample (`Upstream[FrameLike]`: a `latest()` returning items carrying `frame_id`, `ts` and a BGR `image`) — plus its lifecycle and its health, the module's `CameraFeed`:

```python
class CameraFeed(Protocol):
    """A latest-value frame slot the hand stage samples, with its lifecycle and health."""

    def open(
        self,
    ) -> None: ...  # GestureInput.start(): begin publishing (or nothing to do)
    def close(
        self,
    ) -> None: ...  # GestureInput.stop(): stop publishing (or nothing to do)
    def latest(self) -> FrameLike | None: ...  # the newest frame; None before the first
    @property
    def published_count(
        self,
    ) -> int: ...  # frames published, ever: the achieved-rate counter
    @property
    def capture_error(
        self,
    ) -> (
        str | None
    ): ...  # the feed's own failure; None while healthy, or when it has none to report
```

**Why a slot and not a `FrameSource`: the robot already has one.** The bridge reads its camera in exactly one place — `bridge.camera`, a feed thread publishing the newest `CameraFrame` (`frame_id`, `ts`, `image`, `head_pose`) for any number of consumers to sample at their own rate, because upstream's one-shot `get_frame()` hands each frame out once and two readers steal frames from each other, silently (`reachy-mini-bridge/specs/vision/camera.md`). The bridge's own face-detection loop samples that same feed. Re-reading it through a `StreamProvider` would add a second thread, a second `frame_id` numbering and a copy of every frame for nothing — and the library's `FrameLike` protocol exists precisely so a host's own slot plugs into `HandStage` as it stands (`vision-modules/specs/stream.md`, "`FrameLike`"). The webcam has no such slot, so the computer's source goes through a `StreamProvider`, wrapped as a `StreamFeed` (below), and both bodies present the same shape here.

Two rules every feed obeys, so the pipeline behaves the same over any camera:

- **The feed's lifecycle is the embodiment's; `open()` / `close()` are the module's only calls into it.** `GestureInput.start()` calls `open()` before the stages start, `stop()` calls `close()` after they are joined. A feed the embodiment owns end to end (the robot's, alive for the whole session) makes both no-ops; a feed built for this module (the webcam's provider) starts and stops its capture thread there. The module never touches anything else on the feed — in particular it never starts or stops the bridge's `camera` feed (`bridge.camera`), whose `stop()` would blind the bridge's own face detection; the protocol's vocabulary (`open`/`close`, not `start`/`stop`) keeps the bridge's feed from satisfying it by accident, so an adapter is always in between.
- **A missing frame is visible, not fatal — and never the end.** A stage samples `latest()` and simply finds no new `frame_id` while nothing arrives, so a camera that goes quiet (a daemon before its first frame, the headless sim's absent camera, an unplugged webcam) never ends the graph. Health has two sources: the feed's own `capture_error` — what only it can know (a webcam that cannot be opened or read, retried at its pace and cleared on success) — and the module's **stall** detection: `published_count` not advancing for `stall_s` seconds (`3.0` by default) reads `no camera frame for 3 s`, cleared the moment a frame lands. `status()["capture_error"]` is the feed's error when it has one, else the stall line, else `None` — so the Vision line says *why* frames stopped whatever the camera, and the robot's feed, which has no error of its own to report, still shows a dead camera.

The configured `fps` paces what the module can pace: the hand stage and the classifier (their `target_fps`), and — through the embodiment — a webcam source (a webcam delivering 30 fps into a 5 fps hand stage would be wasted captures). The robot's feed runs at the daemon's own rate (10 fps on a local daemon) whatever `fps` says; the stages sample it at theirs.

No mirroring: the classifier sees the frame as captured (`StreamFeed` builds its provider with `mirror=False`).

### `StreamFeed` — a `FrameSource` as a feed

For a camera that is a `FrameSource` rather than a slot (the webcam, the tests' scripted source), the module supplies the adapter: `StreamFeed(source)` owns a `StreamProvider(source, mirror=False)`; `open()` / `close()` are the provider's `start()` / `stop()` (which open and close the source), `latest()` and `published_count` the provider's, `capture_error` the source's. What it requires of the source is the library's `FrameSource` plus that one accessor, the module's `CameraSource`:

```python
class CameraSource(FrameSource, Protocol):
    """A FrameSource that paces itself to the configured fps and reports its own health."""

    @property
    def capture_error(
        self,
    ) -> str | None: ...  # why the last read failed; None while healthy
```

Such a source obeys three rules: **it paces itself** to `fps` (the provider's capture loop calls `read()` back-to-back); **a missing frame is not the end of the stream** — the provider ends its loop for good on a `None` from `read()`, so "nothing yet" waits one interval and tries again, and only `close()` makes `read()` return `None`; **a failing read is visible, not fatal** — logged once per transition, recorded in `capture_error`, retried at the same pace, cleared by a later success. `WebcamSource` is the one implementation ([computer-embodiment.md](computer-embodiment.md)).

### The pipeline

The hand demo's graph with the embodiment's feed in the provider's place and one consumer:

```
CameraFeed ──────────────────────▶ HandStage ─▶ GestureClassifier ─▶ watcher ─▶ on_gesture(hand, sign)
 (robot: bridge.camera, daemon rate;      (fps)      (fps, threshold)     (fps)
  computer: StreamFeed over the webcam, paced at fps)
```

- `HandStage(feed, target_fps=fps, max_hands=max_hands, detector=…)` — the feed is the stage's `Upstream[FrameLike]` directly, no provider of this module's in between; `max_hands` from the config ([configuration.md](configuration.md), `vision.max_hands`, default **2**, the library's own default), default padding, rank (`"area"`) and hysteresis; the shipped MediaPipe detector unless one is injected. The app always passes `max_hands` explicitly, so a change of the library's default never silently changes what the camera looks at.
  - **Why 2 by default.** `max_hands` is also MediaPipe's tracking capacity (`vision-modules/specs/hand.md`, "One count"): with 1, the detector locks onto the first hand it sees and a second hand is not even considered until the first is lost — so a person whose relaxed hand was tracked first could sign with the other and never be seen. With 2, both hands of the person are detected, published and classified — and each gets its own World entry (below), so `max_hands` is also the number of `hand_gesture_<n>` entries. The price is up to one more ViT pass per frame while two hands are in view; if that starves the classifier on a slow machine (the Vision line's achieved rate shows it), `max_hands: 1` is the fallback.
- `GestureClassifier(hands, target_fps=fps, threshold=threshold, device=device, classifier=…)` — the shipped HaGRID ViT unless one is injected. `threshold` is the library's own validation rule: a hand's `label` is its top class **only when its score reaches the threshold**, else `None`. The app's default is **0.55** ([configuration.md](configuration.md), `vision.threshold`), and the module adds no second threshold of its own.
- **Models load lazily**, on each stage's worker thread, at the first frame (that is the library's rule — see `vision-modules/specs/pipeline.md`). The first run downloads the MediaPipe bundle and the ~340 MB ViT; until they are ready the classifier publishes nothing and the status shows no label. A load failure (no network on first run, a missing extra) is recorded by the library in the stage's `last_error` and the graph keeps running; this module surfaces it in `status()["error"]` — vision never crashes the app.
- **Nothing here touches the World or WICA.** The `on_gesture` callback is injected, like `AsrBridge`'s `on_final_segment`, so the module is unit-testable with a recording callback, a scripted `CameraSource`, and scripted detector/classifier seams (no camera, no model).

### The reduction: label → sign, per hand

The watcher thread samples `gestures.latest()` at `fps`, once per new `frame_id`, and reduces **each hand slot** `n` (1 … `max_hands`) to one value through a hardcoded table, `SIGNS` (HaGRID class → sign):

| HaGRID label(s) | Sign |
|---|---|
| `fist`, `stop`, `stop_inverted` | `"stop"` |
| `like` | `"like"` |
| `dislike` | `"dislike"` |
| `ok` | `"ok"` |
| `one` | `"one"` |
| `two_up`, `two_up_inverted`, `peace`, `peace_inverted`, `rock` | `"two"` |
| `three`, `three2` | `"three"` |
| `four` | `"four"` |
| `palm` | `"five"` |

16 of the model's 18 classes are mapped; `call` and `mute` are not (`mute` was in the vocabulary until 2026-09-24 and dropped after use — see open question 1), and neither is anything else the classifier might report. For slot `n`:

| Classifier result | Value of hand `n` |
|---|---|
| fewer than `n` hands published (no hand in frame, or only `n-1`) | `None` |
| `hands[n-1].label` is `None` (top score below threshold, or no crop) | `None` |
| `hands[n-1].label` is a class outside `SIGNS` (`call`, `mute`) | `None` |
| `hands[n-1].label` is in `SIGNS` | `SIGNS[label]` |

The signs are the **app's** vocabulary, not HaGRID's: several classes that mean the same thing to a person collapse to one value (a fist or a flat hand are both `stop`; `peace`, `rock`, `two_up` all show two fingers), and the counts are named by fingers (`palm` → `five`). The mapping is a module constant for now (not a config knob) — the prompt and the persona depend on it.

**Hands are slots, not identities.** Hand `n` is the library's `hands[n-1]`, which stays the same physical hand while it stays in view (`vision-modules/specs/hand.md`, "slot stability"). But the published tuple is **compacted**: when hand 1 leaves while hand 2 stays, the remaining hand becomes `hands[0]`. That shows up as hand 1 taking hand 2's sign (a change, so it triggers) and hand 2 going to `None`. Accepted for now — the agent is told the numbers are not left/right (see "What the agent does with it"); stable identity is open question 5.

**No cross-hand logic here.** Each hand is reported on its own: two hands showing `three` and `two` are two entries, not a `five`. Whether to combine them (a count on two hands, a sign on each) is left to the agent, deliberately not explained in the prompt.

### The hold filter: a value must be visible for `hold_s`

A per-frame label flickers: between neighbouring classes (`stop` / `five`, `two` / `three`), or across the threshold (a sign → `None` → the same sign), and with change detection alone every flicker would wake the agent — `vision-modules/specs/gesture_classifier.md`, open question 4. So the watcher does not deliver a hand's reduced value the moment it is seen: it becomes a **candidate**, and it is delivered only once it has been the hand's value on **every** result for at least `hold_s` seconds — **consecutive**, wall-clock, per hand:

- A hand's value differing from its last *delivered* value starts (or continues) a candidate: the first result showing it stamps the time; each later result showing the same value checks `now - since >= hold_s` and delivers on the first result that satisfies it. A result showing a *different* value restarts the candidate from scratch — one dissenting frame resets the clock (a strict, not a majority, rule). A result showing the delivered value again drops the candidate.
- **It applies to every transition**, `None` included: a hand going away is delivered `hold_s` after it is last seen, so a one-frame dip below the threshold never produces `two → None → two` (which would be a second trigger). `stop` gets the same delay, deliberately — the coalescing window adds up to ~200 ms on top, so a stop acts about 0.7 s after it is shown; revisit if that is felt.
- The clock is read when a result is *observed*, so a stalled classifier (a model still loading) never validates anything: "visible for 0.5 s" means seen on results spanning 0.5 s. At the default 5 fps that is three or four consecutive frames.
- `hold_s = 0` disables the filter — the first result showing a value delivers it (what the fast tests use). It is a config knob, `vision.hold_s`, default **0.5** ([configuration.md](configuration.md)), because the right value depends on the camera, the machine and the person; `status()["hold_s"]` echoes it.
- The filter is a small pure component, `HoldFilter` — one per hand slot, `observe(value, now) -> bool` returning whether `value` just became the delivered one (`value` is then the filter's `.value`) — so the rule is unit-testable without threads or a clock.

### Change detection and the trigger rule (the core decision)

Two rules, at two layers:

1. **The module delivers changes only, per hand.** `on_gesture(hand, value)` is called for hand `n` when its (held) reduced value differs from the last one delivered **for that hand** — including a change to `None` — never on every frame (the classifier runs at 5 fps; the World must not be churned at that rate with an unchanged value, which would also spam the World panel). Every hand's last-delivered value starts at `None`, matching the entries' initial `None`, so the first sign-less frames deliver nothing. A frame that changes both hands delivers twice (hand 1 then hand 2). The app wires `on_gesture` to `world.update(f"hand_gesture_{hand}", value)`.
2. **The World triggers on a sign being shown, never on it going away.** The app registers one entry per hand, each with the same trigger predicate:

   ```python
   def sign_shown(old: str | None, new: str | None) -> bool:
       return new is not None and new != old


   for n in range(1, max_hands + 1):
       wica.world.register(
           f"hand_gesture_{n}",
           str,
           serialize_fn=hand_gesture_line(n),
           triggers_llm_call=True,
           trigger_condition_fn=sign_shown,
       )
   ```

   So `None → two` wakes the agent, and so does `two → three` (a direct switch, no sign-less frame between); `three → None` updates the entry silently; `two → two` never reaches the World at all (rule 1), and even if it did the predicate would not fire. The app also registers each entry with a **TTL** (`HAND_GESTURE_TTL`, 1 s — [app.md](app.md), "Inputs are transient"): the World clears a sign that long after it lands, a reset that never triggers. Since deliveries are change-only, a sign the hand keeps holding is not refreshed — it reads as no sign once expired, until the hand changes — and the `old` value the predicate sees is usually already `None` when a new sign arrives.

   **Coalescing is not bypassed** (no `bypass_coalescing`, unlike the fist-only version): signs ride WICA's ~200 ms coalescing window like every other Input (`wica-framework/specs/inputs.md`). That is what folds two hands changing on the same frame — or a speech segment closing alongside a sign — into **one** reasoning step instead of two. The price is up to ~200 ms before a `stop` acts; revisit if that is felt in use.

### In the prompt: a one-shot event, then nothing

A `hand_gesture_<n>` entry is in the prompt **only while it holds a sign** — in practice the step the sign triggered, and any step that starts within the TTL:

| Value of hand `n` | Rendered |
|---|---|
| `None` | nothing — an empty `Content`, so WICA omits the entry, envelope included |
| a sign | `Hand gesture detected on hand {n}: "{sign}".` — a detection, not something the person is said to do: the camera cannot tell whose hand it is |

`None` is what an entry holds before any sign, once the hand drops or relaxes (rule 1 delivers that change), and once the TTL clears a sign; in every case the prompt simply carries no line for that hand, rather than a "not showing a sign" placeholder — the agent is never left believing a sign is still up, and a sign-less hand costs no tokens. The step's own history keeps the sign it observed, so the agent still sees what was shown in earlier turns, as past observations. The person-facing counterpart (`display_entry` in [app.md](app.md)) shows `✋ hand {n}: {sign}` / `✋ hand {n}: no sign` (the latter only labels a reaction whose trigger carried a sign-less hand, which the trigger rule never produces in practice).

### What the agent does with it — the persona, seeded at startup

The "Hand signs" section of the persona is **not** written in either prompt file: this module owns it as `hand_signs_prompt_section()`, and the app appends it to the resolved persona when — and only when — vision is enabled ([app.md](app.md), build step 3), the same seam the embodiment's `prompt_addendum` uses. A persona never describes hands the agent cannot see, and the vocabulary lives in one place next to the `SIGNS` table it must match.

The section **describes the perception, not the reactions**: a camera detects hand gestures; when one is detected on hand `n` a `hand_gesture_<n>` state appears naming it — one of `stop`, `like`, `dislike`, `ok`, `one`, `two`, `three`, `four`, `five` — and is gone a moment later, so it says a gesture was just detected, not that it is still held, and no state means none just now; hand numbers follow the hands detected in the order they appeared and are not left/right; the camera cannot tell whose hand it is. It does **not** say what `like`, `ok`, a count, or two hands at once mean, nor whether to respond: the agent decides from the interaction context whether a sign is meant for it and what to make of it. That is deliberate — the point is to see how the agent interprets the signs unprompted.

**`stop` is the one exception**: when `stop` is detected on any hand, stop what you are doing — cancel every running Command (`cancel_command` on each in-flight `say` / action / whatever runs) and start nothing new; then, in the next turn, once the cancellation is observed done, `say` a short confirmation (a stop is a request like any other and gets a spoken reply); if nothing was running, just acknowledge briefly. Do not issue the cancel and the confirmation in the same turn (the robot persona's no-collision rule — [reachy-embodiment.md](reachy-embodiment.md); harmless on the computer). The `stop` state disappearing is not a request for anything.

**A known limit** (WICA's, not this module's): the agent handles one reasoning step at a time and a trigger arriving mid-step is dropped, not queued. A running `say` is a Command, not a step, so a sign shown *while the agent speaks* does wake the agent — the common case. A sign shown while the model is still reasoning is lost; the person lowers and shows it again (or shows another), and the next change triggers. With a whole vocabulary this happens more often than with the fist alone; barge-in proper is deferred in WICA.

### `GestureInput`

```python
OnGesture = Callable[[int, str | None], None]  # (hand number 1…max_hands, sign or None)


@dataclass(frozen=True)
class GestureModels:
    """Injectable perception backends (tests); None → the library's shipped models."""

    detector: HandDetector | None = None
    classifier: ImageClassifier | None = None


def hand_signs_prompt_section() -> (
    str
): ...  # the "## Hand signs" persona section (above)


class GestureInput:
    def __init__(
        self,
        feed: CameraFeed,
        *,
        on_gesture: OnGesture,
        threshold: float = 0.55,
        fps: float = 5.0,
        max_hands: int = 2,
        hold_s: float = 0.5,
        device: str | None = None,
        models: GestureModels | None = None,
        stall_s: float = 3.0,
    ) -> None: ...

    def start(
        self,
    ) -> (
        None
    ): ...  # open the feed, start the stages (upstream-first) and the watcher thread
    def stop(
        self,
    ) -> None: ...  # stop the watcher, then the stages (downstream-first), then close the feed; joins
    @property
    def running(self) -> bool: ...
    def save_detector_snapshot(
        self, folder, now=None
    ) -> str: ...  # the frame the hand stage last detected on
    def save_classifier_snapshot(
        self, folder, now=None
    ) -> str: ...  # the crop(s) the classifier last classified
    def status(
        self,
    ) -> dict: ...  # {"running", "signs", "hands", "max_hands", "hold_s", "target_fps", "stream_fps", "hand_fps", "classifier_fps", "capture_error", "error"}
```

- `start()` / `stop()` are **synchronous** (threads, like the library's `Pipeline`), called from the app's main/Gradio thread, never from the loop. `start()` calls `feed.open()` and then starts the two stages (a `Pipeline([hands, gestures])`, upstream-first); `stop()` joins the watcher, stops the stages (downstream-first) and then calls `feed.close()`. `stop()` joins every thread; the stages' `close()` hooks release the models. Both are idempotent.
- `status()` is a cheap read the UI timer polls: `signs` is the last *delivered* value of every hand — past the hold filter — a list of length `max_hands` (`["two", None]`), `hands` one `{"label", "confidence"}` per hand in the latest classifier result, **in the library's slot order** (so entry *i* stays the same physical hand while it is in view) — each hand's validated top class (`None` below threshold or with no crop) and raw top score (`0.0` with no crop); an empty list with no hand or no result yet — `max_hands` the configured cap, `hold_s` the configured hold, `target_fps` the configured rate, `stream_fps` / `hand_fps` / `classifier_fps` each node's **achieved** rate (below), `capture_error` the feed's own error or else the stall line (above), and `error` the message of the last exception a stage recorded (`hands.last_error` or `gestures.last_error`, the classifier's first), `None` when healthy.
- **Achieved rates, the hand demo's way.** A stage's real rate is capped by its upstream and by the model's speed, so `target_fps` is a ceiling, not a fact. Every node's `published_count` increments exactly once per publish — the two stages' and the feed's alike (the bridge's `CameraFeed.published_count`, the `StreamProvider`'s; the library's exact counter for this — `frame_id` would not do, it is inherited from the camera frame) — so the watcher samples the three counters once per window (`fps_window_s`, default 1 s) and keeps (Δcount / Δtime) as that node's rate; `None` until two samples exist. The stream's rate is the rate the camera actually delivered frames at: the daemon's on the robot (10 fps on a local daemon, whatever the configured `fps`), the paced webcam's on the computer. The UI shows the camera's rate on its own and each stage's next to the configured rate, so a camera returning nothing, or a classifier starved by a slow model, is visible at a glance. The watcher also keeps the time `published_count` last advanced, for the stall line.
- `max_hands` must be `>= 1` (the library's `HandStage` raises `ValueError` otherwise; the config validates it first). `hold_s` must be `>= 0` (the config validates it too).
- `device` is forwarded to the classifier: `None` lets the library pick (`mps` → `cuda` → `cpu`).
- **Snapshots, the hand demo's way.** `save_detector_snapshot(folder)` / `save_classifier_snapshot(folder)` write what the hand stage / the classifier **last consumed** — the full camera frame it detected on, the hand crop(s) it classified — through the library's `save_input(stage, path)` (`vision-modules/specs/snapshot.md` — it accepts any `FrameLike`, the bridge's `CameraFrame` included; its writer is OpenCV's, the library's `opencv` extra, which the app's `vision` extra pulls — [project.md](project.md)), under `<folder>/snapshot_<stage name>_<YYYYMMDDHHMMSS>.jpg` (`snapshot_path`; the classifier's crops get `_<i>` before the suffix, one per hand). They run on the **caller's** thread (the Gradio click handler), as the library requires — never on a worker — and **never raise**: a `ValueError` (nothing consumed yet, an unsupported suffix), `TypeError`, or `OSError` (an unwritable folder) comes back as a `⚠️ <stage>: …` status line, a classifier input with no hand as `⚠️ …: no crop to save`, and a success as `Saved \`<path>\`, …`. The UI shows that line under the buttons (see [app.md](app.md), "Vision line").

### App integration

See [app.md](app.md) for the composition; the rules this module imposes on it:

- Built in `build_system` **only when** the config's `vision` section has `enabled: true` ([configuration.md](configuration.md)) **and** the embodiment is up (no body ⇒ no camera ⇒ no vision, like ASR and the mic) **and** the agent built. Built **after** WICA, over `embodiment.camera(fps=cfg.vision.fps)` with `hold_s=cfg.vision.hold_s`, so `on_gesture` resolves against a live agent; the `hand_gesture_1` … `hand_gesture_<max_hands>` entries are registered — and `hand_signs_prompt_section()` appended to the persona — only in that case, so a vision-less run tells the model nothing about hands. Construction never loads a model or opens the camera (both happen on `start()`); a build failure is still caught into a `Vision: …` banner line rather than crashing.
- **MediaPipe is imported before the body is entered.** `build_system` calls `preload_mediapipe()` first when vision is on and the hand detector is the shipped one (an injected detector needs none). Left to the hand stage, MediaPipe would be imported on first use, once the camera runs; on Linux, after the robot's GStreamer media has loaded the system's native libraries, that import aborted the process inside matplotlib's FreeType extension, which MediaPipe pulls in (found by the end-to-end tier's robot job, 2026-11-10). Imported before the body, it loads cleanly. A missing MediaPipe is left to the stage, which reports it as its error.
- `on_gesture(hand, value)` writes `world.update(f"hand_gesture_{hand}", value)` guarded on a running agent (as `_deliver_speech` is): a value arriving before start or after teardown is dropped.
- Started after `wica.start()` (with the ASR bridge), stopped in `_shutdown` right after `wica.close()` and **before** the embodiment's `exit()` — the robot's feed is the session's and resets to `None` at exit, so the stages are joined while it is still live; the webcam's provider is joined before anything else is torn down. Its `stop()` is a synchronous join, isolated like every other teardown step.

## Testing

Drive `GestureInput` end-to-end through the real `vision-modules` pipeline with a scripted `CameraFeed` (a settable synthetic frame republished under a new `frame_id`, an injectable `capture_error`, `open`/`close` recorded) and a scripted `HandDetector` / `ImageClassifier` (no camera, no model, no network), at a high `fps` so the tests run in well under a second:

- Frames with a hand the classifier scores `like` at ≥ threshold deliver `(1, "like")` exactly once, even across many frames.
- Then frames with no hand deliver `(1, None)` once; the hand back at `like` delivers `(1, "like")` again (`None → like → None → like` ⇒ three deliveries).
- The table: every mapped label reduces to its sign (e.g. `fist` and `stop_inverted` → `"stop"`, `rock` → `"two"`, `three2` → `"three"`, `palm` → `"five"`), and a switch between two signs with no sign-less frame (`one → two`) delivers the new sign directly. Two labels mapping to the same sign (`peace → rock`) deliver nothing new.
- An unconfident classification (score below the threshold) and an unmapped class (`call`, `mute`) both deliver nothing (stay `None`).
- **The hold filter**, on `HoldFilter` alone with explicit times: a value observed once at `hold_s > 0` is not delivered; the same value observed again at `+hold_s` is, once; a dissenting observation in between restarts the clock from the dissent, not from the first sighting; a one-frame dip to `None` between two `two`s delivers nothing (the candidate is dropped when the delivered value returns); a `x → None` transition waits the same hold; `hold_s = 0` delivers on the first observation. Then through `GestureInput` with a real clock: at a short hold, a sign is delivered once after at least `hold_s` and not before; a classifier flipping between two signs faster than the hold delivers neither, and settling on one delivers it.
- `status()` reports each hand's raw `label` / `confidence` even when the value is `None` (a sub-threshold `like` shows `hands == [{"label": None, "confidence": 0.5}]`), and `signs` has one entry per `max_hands`.
- **Two hands** (a scripted detector returning two boxes, a classifier scoring each crop by its position): a sign on the *second* hand only delivers `(2, sign)` and nothing for hand 1; different signs on both hands deliver one change each; hand 2 relaxing delivers `(2, None)` only; `hands` lists both in slot order. With `max_hands=1` the same frame publishes one hand and only hand 1 is ever delivered.
- `sign_shown`: `None → x` and `x → y` fire; `x → None` and `x → x` do not.
- With a short `fps_window_s`, `stream_fps` / `hand_fps` / `classifier_fps` go from `None` to a positive rate once frames flow, and `target_fps` is the configured rate.
- Snapshots: before any frame both save methods return a `⚠️` line and write nothing; once frames flow, the detector snapshot writes `snapshot_HandStage_<ts>.jpg` (the full frame) and the classifier snapshot `snapshot_GestureClassifier_<ts>_0.jpg` (the crop), each reporting `Saved …`; with no hand in frame the classifier snapshot reports `no crop to save`; an unwritable folder reports the `OSError` instead of raising.
- `hand_signs_prompt_section()` names every sign of the vocabulary (exactly the values of `SIGNS`), does not name `mute`, states the `stop` rule, and describes a sign state as what the person just did (never promising a "showing none" state).
- **Health:** with a feed that stops advancing, `status()["capture_error"]` is `None` until `stall_s` has passed and the stall line after; a frame landing clears it; a feed reporting its own `capture_error` shows that message instead of the stall line; `open()` is called once by `start()` and `close()` once by `stop()`, and nothing else on the feed.
- **`StreamFeed`** over a scripted `CameraSource`: `open()` opens the source and starts publishing (`latest()` a `Frame` of the source's image, `published_count` counting); `close()` closes the source and stops the thread; `capture_error` is the source's; a source returning nothing yet never ends the provider.
- The embodiments' feeds are tested with them: `RobotCameraFeed` in [reachy-embodiment.md](reachy-embodiment.md), `WebcamSource` in [computer-embodiment.md](computer-embodiment.md).
- `stop()` joins the threads and the callback is not called afterwards.
- In `tests/test_system.py`, with a `FakeEmbodiment` whose camera is a scripted feed of synthetic frames + `GestureModels`: `max_hands` entries `hand_gesture_1…n` are registered, `hold_s` reaches the gesture input, and the hand-signs section is in the first prompt; a `None → two` update on `hand_gesture_2` triggers a reasoning step whose prompt renders the hand-2 sign line and no line for the sign-less hand 1; `HAND_GESTURE_TTL` later the World has cleared the sign with no step and renders no hand entry; `two → None` does not trigger; the serializer renders `None` as an empty `Content`; a vision config with no embodiment builds no `GestureInput`; `enabled: false` builds none either (registers no entry, seeds no section); teardown stops vision before the embodiment exits.
- `tests-e2e/test_gesture_input_live.py` runs the **real** models, at the default hold (so it also proves the hold clears on a steady photo): a scripted source serving each of `vision-modules`' bundled photos must deliver its sign for hand 1 — `hand_fist.jpg` and `hand_stop.jpg` → `"stop"`, `hand_palm.jpg` → `"five"`, `hand_one.jpg` → `"one"`, `hand_three2.jpg` → `"three"` — and `no_hand.jpg` must deliver nothing — skipped when the fixtures or the models are unavailable (see [testing.md](testing.md)).

## Open questions

1. **Tuning the vocabulary.** The `SIGNS` table is hardcoded and chosen by hand (`call` left out; `rock` counted as two fingers). `mute` was the first sign dropped after use (2026-09-24). Once more of the agent's unprompted interpretations are observed on the robot, the table and the persona's one-line vocabulary may change again — or move to config if it needs per-deployment tuning.
2. **Debouncing.** **Resolved (2026-09-24):** the flicker was observed, and the hold filter above is the fix — time-based (`hold_s`) rather than a frame count, so it means the same thing whatever rate the classifier actually achieves. Still open within it: the strict consecutive rule may prove too strict on a flickery camera (a sign never validating); a majority-over-the-window rule is the fallback, and a sign-specific hold (a shorter one for `stop`) the other knob.
3. **Showing the camera in the UI.** Only a status line is shown; the frame with the hand box (as in the hand demo) would be a natural debug panel, at the cost of a per-tick image round-trip. Deferred.
4. **Vision without the robot.** **Resolved (2026-09-17):** the camera is the embodiment's, so the computer embodiment's `WebcamSource` runs the same pipeline over a laptop webcam ([computer-embodiment.md](computer-embodiment.md)); the bridge's `fake` backend still exercises the robot plumbing with its synthetic frame.
5. **Hand identity and handedness.** Entries are keyed by slot, and the library compacts slots when a hand leaves (see "Hands are slots, not identities"), so hand 2's sign can move to hand 1. Keying by `left` / `right` would need MediaPipe's handedness exposed on `Hand` (`vision-modules/specs/hand.md`, open question 1) and a stable identity across gaps (open question 3 there). Deferred until the slot shift is seen to confuse the agent.
6. **Coalescing for `stop`.** Signs no longer bypass the coalescing window, so `stop` waits up to ~200 ms. A separate `bypass_coalescing` entry for the stop sign alone is the fix if that latency is felt.
