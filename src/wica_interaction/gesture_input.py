"""Vision-in: the embodiment's camera → hand/gesture pipeline → the ``hand_gesture_<n>`` sign inputs.

The vision half of the app's perception, over whatever camera the embodiment looks through (the
robot's own, a webcam on the computer — specs/embodiment.md): ``GestureInput`` takes a
``CameraSource``, it never builds one. It runs the library's
hand stage + HaGRID gesture classifier over it (up to ``max_hands`` hands per frame) and reduces
**each hand** to one value of the app's own sign vocabulary — ``SIGNS`` maps the classifier's labels
onto ``"stop"``, ``"like"``, ``"dislike"``, ``"ok"`` and the counts ``"one"`` … ``"five"``;
anything unmapped, unconfident or absent is ``None`` — holds each hand's value for ``hold_s`` (a
per-hand ``HoldFilter``: unbroken on every result for that long, so a one-frame flicker never
counts) and delivers ``(hand, sign)`` to the injected ``on_gesture`` callback **on change only, per
hand**. The app wires that callback to
``world.update(f"hand_gesture_{hand}", sign)`` on entries whose trigger predicate, ``sign_shown``,
wakes the agent whenever a hand shows a sign (never when one goes away). The vocabulary's persona
text lives here too, as ``hand_signs_prompt_section()``, appended to the resolved persona at
startup when — and only when — vision is on, so it stays next to the ``SIGNS`` table it describes.
The library perceives, this module decides; nothing here touches WICA. See specs/gesture-input.md.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, Protocol

from vision_modules import (
    FrameSource,
    GestureClassifier,
    HandDetector,
    HandStage,
    ImageClassifier,
    Pipeline,
    Stage,
    StreamProvider,
    save_input,
)

logger = logging.getLogger(__name__)

# (hand number, 1…max_hands; that hand's sign, or None for "no sign")
OnGesture = Callable[[int, str | None], None]

# The app's sign vocabulary: HaGRID class (the library reports them verbatim) → the value the World
# carries. Several classes collapse onto one sign — a fist and a flat hand both mean "stop", and
# `peace` / `rock` / `two_up` all show two fingers — and the counts are named by fingers, so `palm`
# is "five". `call` and `mute` are the classes left unmapped (they reduce to `None`, like an
# unconfident or absent hand). Hardcoded for now: the prompt and the persona depend on it (specs/gesture-input.md,
# "The reduction: label → sign, per hand").
SIGNS: Mapping[str, str] = {
    "fist": "stop",
    "stop": "stop",
    "stop_inverted": "stop",
    "like": "like",
    "dislike": "dislike",
    "ok": "ok",
    "one": "one",
    "two_up": "two",
    "two_up_inverted": "two",
    "peace": "two",
    "peace_inverted": "two",
    "rock": "two",
    "three": "three",
    "three2": "three",
    "four": "four",
    "palm": "five",
}

# How often the watcher samples each stage's achieved rate (delta published_count / delta time).
# One second smooths a 5 fps target into a readable figure; the UI timer (0.2 s) reads the latest.
_FPS_WINDOW_S = 1.0


def snapshot_path(folder: str | PathLike[str], stage_name: str, now: datetime) -> Path:
    """``<folder>/snapshot_<stage name>_<YYYYMMDDHHMMSS>.jpg`` — the hand demo's naming (local
    wall-clock time); ``save_input`` inserts ``_<i>`` per crop for the classifier's input."""
    return Path(folder) / f"snapshot_{stage_name}_{now:%Y%m%d%H%M%S}.jpg"


