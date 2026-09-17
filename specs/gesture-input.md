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

**Status:** Updated

> **Updated (2026-09-17):** frames come from the **embodiment's camera** (a `CameraSource` injected into `GestureInput`), not from the robot api: `RobotCameraSource` moves to [reachy-embodiment.md](reachy-embodiment.md) and the computer gains a `WebcamSource` ([computer-embodiment.md](computer-embodiment.md)); the persona's "Hand signs" section is seeded at startup from this module rather than written in the prompt file. Open question 4 (vision without the robot) is resolved by that. Code catches up in the [merge plan](../plans/202609172311_wica-interaction-merge.md).

## Purpose

The **vision-in** half of the app — the first perception besides speech. The embodiment's camera — the robot's own, or a webcam on the computer — is run through the [`vision-modules`](../../vision-modules/README.md) hand pipeline (hand detection + HaGRID gesture classification, up to `max_hands` hands per frame), and each hand's gesture is turned into a WICA Input: a small, app-owned **hand-sign vocabulary** — `stop`, `like`, `dislike`, `ok`, `mute`, and the counts `one` … `five` — one World entry **per hand**. Showing a sign wakes the agent, which decides for itself whether (and how) it matters in the current interaction; only `stop` carries an explicit persona rule (stop whatever it is doing). No hand, an unconfident classification, or a HaGRID class outside the vocabulary is "no sign".

`vision-modules` *perceives* and this module *decides*: the library reports a label per frame with a score; this module owns the threshold, the label → sign reduction, the per-hand change detection, and the rule for which changes wake the agent. It is the one place that knows how a stream of per-frame classifications maps onto "the person is showing a sign", so the rest of the app (the WICA agent, the UI) never touches the vision pipeline directly.

Two consumers read from it, as for speech ([asr-bridge.md](asr-bridge.md)):

- **The WICA World** — each hand's current sign is a `hand_gesture_<n>` entry the agent reads in every prompt, and a hand *showing a new sign* triggers a reasoning step.
- **The Gradio UI** — each published hand's current label/confidence and the pipeline's health are displayed (see [app.md](app.md), "Vision line").

## Decided

### Frames come from the embodiment

The video source is whatever camera the embodiment looks through ([embodiment.md](embodiment.md), `camera(fps=…)`): the robot's camera through the bridge's `get_camera_frame()` on the Reachy Mini ([reachy-embodiment.md](reachy-embodiment.md), `RobotCameraSource`), a local webcam through `vision-modules`' `OpenCVSource` on the computer ([computer-embodiment.md](computer-embodiment.md), `WebcamSource`). `GestureInput` takes the source, it never builds one. What it requires of a source is the library's `FrameSource` protocol plus one accessor, the module's `CameraSource`:

```python
class CameraSource(FrameSource, Protocol):
    """A FrameSource that paces itself to the configured fps and reports its own health."""

    @property
    def capture_error(
        self,
    ) -> str | None: ...  # why the last read failed; None while healthy
```

Every embodiment's source obeys the same three rules, so the pipeline behaves the same over any camera:

- **It paces itself** to `fps` (sleeping the remainder of `1/fps` after each read), because the provider's capture loop calls `read()` back-to-back and a camera read faster than the hand stage consumes would be wasted work. Hand stage and classifier run at the same `fps`, so every read frame is looked at once.
- **A missing frame is not the end of the stream.** The provider ends its loop for good on a `None` from `read()`, so "nothing yet" (a daemon before its first frame, a webcam not opened yet) must *not* be reported as `None`: the source waits one interval and tries again. Only `close()` makes `read()` return `None`. The provider therefore never `ended`s on its own — the camera is live for as long as the app runs.
- **A failing read is visible, not fatal.** A raise is logged once per transition, recorded as `capture_error`, and the source keeps trying at the same pace; a later success clears it. A missing camera therefore shows on the Vision line rather than killing vision, and a transient failure heals.

No mirroring (`StreamProvider(source, mirror=False)`): the classifier sees the frame as captured.

### The pipeline

Exactly the hand demo's graph, with the robot as source and one consumer:

```
CameraSource ─▶ StreamProvider ─▶ HandStage ─▶ GestureClassifier ─▶ watcher ─▶ on_gesture(hand, sign)
 (paced at fps)   (no mirror)       (fps)          (fps, threshold)     (fps)
```

