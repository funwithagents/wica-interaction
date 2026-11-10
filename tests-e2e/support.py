"""Helpers for the opt-in live tier.

A live test needs real credentials, devices and models. Rather than fail when one is absent, a test
calls `require_env(...)` / `require_device(...)` up front so it *skips* cleanly — you only exercise
what you have, and a contributor with none is never broken. A run that must not skip (CI) sets
`WICA_E2E_FAIL_ON_MISSING=1`, which turns each of those skips into a failure naming what is missing
(specs/e2e.md, "Skips and failures").
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self

import pytest
from wica.testing import CHECKS, CheckContext, CheckSpec, Finding, TestRun, run_check

from wica_interaction.system import AppSystem

if TYPE_CHECKING:
    from person import Person

FAIL_ON_MISSING = "WICA_E2E_FAIL_ON_MISSING"

# First run: the hand models download (~340 MB) and every engine warms up.
READY_TIMEOUT_S = 300.0


def missing(reason: str) -> NoReturn:
    """Skip the calling test for *reason* — or fail it, when the run sets `FAIL_ON_MISSING`."""
    if os.environ.get(FAIL_ON_MISSING) == "1":
        pytest.fail(f"{reason} ({FAIL_ON_MISSING}=1)", pytrace=False)
    pytest.skip(reason)


def require_env(name: str) -> str:
    """Return env var `name`, or skip (fail under `FAIL_ON_MISSING`) if it's unset/empty."""
    value = os.environ.get(name)
    if not value:
        missing(f"{name} not set")
    return value


def require_device(name: str, kind: Literal["input", "output"]) -> str:
    """Return *name* once an audio *kind* device by that name exists, or skip (fail under
    `FAIL_ON_MISSING`). The engines open devices by name too; checking here turns a missing cable
    into a clear skip rather than a scenario that times out."""
    import sounddevice as sd

    try:
        sd.query_devices(name, kind)
    except ValueError:
        missing(f"no {kind} audio device named {name!r}")
    return name


def is_device(name: str, kind: Literal["input", "output"]) -> bool:
    import sounddevice as sd

    try:
        sd.query_devices(name, kind)
    except ValueError:
        return False
    return True


def echo_device(speaker: str | None) -> str | None:
    """The device the room records the app's voice from (specs/e2e.md, "The room"):
    `WICA_E2E_ECHO_DEVICE`, else the app's *speaker* when it is also an input device (BlackHole),
    else none — the run then has no echo."""
    named = os.environ.get("WICA_E2E_ECHO_DEVICE") or None
    if named is not None:
        return named
    if speaker is not None and is_device(speaker, "input"):
        return speaker
    return None


def wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@dataclass(frozen=True)
class Delivered:
    t: float
    key: str
    value: Any


class InputLog:
    """What the app's inputs wrote to the World — the ASR's `speech_input`, the gesture input's
    `hand_gesture_<n>` — recorded by a World listener. The test harness's timeline holds a trigger
    for each write but not its value, and `expect_world` only sees the current value of an entry
    that a TTL clears, so the scenarios read what was delivered here."""

    def __init__(self, world: Any, keys: Iterable[str]) -> None:
        self._cond = threading.Condition()
        self._delivered: list[Delivered] = []
        for key in keys:
            world.add_listener(key, self._listener(key))

    def _listener(self, key: str) -> Callable[[Any], None]:
        def record(entry: Any) -> None:
            with self._cond:
                self._delivered.append(
                    Delivered(time.monotonic(), key, entry.current.value)
                )
                self._cond.notify_all()

        return record

    def delivered(self, key: str | None = None) -> list[Delivered]:
        with self._cond:
            return [d for d in self._delivered if key is None or d.key == key]

    def wait_for(
        self,
        key: str,
        predicate: Callable[[Any], bool],
        *,
        within: float,
        after: float = 0.0,
    ) -> Delivered | None:
        """The first value written to *key* at or after the monotonic time *after* that satisfies
        *predicate*, waiting up to *within* seconds; None on timeout."""
        deadline = time.monotonic() + within
        with self._cond:
            while True:
                for d in self._delivered:
                    if d.key == key and d.t >= after and predicate(d.value):
                        return d
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)


# The framework's checks run over every scenario's whole timeline (specs/e2e.md, "The scenarios"),
# all but `answers_each_input`: it counts turns from the tester's own writes, and here the app's
# inputs write the World, so it would pass over zero turns. `no_noop_on_input` judges speech only:
# the persona lets the agent decide a sign was not meant for it.
SCENARIO_CHECKS = ("no_noop_on_input", "no_failed_reactions", "no_colliding_cancel")


def failed_checks(
    run: TestRun, checks: tuple[str, ...] = SCENARIO_CHECKS, *, since: float = 0.0
) -> list[Finding]:
    """The framework's *checks* over *run*'s timeline from the wall-clock time *since* (a step's
    start; the whole run by default); the ones that failed."""
    whole = run.timeline()
    timeline = [e for e in whole if e.t >= since]
    context = CheckContext(
        output_command_name=run.probe.output_command_name,
        scenario_inputs=("speech_input",),
        origin=whole[0].t if whole else 0.0,
    )
    findings = [
        run_check(CHECKS[name], CheckSpec(name), timeline, context) for name in checks
    ]
    return [f for f in findings if not f.ok]


def reactions_to(run: TestRun, key: str) -> int:
    """How many of *run*'s reactions so far had a write to *key* among their triggers."""
    return sum(
        1
        for e in run.timeline()
        if e.reaction is not None
        and any(
            t.key == key and not t.is_command_completion for t in e.reaction.triggers
        )
    )