# The "Hand signs" persona section, owned here rather than written in either prompt file: the
# app appends it at startup when vision is on, so a persona never describes hands the agent
# cannot see and the vocabulary stays next to the SIGNS table it must match
# (specs/gesture-input.md, "What the agent does with it"). It describes the *perception*, not
# the reactions — what each sign means is the agent's to read from context — except `stop`,
# the one sign that carries an instruction.
_HAND_SIGNS_TEXT = (
    "You can see the person's hands. When hand *n* shows a sign, a `hand_gesture_<n>` state appears saying which one — one of `stop`, `like`, `dislike`, `ok`, `one`, `two`, `three`, `four`, `five` — and it is gone again a moment later: it tells you what the person just did, not what they are still holding, and no such state means no sign was shown just now. The numbers follow the hands in view, in the order they appeared; they are not left and right, and a hand can change number when the other one leaves. A sign is not necessarily meant for you: read it the way you read anything else the person does, and decide for yourself whether it matters here and what to make of it."
    "\n\n"
    "One sign always means the same thing: **`stop`**. When a hand shows `stop`, **stop what you are doing.** Cancel every command that is running (`cancel_command` on each in-flight `say` or anything else), and start nothing new. Then, in the next turn, once you see the cancellation done, `say` a short confirmation that you stopped — a stop is a request like any other and gets a spoken reply, never a silent halt. If nothing was running, just briefly acknowledge out loud. Do not issue the cancel and the confirmation in the same turn. The `stop` state disappearing is not a request for anything: do not react to it on its own, just carry on when the person next speaks."
)


def hand_signs_prompt_section() -> str:
    """The persona's "Hand signs" section, appended by the app to the resolved persona at startup
    when vision is enabled (specs/gesture-input.md). Starts with a blank line so it reads as a new
    section after whatever precedes it."""
    return "\n\n## Hand signs\n\n" + _HAND_SIGNS_TEXT


def sign_shown(old: str | None, new: str | None) -> bool:
    """The ``hand_gesture_<n>`` trigger predicate: only a sign being *shown* wakes the agent.

    ``None → "two"`` triggers, and so does ``"two" → "three"`` (a hand switching sign with no
    sign-less frame between). A sign going away (``"two" → None``) updates the entry silently, and an
    unchanged sign never fires (it never reaches the World anyway — ``GestureInput`` delivers changes
    only). The app's entries also expire on a TTL (``system.HAND_GESTURE_TTL``, a World reset that
    never triggers), so ``old`` is usually ``None`` by the time the hand changes sign.
    """
    return new is not None and new != old


class CameraSource(FrameSource, Protocol):
    """A ``FrameSource`` that paces itself to the configured fps and reports its own health.

    What this module requires of the camera the embodiment hands it (specs/gesture-input.md,
    "Frames come from the embodiment"): the library's ``FrameSource`` protocol — ``open``/``read``/
    ``close``/``fps`` — plus ``capture_error``, so the Vision line can say *why* frames stopped.
    Every implementation obeys the same three rules: it paces itself to ``fps``; a missing frame is
    not the end of the stream (only ``close()`` makes ``read()`` return ``None``); and a failing
    read is recorded in ``capture_error`` and retried, never fatal.
    """

    @property
    def capture_error(self) -> str | None:
        """Why the last read failed; ``None`` while healthy (or before the first read)."""
        ...


class HoldFilter:
    """One hand slot's hold: a value is delivered only once it has been seen, unbroken, for
    ``hold_s`` seconds (specs/gesture-input.md, "The hold filter").

    ``value`` is the last *delivered* value (``None`` to start, matching the World entry).
    ``observe(value, now)`` feeds one classifier result and returns whether ``value`` just became
    the delivered one. A value differing from the delivered one starts a candidate stamped ``now``;
    each later observation of the same candidate delivers on the first ``now`` at least ``hold_s``
    after the stamp; a different value restarts the candidate from scratch (one dissenting frame
    resets the clock — strictly consecutive, no majority vote); the delivered value seen again drops
    the candidate. Applies to every transition, ``None`` included, and ``hold_s == 0`` delivers on the
    first observation. Pure — no clock, no thread — so it is tested with explicit times.
    """

    def __init__(self, hold_s: float) -> None:
        if hold_s < 0:
            raise ValueError("hold_s must be >= 0")
        self._hold_s = hold_s
        self.value: str | None = None
        self._candidate: str | None = None
        self._since: float | None = None

    def observe(self, value: str | None, now: float) -> bool:
        if value == self.value:
            self._candidate, self._since = None, None
            return False
        if self._since is None or value != self._candidate:
            self._candidate, self._since = value, now
        if now - self._since < self._hold_s:
            return False
        self.value = value
        self._candidate, self._since = None, None
        return True


