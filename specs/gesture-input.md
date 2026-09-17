---
code:
  - src/reachy_mini_interaction_wica/gesture_input.py
  - src/reachy_mini_interaction_wica/app.py
  - prompts/assistant.md
tests:
  - tests/test_gesture_input.py
  - tests/test_app.py
  - tests-e2e/test_gesture_input_live.py
---

# Gesture input

**Status:** Implemented

## Purpose

The **vision-in** half of the app — the first perception besides speech. The robot's own camera is run through the [`vision-modules`](../../vision-modules/README.md) hand pipeline (hand detection + HaGRID gesture classification), and one gesture is turned into a WICA Input: a **raised fist**, the person's "stop" sign. Holding up a fist wakes the agent, which the persona tells to stop whatever it is doing. Every other gesture, and no hand at all, is "no fist".

`vision-modules` *perceives* and this module *decides*: the library reports a label per frame with a score; this module owns the threshold, the "fist or not" reduction, the change detection, and the rule for which changes wake the agent. It is the one place that knows how a stream of per-frame classifications maps onto "the person is showing the stop sign", so the rest of the app (the WICA agent, the UI) never touches the vision pipeline directly.

Two consumers read from it, as for speech ([asr-bridge.md](asr-bridge.md)):

- **The WICA World** — the current fist state is the `hand_gesture` entry the agent reads in every prompt, and a fist being *raised* triggers a reasoning step.
- **The Gradio UI** — the current label/confidence and the pipeline's health are displayed (see [app.md](app.md), "Vision line").

## Decided

### Frames come from the robot

The video source is the robot camera, read through the bridge's one perception verb, `ReachyMiniApi.get_camera_frame()` (a BGR `HxWx3` `uint8` array, or `None` when no frame is buffered yet — see [reachy-mini-bridge api.md](../../reachy-mini-bridge/specs/api.md)). Not the browser webcam the `vision-modules` hand demo uses, and not a local camera index: the robot is what looks at the person. No mirroring (`StreamProvider(..., mirror=False)`): the robot's view is not a selfie.

A small **`RobotCameraSource`** adapter implements the library's `FrameSource` protocol (`open()` on every provider start, `read() -> ndarray | None`, `close()` on every stop — the provider drives that lifecycle — and `fps()`, the nominal rate) over that verb:

```python
class RobotCameraSource:
    """Polls ReachyMiniApi.get_camera_frame() on the app loop, paced at `fps`, as a FrameSource."""

    def __init__(
        self, api: ReachyMiniApi, loop: asyncio.AbstractEventLoop, *, fps: float
    ) -> None: ...
    def open(
        self,
    ) -> (
        None
    ): ...  # (re)arm; nothing to open robot-side (the media session is the app's)
    def read(self) -> np.ndarray | None: ...  # next frame; None only once closed
    def close(self) -> None: ...
    def fps(
        self,
    ) -> float: ...  # nominal rate = the poll rate (the bridge reports no camera rate)
    @property
    def capture_error(
        self,
    ) -> str | None: ...  # why the last poll failed; None while healthy
```

- `read()` runs on the `StreamProvider`'s capture thread and hops to the app loop with `asyncio.run_coroutine_threadsafe(api.get_camera_frame(), loop).result(timeout)` — the verb is async and bound to the loop the api was entered on (see [app.md](app.md), "One event loop"). The bridge already runs the blocking SDK read off the loop (`asyncio.to_thread`), so the loop is not stalled.
- **It paces itself** to `fps` (sleeping the remainder of `1/fps` after each poll) because the provider's capture loop calls `read()` back-to-back and a camera poll faster than the hand stage consumes would be wasted work. Hand stage and classifier run at the same `fps`, so every polled frame is looked at once.
- **A `None` frame is not the end of the stream.** The provider ends its loop for good on a `None` from `read()`, so "nothing buffered yet" (the daemon before its first frame; the headless sim, which has no GL context) must *not* be reported as `None`: the source waits one interval and polls again. Only `close()` makes `read()` return `None`. The provider therefore never `ended`s on its own — the camera is live for as long as the app runs.
- **A failing poll is visible, not fatal.** If the verb raises, the source logs it (once per transition, not per poll), records the reason as `capture_error` (`"camera read failed: …"`), and keeps polling at the same pace; a later success clears it. A robot whose camera is missing therefore shows a warning in the UI rather than killing vision, and a transient failure heals.

### The pipeline

Exactly the hand demo's graph, with the robot as source and one consumer:

```
RobotCameraSource ─▶ StreamProvider ─▶ HandStage ─▶ GestureClassifier ─▶ watcher ─▶ on_gesture(value)
   (poll at fps)      (no mirror)       (fps)          (fps, threshold)     (fps)
```

- `HandStage(provider, target_fps=fps, detector=…)` — default `max_hands=1`, default padding; the shipped MediaPipe detector unless one is injected.
- `GestureClassifier(hands, target_fps=fps, threshold=threshold, device=device, classifier=…)` — the shipped HaGRID ViT unless one is injected. `threshold` is the library's own validation rule: a hand's `label` is its top class **only when its score reaches the threshold**, else `None`. The app's default is **0.55** ([configuration.md](configuration.md), `vision.threshold`), and the module adds no second threshold of its own.
- **Models load lazily**, on each stage's worker thread, at the first frame (that is the library's rule — see [vision-modules pipeline.md](../../vision-modules/specs/pipeline.md)). The first run downloads the MediaPipe bundle and the ~340 MB ViT; until they are ready the classifier publishes nothing and the status shows no label. A load failure (no network on first run, a missing extra) is recorded by the library in the stage's `last_error` and the graph keeps running; this module surfaces it in `status()["error"]` — vision never crashes the app.
- **Nothing here touches the World or WICA.** The `on_gesture` callback is injected, like `AsrBridge`'s `on_final_segment`, so the module is unit-testable with a recording callback, a fake api, and scripted detector/classifier seams (no camera, no model).

### The reduction: fist or nothing

The watcher thread samples `gestures.latest()` at `fps`, once per new `frame_id`, and reduces each `Gesture` result to one value:

| Classifier result (first hand) | Value |
|---|---|
| no hand in frame (`present=False`) | `None` |
| a hand whose `label` is `None` (top score below threshold) | `None` |
| a hand whose `label` is any class other than `fist` | `None` |
| a hand whose `label` is `"fist"` | `"fist"` |