- `HandStage(provider, target_fps=fps, max_hands=max_hands, detector=…)` — `max_hands` from the config ([configuration.md](configuration.md), `vision.max_hands`, default **2**, the library's own default), default padding, rank (`"area"`) and hysteresis; the shipped MediaPipe detector unless one is injected. The app always passes `max_hands` explicitly, so a change of the library's default never silently changes what the robot looks at.
  - **Why 2 by default.** `max_hands` is also MediaPipe's tracking capacity ([vision-modules hand.md](../../vision-modules/specs/hand.md), "One count"): with 1, the detector locks onto the first hand it sees and a second hand is not even considered until the first is lost — so a person whose relaxed hand was tracked first could sign with the other and never be seen. With 2, both hands of the person are detected, published and classified — and each gets its own World entry (below), so `max_hands` is also the number of `hand_gesture_<n>` entries. The price is up to one more ViT pass per frame while two hands are in view; if that starves the classifier on a slow machine (the Vision line's achieved rate shows it), `max_hands: 1` is the fallback.
- `GestureClassifier(hands, target_fps=fps, threshold=threshold, device=device, classifier=…)` — the shipped HaGRID ViT unless one is injected. `threshold` is the library's own validation rule: a hand's `label` is its top class **only when its score reaches the threshold**, else `None`. The app's default is **0.55** ([configuration.md](configuration.md), `vision.threshold`), and the module adds no second threshold of its own.
- **Models load lazily**, on each stage's worker thread, at the first frame (that is the library's rule — see [vision-modules pipeline.md](../../vision-modules/specs/pipeline.md)). The first run downloads the MediaPipe bundle and the ~340 MB ViT; until they are ready the classifier publishes nothing and the status shows no label. A load failure (no network on first run, a missing extra) is recorded by the library in the stage's `last_error` and the graph keeps running; this module surfaces it in `status()["error"]` — vision never crashes the app.
- **Nothing here touches the World or WICA.** The `on_gesture` callback is injected, like `AsrBridge`'s `on_final_segment`, so the module is unit-testable with a recording callback, a scripted `CameraSource`, and scripted detector/classifier seams (no camera, no model).

### The reduction: label → sign, per hand

The watcher thread samples `gestures.latest()` at `fps`, once per new `frame_id`, and reduces **each hand slot** `n` (1 … `max_hands`) to one value through a hardcoded table, `SIGNS` (HaGRID class → sign):

| HaGRID label(s) | Sign |
|---|---|
| `fist`, `stop`, `stop_inverted` | `"stop"` |
| `like` | `"like"` |
| `dislike` | `"dislike"` |
| `ok` | `"ok"` |
| `mute` | `"mute"` |
| `one` | `"one"` |
| `two_up`, `two_up_inverted`, `peace`, `peace_inverted`, `rock` | `"two"` |
| `three`, `three2` | `"three"` |
| `four` | `"four"` |
| `palm` | `"five"` |

17 of the model's 18 classes are mapped; `call` is not, and neither is anything else the classifier might report. For slot `n`:

| Classifier result | Value of hand `n` |
|---|---|
| fewer than `n` hands published (no hand in frame, or only `n-1`) | `None` |
| `hands[n-1].label` is `None` (top score below threshold, or no crop) | `None` |
| `hands[n-1].label` is a class outside `SIGNS` (`call`) | `None` |
| `hands[n-1].label` is in `SIGNS` | `SIGNS[label]` |

The signs are the **app's** vocabulary, not HaGRID's: several classes that mean the same thing to a person collapse to one value (a fist or a flat hand are both `stop`; `peace`, `rock`, `two_up` all show two fingers), and the counts are named by fingers (`palm` → `five`). The mapping is a module constant for now (not a config knob) — the prompt and the persona depend on it.

**Hands are slots, not identities.** Hand `n` is the library's `hands[n-1]`, which stays the same physical hand while it stays in view ([vision-modules hand.md](../../vision-modules/specs/hand.md), "slot stability"). But the published tuple is **compacted**: when hand 1 leaves while hand 2 stays, the remaining hand becomes `hands[0]`. That shows up as hand 1 taking hand 2's sign (a change, so it triggers) and hand 2 going to `None`. Accepted for now — the agent is told the numbers are not left/right (see "What the agent does with it"); stable identity is open question 5.

**No cross-hand logic here.** Each hand is reported on its own: two hands showing `three` and `two` are two entries, not a `five`. Whether to combine them (a count on two hands, a sign on each) is left to the agent, deliberately not explained in the prompt.

### Change detection and the trigger rule (the core decision)

Two rules, at two layers:

1. **The module delivers changes only, per hand.** `on_gesture(hand, value)` is called for hand `n` when its reduced value differs from the last one delivered **for that hand** — including a change to `None` — never on every frame (the classifier runs at 5 fps; the World must not be churned at that rate with an unchanged value, which would also spam the World panel). Every hand's last-delivered value starts at `None`, matching the entries' initial `None`, so the first sign-less frames deliver nothing. A frame that changes both hands delivers twice (hand 1 then hand 2). The app wires `on_gesture` to `world.update(f"hand_gesture_{hand}", value)`.
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

   So `None → two` wakes the agent, and so does `two → three` (a direct switch, no sign-less frame between); `three → None` updates the entry silently (the agent sees the hand is down on its next step); `two → two` never reaches the World at all (rule 1), and even if it did the predicate would not fire.

   **Coalescing is not bypassed** (no `bypass_coalescing`, unlike the fist-only version): signs ride WICA's ~200 ms coalescing window like every other Input ([wica inputs.md](../../wica-framework/specs/inputs.md)). That is what folds two hands changing on the same frame — or a speech segment closing alongside a sign — into **one** reasoning step instead of two. The price is up to ~200 ms before a `stop` acts; revisit if that is felt on the robot.

### In the prompt: ambient state, not a one-shot event

Every `hand_gesture_<n>` entry is rendered in **every** prompt (`include_in_prompt` default), not only on the step it triggers, so the agent always knows what each hand currently shows:

| Value of hand `n` | Rendered |
|---|---|
| `None` | `The person's hand {n} is not showing a sign.` |
| a sign | `The person's hand {n} is showing the "{sign}" sign.` |

The `None` rendering is what an entry reads once the hand drops or relaxes (rule 1 delivers that change), so the agent is never left believing a sign is still up. The person-facing counterpart (`display_entry` in [app.md](app.md)) shows `✋ hand {n}: {sign}` / `✋ hand {n}: no sign`.

### What the agent does with it — the persona, seeded at startup

The "Hand signs" section of the persona is **not** written in either prompt file: this module owns it as `hand_signs_prompt_section()`, and the app appends it to the resolved persona when — and only when — vision is enabled ([app.md](app.md), build step 3), the same seam the embodiment's `prompt_addendum` uses. A persona never describes hands the agent cannot see, and the vocabulary lives in one place next to the `SIGNS` table it must match.

The section **describes the perception, not the reactions**: you can see the person's hands; each `hand_gesture_<n>` state says which sign hand `n` shows — one of `stop`, `like`, `dislike`, `ok`, `mute`, `one`, `two`, `three`, `four`, `five` — or none; hand numbers follow the hands in view in the order they appeared and are not left/right. It does **not** say what `like`, `ok`, a count, or two hands at once mean, nor whether to respond: the agent decides from the interaction context whether a sign is meant for it and what to make of it. That is deliberate — the point is to see how the agent interprets the signs unprompted.

**`stop` is the one exception**: when any hand shows `stop`, stop what you are doing — cancel every running Command (`cancel_command` on each in-flight `say` / action / whatever runs) and start nothing new; then, in the next turn, once the cancellation is observed done, `say` a short confirmation (a stop is a request like any other and gets a spoken reply); if nothing was running, just acknowledge briefly. Do not issue the cancel and the confirmation in the same turn (the robot persona's no-collision rule — [reachy-embodiment.md](reachy-embodiment.md); harmless on the computer). A `stop` going away is not a request for anything.

**A known limit** (WICA's, not this module's): the agent handles one reasoning step at a time and a trigger arriving mid-step is dropped, not queued. A running `say` is a Command, not a step, so a sign shown *while the robot speaks* does wake the agent — the common case. A sign shown while the model is still reasoning is lost; the person lowers and shows it again (or shows another), and the next change triggers. With a whole vocabulary this happens more often than with the fist alone; barge-in proper is deferred in WICA.

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
        source: CameraSource,
        *,
        on_gesture: OnGesture,
        threshold: float = 0.55,
        fps: float = 5.0,
        max_hands: int = 2,
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
    ) -> dict: ...  # {"running", "signs", "hands", "max_hands", "target_fps", "stream_fps", "hand_fps", "classifier_fps", "capture_error", "error"}