@dataclass(frozen=True)
class E2E:
    """One scenario's running system and what it observes it through; *echo* is whether the room
    plays the app's voice back into its mic."""

    system: AppSystem
    run: TestRun
    inputs: InputLog
    person: Person
    echo: bool


def forget_earlier(run: TestRun) -> None:
    """Make *run*'s next `expect_*` match only what happens from now on. The harness moves its
    cursor past a match, and on its own `set`; here the app's inputs write the World, so a
    conversation's step would otherwise match an event left from an earlier one (a `say` still
    listed as running). The harness has no public call for it."""
    with run._cond:  # pyright: ignore[reportPrivateUsage]
        run._watermark = len(run._timeline)  # pyright: ignore[reportPrivateUsage]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2}


def _overlap(heard: set[str], said: str) -> float:
    return len(heard & _words(said)) / len(heard) if heard else 0.0


def echoes(run: TestRun, inputs: InputLog, person_said: list[str]) -> list[str]:
    """The `speech_input`s that are the app's own words: mostly words of something its voice said,
    and not mostly the person's (specs/e2e.md, "The conversation"). The person's lines count
    together, since a passage of several is delivered as one, and the app may say it back."""
    voice = run.probe.output_command_name
    app_said = [
        str((e.args or {}).get("text", ""))
        for e in run.timeline()
        if e.kind == "command_issued" and e.name == voice
    ]
    found = []
    for d in inputs.delivered("speech_input"):
        heard = _words(str(d.value or ""))
        app = max((_overlap(heard, t) for t in app_said), default=0.0)
        person = _overlap(heard, " ".join(person_said))
        if app >= 0.5 and person < 0.5:
            found.append(str(d.value))
    return found


# How long the agent must stay idle to count as settled: a Command's completion reaches the agent
# as a trigger a moment after the Command ends, and starts one more reaction.
SETTLE_S = 1.0


def _idle(run: TestRun) -> bool:
    """No reaction in flight and no Command running, by *run*'s timeline."""
    timeline = run.timeline()
    prompts = sum(1 for e in timeline if e.kind == "prompt")
    ended = sum(1 for e in timeline if e.kind == "reaction_ended")
    states: dict[str, str] = {}
    for e in timeline:
        if e.call_id is None or e.name in (None, "noop"):
            continue
        if e.kind == "command_issued":
            states.setdefault(e.call_id, "running")
        elif e.kind == "command_state":
            states[e.call_id] = e.state or ""
    return prompts == ended and "running" not in states.values()


def settled(run: TestRun) -> bool:
    """Whether the agent is idle and stays so for `SETTLE_S`."""
    if not _idle(run):
        return False
    time.sleep(SETTLE_S)
    return _idle(run)


def command_spans(run: TestRun) -> list[tuple[str, float, float]]:
    """Each Command call of *run* so far as `(name, issued, ended)`, in timeline seconds; a call
    still running ends at infinity."""
    started: dict[str, tuple[str, float]] = {}
    ended: dict[str, float] = {}
    for e in run.timeline():
        if e.call_id is None or e.name in (None, "noop"):
            continue
        if e.kind == "command_issued":
            started[e.call_id] = (e.name, e.t)
        elif e.kind == "command_state" and e.state in (
            "complete",
            "failed",
            "cancelled",
        ):
            ended[e.call_id] = e.t
    return [
        (name, t, ended.get(call_id, float("inf")))
        for call_id, (name, t) in started.items()
    ]


class EchoRecorder:
    """What the computer's echo canceller saw and made, saved as evidence when the room's echo is on
    (specs/e2e.md, "Evidence on failure"): the mic as captured (`near`), the voice's reference
    (`far`) and the cleaned mic the ASR heard (`out`), each int16 at 16 kHz, frame-aligned. From
    them the echo's delay and how much of it the canceller removed can be read back offline."""

    def __init__(self) -> None:
        from wica_interaction.computer import audio_processing

        self._cls = audio_processing.AudioProcessingCoordinator
        self._process = self._cls._process_chunk
        self._pop = self._cls._pop_far
        self.near: list[Any] = []
        self.far: list[Any] = []
        self.out: list[Any] = []

    def __enter__(self) -> Self:
        import numpy as np

        recorder, process, pop = self, self._process, self._pop

        def _process_chunk(coordinator: Any, chunk: bytes) -> bytes:
            recorder.near.append(np.frombuffer(chunk, np.int16).copy())
            out = process(coordinator, chunk)
            recorder.out.append(np.frombuffer(out, np.int16).copy())
            return out

        def _pop_far(coordinator: Any, n: int) -> Any:
            frame = pop(coordinator, n)
            recorder.far.append(frame.copy())
            return frame

        self._cls._process_chunk = _process_chunk  # pyright: ignore[reportAttributeAccessIssue]
        self._cls._pop_far = _pop_far  # pyright: ignore[reportAttributeAccessIssue]
        return self

    def __exit__(self, *_exc: object) -> None:
        self._cls._process_chunk = self._process  # pyright: ignore[reportAttributeAccessIssue]
        self._cls._pop_far = self._pop  # pyright: ignore[reportAttributeAccessIssue]

    def save(self, path: Any) -> None:
        import numpy as np

        def joined(chunks: list[Any]) -> Any:
            return np.concatenate(chunks) if chunks else np.zeros(0, np.int16)

        np.savez_compressed(
            path, near=joined(self.near), far=joined(self.far), out=joined(self.out)
        )