Only the **first** hand counts (the detector's highest-scoring one, and `max_hands=1` anyway). Only `fist` is recognised for now: the other 17 HaGRID classes are deliberately folded into "no fist" rather than exposed, so the World holds a stop signal, not a gesture vocabulary (extending to more gestures is open question 1).

### Change detection and the trigger rule (the core decision)

Two rules, at two layers:

1. **The module delivers changes only.** `on_gesture(value)` is called when the reduced value differs from the last delivered one — `None → "fist"` and `"fist" → None` — never on every frame (the classifier runs at 5 fps; the World must not be churned at that rate with an unchanged value, which would also spam the World panel). The last-delivered value starts at `None`, matching the entry's initial `None`, so the first "no fist" frames deliver nothing. The app wires `on_gesture` to `world.update("hand_gesture", value)`.
2. **The World triggers on a raised fist only.** The app registers the entry with a trigger predicate, so the direction of the change is decided where WICA decides it:

   ```python
   FIST = "fist"


   def fist_raised(old: str | None, new: str | None) -> bool:
       return new == FIST and old != FIST


   wica.world.register(
       "hand_gesture",
       str,
       serialize_fn=_hand_gesture,
       triggers_llm_call=True,
       trigger_condition_fn=fist_raised,
       bypass_coalescing=True,
   )
   ```

   So `None → fist` wakes the agent; `fist → None` updates the entry silently (the agent sees the fist is down on its next step); `fist → fist` never reaches the World at all (rule 1), and even if it did the predicate would not fire. `bypass_coalescing=True` because a stop sign must act **now**, not after WICA's ~200 ms coalescing window (the exact case [wica inputs.md](../../wica-framework/specs/inputs.md) names for it).

### In the prompt: ambient state, not a one-shot event

`hand_gesture` is rendered in **every** prompt (`include_in_prompt` default), not only on the step it triggers, so the agent always knows whether the fist is still up:

| Value | Rendered |
|---|---|
| `None` | `The person is not holding up a fist.` |
| `"fist"` | `The person is holding up a fist — the stop sign.` |

The `None` rendering is what the entry reads once the hand drops (rule 1 delivers that change), so the agent is never left believing a fist is still raised. The person-facing counterpart (`display_entry` in [app.md](app.md)) shows a raised fist as `✊ fist raised` and a lowered one as `🖐️ fist lowered`.

### What the agent does with it — the persona

The stop rule is **prompt text**, in [prompts/assistant.md](../prompts/assistant.md), not a guard in code: this module reports the sign, the agent decides. The persona says, in a "Stop sign" section: a raised fist means *stop what you are doing* — cancel every running Command (`cancel_command` on each running `say` / `play_emotion` / whatever is in flight) and start nothing new; then, per the existing "always answer out loud" rule, **say a short confirmation in the next turn** once the cancellation is observed done (the person asked for a stop and gets a spoken acknowledgement, not a silent halt); and if nothing was running, just acknowledge briefly. It also says not to issue the cancel and the confirmation in the same turn (the no-collision rule, [app.md](app.md)) and that the fist being lowered is not a request for anything — do not react to `fist lowered` on its own.

**A known limit** (WICA's, not this module's): the agent handles one reasoning step at a time and a trigger arriving mid-step is dropped, not queued. A running `say` is a Command, not a step, so a fist raised *while the robot speaks* does wake the agent — the common case. A fist raised while the model is still reasoning is lost; the person holds the fist a moment longer, lowers and raises it again, and the next `None → fist` triggers. Barge-in proper is deferred in WICA.

### `GestureInput`

```python
OnGesture = Callable[[str | None], None]


@dataclass(frozen=True)
class GestureModels:
    """Injectable perception backends (tests); None → the library's shipped models."""

    detector: HandDetector | None = None
    classifier: ImageClassifier | None = None


class GestureInput:
    def __init__(
        self,
        api: ReachyMiniApi,
        loop: asyncio.AbstractEventLoop,
        *,
        on_gesture: OnGesture,
        threshold: float = 0.55,
        fps: float = 5.0,
        device: str | None = None,
        models: GestureModels | None = None,
    ) -> None: ...

    def start(
        self,
    ) -> None: ...  # start the pipeline (upstream-first) and the watcher thread
    def stop(
        self,
    ) -> None: ...  # stop the watcher, then the pipeline (downstream-first); joins
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
    ) -> dict: ...  # {"running", "gesture", "label", "confidence", "target_fps", "stream_fps", "hand_fps", "classifier_fps", "capture_error", "error"}
```

- `start()` / `stop()` are **synchronous** (threads, like the library's `Pipeline`), called from the app's main/Gradio thread, never from the loop. `stop()` joins every thread; `close()` hooks release the models. Both are idempotent.
- `status()` is a cheap read the UI timer polls: `gesture` is the last *delivered* value (`"fist"` / `None`), `label` / `confidence` the first hand's raw top class and score from the latest classifier result (`None` / `0.0` with no hand or no result yet), `target_fps` the configured rate, `stream_fps` / `hand_fps` / `classifier_fps` each node's **achieved** rate (below), `capture_error` the camera source's, and `error` the message of the last exception a stage recorded (`hands.last_error` or `gestures.last_error`, the classifier's first), `None` when healthy.
- **Achieved rates, the hand demo's way.** A stage's real rate is capped by its upstream and by the model's speed, so `target_fps` is a ceiling, not a fact. Every node's `published_count` increments exactly once per publish — the two stages' and the `StreamProvider`'s alike (the library's exact counter for this — `frame_id` would not do, it is inherited from the camera frame) — so the watcher samples the three counters once per window (`fps_window_s`, default 1 s) and keeps (Δcount / Δtime) as that node's rate; `None` until two samples exist. The stream's rate is the rate the robot camera actually delivered frames at (a `None` frame is not a publish), against the configured poll rate — the provider's `source_fps` would only echo `RobotCameraSource.fps()`, the poll rate, since the bridge reports no camera rate. The UI shows each next to its configured rate, so a camera returning nothing, or a classifier starved by a slow model, is visible at a glance.
- `device` is forwarded to the classifier: `None` lets the library pick (`mps` → `cuda` → `cpu`).
- **Snapshots, the hand demo's way.** `save_detector_snapshot(folder)` / `save_classifier_snapshot(folder)` write what the hand stage / the classifier **last consumed** — the full camera frame it detected on, the hand crop(s) it classified — through the library's `save_input(stage, path)` ([vision-modules snapshot.md](../../vision-modules/specs/snapshot.md)), under `<folder>/snapshot_<stage name>_<YYYYMMDDHHMMSS>.jpg` (`snapshot_path`; the classifier's crops get `_<i>` before the suffix, one per hand). They run on the **caller's** thread (the Gradio click handler), as the library requires — never on a worker — and **never raise**: a `ValueError` (nothing consumed yet, an unsupported suffix), `TypeError`, or `OSError` (an unwritable folder) comes back as a `⚠️ <stage>: …` status line, a classifier input with no hand as `⚠️ …: no crop to save`, and a success as `Saved \`<path>\`, …`. The UI shows that line under the buttons (see [app.md](app.md), "Vision line").

### App integration

See [app.md](app.md) for the composition; the rules this module imposes on it:

- Built in `build_system` **only when** the config's `vision` section has `enabled: true` ([configuration.md](configuration.md)) **and** the robot is up (no api ⇒ no camera ⇒ no vision, like ASR and the mic). Built **after** WICA, so `on_gesture` resolves against a live agent; the `hand_gesture` entry is registered only in that case too, so a vision-less run does not tell the model about a fist it can never see. Construction never loads a model or touches the camera (both happen on `start()`), so the only build failure is a programming/config error; it is still caught into a `Vision: …` banner line rather than crashing.
- `on_gesture` writes `world.update("hand_gesture", value)` guarded on a running agent (as `_deliver_speech` is): a value arriving before start or after teardown is dropped.
- Started after `wica.start()` (with the ASR bridge), stopped in `_shutdown` right after `wica.close()` and **before** the api's `__aexit__` — the camera source polls the api on the loop, so it must be stopped while the media session is still open, exactly the reason the ASR bridge is stopped before the api. Its `stop()` is a synchronous join, so it is not hopped onto the loop; it is isolated like every other teardown step.

## Testing

Drive `GestureInput` end-to-end through the real `vision-modules` pipeline with a fake `ReachyMiniApi` whose `get_camera_frame()` returns synthetic frames and a scripted `HandDetector` / `ImageClassifier` (no camera, no model, no network), at a high `fps` so the tests run in well under a second:

- Frames with a hand the classifier scores `fist` at ≥ threshold deliver `"fist"` exactly once, even across many frames.
- Then frames with no hand deliver `None` once; a hand back at `fist` delivers `"fist"` again (`None → fist → None → fist` ⇒ three deliveries).
- A `fist` score below the threshold delivers nothing; a hand classified as any other label delivers nothing (both stay `None`).
- `status()` reports the raw `label` / `confidence` even when the value is `None` (a sub-threshold fist shows `label=None`, `confidence=0.5`).
- With a short `fps_window_s`, `stream_fps` / `hand_fps` / `classifier_fps` go from `None` to a positive rate once frames flow, and `target_fps` is the configured rate.
- Snapshots: before any frame both save methods return a `⚠️` line and write nothing; once frames flow, the detector snapshot writes `snapshot_HandStage_<ts>.jpg` (the full frame) and the classifier snapshot `snapshot_GestureClassifier_<ts>_0.jpg` (the crop), each reporting `Saved …`; with no hand in frame the classifier snapshot reports `no crop to save`; an unwritable folder reports the `OSError` instead of raising.
- `RobotCameraSource`: `None` frames from the api are skipped (the provider keeps running and later frames arrive); a raising `get_camera_frame` sets `capture_error`, a later success clears it; `close()` makes `read()` return `None` promptly.
- `stop()` joins the threads and the callback is not called afterwards.
- In `tests/test_app.py`, with the fake api + `GestureModels`: a `None → fist` update on `hand_gesture` triggers a reasoning step whose prompt renders the fist line; `fist → None` does not trigger; a vision config with no robot builds no `GestureInput`; `enabled: false` builds none either (and registers no entry); teardown stops vision before the api exits.
- `tests-e2e/test_gesture_input_live.py` runs the **real** models: a fake api serving `vision-modules`' bundled `hand_fist.jpg` fixture must deliver `"fist"`, and `no_hand.jpg` must not — skipped when the fixtures or the models are unavailable (see [testing.md](testing.md)).

## Open questions

1. **More gestures.** Only `fist` is mapped. Exposing more of HaGRID's vocabulary (a `palm`/`stop` as an alternative stop sign, `like`/`dislike` as feedback, `one`…`four` as choices) means the entry becomes a gesture rather than a fist flag and the trigger predicate grows a whitelist. Deferred until a second gesture has a use.
2. **Debouncing.** A per-frame label can flicker between neighbouring classes (vision-modules gesture_classifier.md, open question 4), so a fist held at the threshold's edge could deliver `fist`/`None` repeatedly — each `None → fist` waking the agent. If observed, require N consecutive `fist` frames before delivering (a small counter in the watcher). Deferred until seen on the robot.
3. **Showing the camera in the UI.** Only a status line is shown; the frame with the hand box (as in the hand demo) would be a natural debug panel, at the cost of a per-tick image round-trip. Deferred.
4. **Vision without the robot.** A local webcam / browser source would let the vision path be developed with the `fake` backend (whose camera is a synthetic gradient — no hand). Deferred; the fake backend still exercises the plumbing.
