"""Helpers for the opt-in live tier.

A live test needs real credentials, devices and models. Rather than fail when one is absent, a test
calls `require_env(...)` / `require_device(...)` up front so it *skips* cleanly — you only exercise
what you have, and a contributor with none is never broken. A run that must not skip (CI) sets
`WICA_E2E_FAIL_ON_MISSING=1`, which turns each of those skips into a failure naming what is missing
(specs/e2e.md, "Skips and failures").
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn

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


def failed_checks(run: TestRun) -> list[Finding]:
    """The framework's checks over *run*'s timeline so far; the ones that failed."""
    timeline = run.timeline()
    context = CheckContext(
        output_command_name=run.probe.output_command_name,
        scenario_inputs=("speech_input",),
        origin=timeline[0].t if timeline else 0.0,
    )
    findings = [
        run_check(CHECKS[name], CheckSpec(name), timeline, context)
        for name in SCENARIO_CHECKS
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
    """One scenario's running system and what it observes it through."""

    system: AppSystem
    run: TestRun
    inputs: InputLog
    person: Person