class _FpsMeter:
    """A stage's real achieved rate from its ``published_count`` alone — the hand demo's meter.

    ``published_count`` increments exactly once per publish, so (delta count / delta time) between
    two samples is the exact rate over that window. ``frame_id`` would not do: it is inherited from
    the upstream frame, so it reflects the camera's rate, not this stage's own.
    """

    def __init__(self) -> None:
        self._last_count: int | None = None
        self._last_t: float | None = None
        self.fps: float | None = None  # None until two samples exist

    def sample(self, published_count: int) -> None:
        now = time.monotonic()
        if self._last_count is not None and self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                self.fps = (published_count - self._last_count) / dt
        self._last_count, self._last_t = published_count, now


@dataclass(frozen=True)
class GestureModels:
    """Injectable perception backends (tests); ``None`` for either means the library's shipped
    model (MediaPipe hand landmarker / HaGRID ViT), loaded lazily on its stage's thread."""

    detector: HandDetector | None = None
    classifier: ImageClassifier | None = None


class GestureInput:
    """Runs the hand + gesture pipeline over the embodiment's camera and delivers each hand's sign
    changes.

    Graph: ``CameraSource → StreamProvider → HandStage → GestureClassifier``, every stage at
    ``fps``, plus a watcher thread (also at ``fps``) that reduces each new classifier result to one
    value per hand slot — ``SIGNS[label]`` for a validated, mapped label, else ``None`` — runs it
    through that slot's ``HoldFilter`` (the value must hold, unbroken, for ``hold_s`` seconds —
    ``0`` delivers at once) and calls ``on_gesture(hand, sign)`` only for the hands whose held value
    changed from the last one delivered for *that* hand (all starting at ``None``, matching the
    World entries' initial value). Hand ``n`` is
    the library's ``hands[n-1]``: the same physical hand while it stays in view, but slots are
    compacted when a hand leaves, so a remaining hand can move down a slot (specs/gesture-input.md,
    "Hands are slots, not identities"). ``threshold`` is the
    library's own validation rule — a hand's label is its top class only at or above it — so this
    module adds no second one. ``max_hands`` is always passed to the hand stage (never the library's
    default), so what the camera looks at is the app's setting, and it is also how many hands are
    reported. ``start``/``stop`` are synchronous and idempotent (threads, like the
    library's ``Pipeline``); ``status()`` is the cheap read the UI timer polls.
    """

    def __init__(
        self,
        source: CameraSource,
        *,
        on_gesture: OnGesture,
        threshold: float = 0.55,
        fps: float = 5.0,
        max_hands: int = 2,
        hold_s: float = 0.5,
        device: str | None = None,
        models: GestureModels | None = None,
        fps_window_s: float = _FPS_WINDOW_S,
    ) -> None:
        models = models if models is not None else GestureModels()
        self._hold_s = hold_s
        # One hold per hand slot (hand n is `_holds[n - 1]`); each owns its last delivered value.
        self._holds = [HoldFilter(hold_s) for _ in range(max_hands)]
        self._on_gesture = on_gesture
        self._target_fps = fps
        self._max_hands = max_hands
        self._interval = 1.0 / fps
        self._fps_window_s = fps_window_s
        self._stream_meter = _FpsMeter()
        self._hand_meter = _FpsMeter()
        self._classifier_meter = _FpsMeter()
        self._source = source
        self._provider = StreamProvider(self._source, mirror=False)
        self._hands = HandStage(
            self._provider,
            target_fps=fps,
            max_hands=max_hands,
            detector=models.detector,
        )
        self._gestures = GestureClassifier(
            self._hands,
            target_fps=fps,
            threshold=threshold,
            classifier=models.classifier,
            device=device,
        )
        self._pipeline = Pipeline([self._provider, self._hands, self._gestures])
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # Every hand of the latest result, in the library's slot order: (validated label, top score).
        self._seen: tuple[tuple[str | None, float], ...] = ()

    def start(self) -> None:
        """Start the pipeline (upstream-first) and the watcher. A second call is a no-op."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._pipeline.start()
        self._thread = threading.Thread(
            target=self._watch, name="gesture-input-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the watcher, then the pipeline (downstream-first); joins every thread. Idempotent."""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join()
        self._thread = None
        self._pipeline.stop()

    @property
    def running(self) -> bool:
        return self._thread is not None

    def status(self) -> dict:
        """What the Vision line renders: every hand's last delivered sign (past the hold), each hand's
        latest label/score (slot order — entry *i* stays the same physical hand while it is in
        view), the hand cap, the hold, the target rate and each node's achieved rate — the camera stream, the hand stage, the
        classifier (``None`` until a full sampling window has passed) — the camera's capture health,
        and the last stage error (the classifier's first — a failed model load lands there),
        ``None`` when healthy."""
        with self._lock:
            signs, seen = [hold.value for hold in self._holds], self._seen
            stream_fps = self._stream_meter.fps
            hand_fps, classifier_fps = self._hand_meter.fps, self._classifier_meter.fps
        error = self._gestures.last_error or self._hands.last_error
        return {
            "running": self.running,
            "signs": signs,
            "hands": [
                {"label": label, "confidence": confidence} for label, confidence in seen
            ],
            "max_hands": self._max_hands,
            "hold_s": self._hold_s,
            "target_fps": self._target_fps,
            "stream_fps": stream_fps,
            "hand_fps": hand_fps,
            "classifier_fps": classifier_fps,
            "capture_error": self._source.capture_error,
            "error": str(error) if error is not None else None,
        }

    # --- Snapshots: what each stage last consumed, saved from the caller's thread -------------

    def save_detector_snapshot(
        self, folder: str | PathLike[str], now: datetime | None = None
    ) -> str:
        """Save the full frame the hand stage last detected on; returns a one-line status."""
        return self._save_snapshot(self._hands, folder, now)

    def save_classifier_snapshot(
        self, folder: str | PathLike[str], now: datetime | None = None
    ) -> str:
        """Save the hand crop(s) the classifier last classified; returns a one-line status."""
        return self._save_snapshot(self._gestures, folder, now)

    @staticmethod
    def _save_snapshot(
        stage: Stage[Any, Any], folder: str | PathLike[str], now: datetime | None
    ) -> str:
        """The hand demo's ``save_snapshot``: ``save_input(stage)`` into *folder* under the
        ``snapshot_<stage>_<timestamp>.jpg`` name, returning a status line the UI shows instead of
        raising, so a click can never error out. Runs on the caller's (Gradio) thread — the
        library's rule for the snapshot functions — never on a worker."""
        if now is None:
            now = datetime.now(
                tz=UTC
            ).astimezone()  # aware, in the machine's local zone
        target = snapshot_path(folder, stage.name, now)
        try:
            written = save_input(stage, target)
        except (ValueError, TypeError, OSError) as exc:
            return f"⚠️ {stage.name}: {exc}"
        paths = (
            [written]
            if isinstance(written, Path)
            else [p for p in written if p is not None]
        )
        if not paths:
            return f"⚠️ {stage.name}: no crop to save (no hand in its last input)"
        return "Saved " + ", ".join(f"`{p}`" for p in paths)

    def _watch(self) -> None:
        seen: int | None = None
        next_sample = time.monotonic()
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            if t0 >= next_sample:
                # The achieved-rate meters, sampled once per window (like the demo's 1 s timer).
                with self._lock:
                    self._stream_meter.sample(self._provider.published_count)
                    self._hand_meter.sample(self._hands.published_count)
                    self._classifier_meter.sample(self._gestures.published_count)
                next_sample = t0 + self._fps_window_s
            result = self._gestures.latest()
            if result is not None and result.frame_id != seen:
                seen = result.frame_id
                seen_hands = tuple(
                    (hand.label, hand.confidence) for hand in result.hands
                )
                # One value per slot, whether or not that hand is in the frame: a hand that left
                # reads `None`, like one showing nothing the vocabulary names. Each slot's value
                # goes through its hold: only a value seen unbroken for `hold_s` is delivered.
                values = [
                    SIGNS.get(seen_hands[i][0] or "") if i < len(seen_hands) else None
                    for i in range(self._max_hands)
                ]
                now = time.monotonic()
                with self._lock:
                    self._seen = seen_hands
                    changes = [
                        (i + 1, value)
                        for i, (hold, value) in enumerate(
                            zip(self._holds, values, strict=True)
                        )
                        if hold.observe(value, now)
                    ]
                for hand, value in changes:  # outside the lock: a consumer may be slow
                    self._deliver(hand, value)
            self._stop_event.wait(max(self._interval - (time.monotonic() - t0), 0.0))

    def _deliver(self, hand: int, value: str | None) -> None:
        try:
            self._on_gesture(hand, value)
        except Exception:  # the watcher must survive a failing consumer
            logger.exception("on_gesture(%d, %r) failed", hand, value)