```

- `start()` / `stop()` are **synchronous** (threads, like the library's `Pipeline`), called from the app's main/Gradio thread, never from the loop. The source is opened by the provider on `start()` and closed on `stop()`. `stop()` joins every thread; `close()` hooks release the models. Both are idempotent.
- `status()` is a cheap read the UI timer polls: `signs` is the last *delivered* value of every hand, a list of length `max_hands` (`["two", None]`), `hands` one `{"label", "confidence"}` per hand in the latest classifier result, **in the library's slot order** (so entry *i* stays the same physical hand while it is in view) — each hand's validated top class (`None` below threshold or with no crop) and raw top score (`0.0` with no crop); an empty list with no hand or no result yet — `max_hands` the configured cap, `target_fps` the configured rate, `stream_fps` / `hand_fps` / `classifier_fps` each node's **achieved** rate (below), `capture_error` the camera source's, and `error` the message of the last exception a stage recorded (`hands.last_error` or `gestures.last_error`, the classifier's first), `None` when healthy.
- **Achieved rates, the hand demo's way.** A stage's real rate is capped by its upstream and by the model's speed, so `target_fps` is a ceiling, not a fact. Every node's `published_count` increments exactly once per publish — the two stages' and the `StreamProvider`'s alike (the library's exact counter for this — `frame_id` would not do, it is inherited from the camera frame) — so the watcher samples the three counters once per window (`fps_window_s`, default 1 s) and keeps (Δcount / Δtime) as that node's rate; `None` until two samples exist. The stream's rate is the rate the camera actually delivered frames at (a `None` frame is not a publish), against the configured rate — the provider's `source_fps` would only echo the source's `fps()`, the pace, since neither the bridge nor a webcam reports a usable rate. The UI shows each next to its configured rate, so a camera returning nothing, or a classifier starved by a slow model, is visible at a glance.
- `max_hands` must be `>= 1` (the library's `HandStage` raises `ValueError` otherwise; the config validates it first).
- `device` is forwarded to the classifier: `None` lets the library pick (`mps` → `cuda` → `cpu`).
- **Snapshots, the hand demo's way.** `save_detector_snapshot(folder)` / `save_classifier_snapshot(folder)` write what the hand stage / the classifier **last consumed** — the full camera frame it detected on, the hand crop(s) it classified — through the library's `save_input(stage, path)` ([vision-modules snapshot.md](../../vision-modules/specs/snapshot.md)), under `<folder>/snapshot_<stage name>_<YYYYMMDDHHMMSS>.jpg` (`snapshot_path`; the classifier's crops get `_<i>` before the suffix, one per hand). They run on the **caller's** thread (the Gradio click handler), as the library requires — never on a worker — and **never raise**: a `ValueError` (nothing consumed yet, an unsupported suffix), `TypeError`, or `OSError` (an unwritable folder) comes back as a `⚠️ <stage>: …` status line, a classifier input with no hand as `⚠️ …: no crop to save`, and a success as `Saved \`<path>\`, …`. The UI shows that line under the buttons (see [app.md](app.md), "Vision line").

### App integration

See [app.md](app.md) for the composition; the rules this module imposes on it:

- Built in `build_system` **only when** the config's `vision` section has `enabled: true` ([configuration.md](configuration.md)) **and** the embodiment is up (no body ⇒ no camera ⇒ no vision, like ASR and the mic) **and** the agent built. Built **after** WICA, over `embodiment.camera(fps=cfg.vision.fps)`, so `on_gesture` resolves against a live agent; the `hand_gesture_1` … `hand_gesture_<max_hands>` entries are registered — and `hand_signs_prompt_section()` appended to the persona — only in that case, so a vision-less run tells the model nothing about hands. Construction never loads a model or opens the camera (both happen on `start()`); a build failure is still caught into a `Vision: …` banner line rather than crashing.
- `on_gesture(hand, value)` writes `world.update(f"hand_gesture_{hand}", value)` guarded on a running agent (as `_deliver_speech` is): a value arriving before start or after teardown is dropped.
- Started after `wica.start()` (with the ASR bridge), stopped in `_shutdown` right after `wica.close()` and **before** the embodiment's `exit()` — the robot's source polls the api on the loop, so it must be joined while the media session is still open. Its `stop()` is a synchronous join, isolated like every other teardown step.

## Testing

Drive `GestureInput` end-to-end through the real `vision-modules` pipeline with a scripted `CameraSource` (synthetic frames, an injectable `capture_error`) and a scripted `HandDetector` / `ImageClassifier` (no camera, no model, no network), at a high `fps` so the tests run in well under a second:

- Frames with a hand the classifier scores `like` at ≥ threshold deliver `(1, "like")` exactly once, even across many frames.
- Then frames with no hand deliver `(1, None)` once; the hand back at `like` delivers `(1, "like")` again (`None → like → None → like` ⇒ three deliveries).
- The table: every mapped label reduces to its sign (e.g. `fist` and `stop_inverted` → `"stop"`, `rock` → `"two"`, `three2` → `"three"`, `palm` → `"five"`), and a switch between two signs with no sign-less frame (`one → two`) delivers the new sign directly. Two labels mapping to the same sign (`peace → rock`) deliver nothing new.
- An unconfident classification (score below the threshold) and an unmapped class (`call`) both deliver nothing (stay `None`).
- `status()` reports each hand's raw `label` / `confidence` even when the value is `None` (a sub-threshold `like` shows `hands == [{"label": None, "confidence": 0.5}]`), and `signs` has one entry per `max_hands`.
- **Two hands** (a scripted detector returning two boxes, a classifier scoring each crop by its position): a sign on the *second* hand only delivers `(2, sign)` and nothing for hand 1; different signs on both hands deliver one change each; hand 2 relaxing delivers `(2, None)` only; `hands` lists both in slot order. With `max_hands=1` the same frame publishes one hand and only hand 1 is ever delivered.
- `sign_shown`: `None → x` and `x → y` fire; `x → None` and `x → x` do not.
- With a short `fps_window_s`, `stream_fps` / `hand_fps` / `classifier_fps` go from `None` to a positive rate once frames flow, and `target_fps` is the configured rate.
- Snapshots: before any frame both save methods return a `⚠️` line and write nothing; once frames flow, the detector snapshot writes `snapshot_HandStage_<ts>.jpg` (the full frame) and the classifier snapshot `snapshot_GestureClassifier_<ts>_0.jpg` (the crop), each reporting `Saved …`; with no hand in frame the classifier snapshot reports `no crop to save`; an unwritable folder reports the `OSError` instead of raising.
- `hand_signs_prompt_section()` names every sign of the vocabulary (exactly the values of `SIGNS`) and the `stop` rule.
- The sources themselves are tested with their embodiments: `RobotCameraSource` in [reachy-embodiment.md](reachy-embodiment.md), `WebcamSource` in [computer-embodiment.md](computer-embodiment.md).
- `stop()` joins the threads and the callback is not called afterwards.
- In `tests/test_system.py`, with a `FakeEmbodiment` whose camera serves synthetic frames + `GestureModels`: `max_hands` entries `hand_gesture_1…n` are registered and the hand-signs section is in the first prompt; a `None → two` update on `hand_gesture_2` triggers a reasoning step whose prompt renders the hand-2 sign line (and the hand-1 "not showing a sign" line); `two → None` does not trigger; a vision config with no embodiment builds no `GestureInput`; `enabled: false` builds none either (registers no entry, seeds no section); teardown stops vision before the embodiment exits.
- `tests-e2e/test_gesture_input_live.py` runs the **real** models: a scripted source serving each of `vision-modules`' bundled photos must deliver its sign for hand 1 — `hand_fist.jpg` and `hand_stop.jpg` → `"stop"`, `hand_palm.jpg` → `"five"`, `hand_one.jpg` → `"one"`, `hand_three2.jpg` → `"three"` — and `no_hand.jpg` must deliver nothing — skipped when the fixtures or the models are unavailable (see [testing.md](testing.md)).

## Open questions

1. **Tuning the vocabulary.** The `SIGNS` table is hardcoded and chosen by hand (`call` left out; `rock` counted as two fingers; `mute` its own sign). Once the agent's unprompted interpretations are observed on the robot, the table and the persona's one-line vocabulary may change — or move to config if it needs per-deployment tuning.
2. **Debouncing.** A per-frame label can flicker between neighbouring classes (vision-modules gesture_classifier.md, open question 4), and a vocabulary makes that likelier than the fist alone: `stop` (fingers together) vs `five` (`palm`, fingers spread), `two` vs `three`, a hand at the threshold's edge flipping to `None` and back — each change to a sign waking the agent. If observed, require N consecutive identical frames before delivering (a small per-hand counter in the watcher). Deliberately deferred.
3. **Showing the camera in the UI.** Only a status line is shown; the frame with the hand box (as in the hand demo) would be a natural debug panel, at the cost of a per-tick image round-trip. Deferred.
4. **Vision without the robot.** **Resolved (2026-09-17):** the camera is the embodiment's, so the computer embodiment's `WebcamSource` runs the same pipeline over a laptop webcam ([computer-embodiment.md](computer-embodiment.md)); the bridge's `fake` backend still exercises the robot plumbing with its synthetic frame.
5. **Hand identity and handedness.** Entries are keyed by slot, and the library compacts slots when a hand leaves (see "Hands are slots, not identities"), so hand 2's sign can move to hand 1. Keying by `left` / `right` would need MediaPipe's handedness exposed on `Hand` ([vision-modules hand.md](../../vision-modules/specs/hand.md), open question 1) and a stable identity across gaps (open question 3 there). Deferred until the slot shift is seen to confuse the agent.
6. **Coalescing for `stop`.** Signs no longer bypass the coalescing window, so `stop` waits up to ~200 ms. A separate `bypass_coalescing` entry for the stop sign alone is the fix if that latency is felt.
