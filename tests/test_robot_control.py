"""Functional tests for ``RobotControl`` — driven against the bridge's ``fake`` backend (no daemon,
no hardware, no network).

Tests drive the public ``RobotControl`` coroutines the way WICA's Commands will, using a real,
**entered** ``ReachyMiniApi("fake")`` so the *actual* verb wiring runs: the fake honors motor-state
changes and records the moves it is handed, so the motor precondition is genuinely enforced (not
mocked). They assert on the non-raising Command contract — a readable string in every case, never an
exception — and on the side effects the fake records, rather than on exact wording. See
specs/robot-control.md ("Testing").

The verbs use ``asyncio.to_thread`` internally, so each coroutine is driven with ``asyncio.run`` to
provide a running loop (no async-test plugin required). ``api.robot`` is only available while entered
(``reachy-mini-bridge``'s entered-only lifecycle — construction never connects), so each test enters
the ``fake`` api itself and exits it in a ``finally``; the ``fake`` backend needs no daemon, so this
stays offline and fast. ``play_emotion`` plays through the bridge's motion loop and awaits until the
trajectory ends (see reachy-mini-bridge specs/motion.md, "Emotions through the loop"), so a successful
play is observed here as its sidecar sound reaching the fake's media layer (``media.play_sound``), not
as a discrete "move" command.

Head tracking is deliberately not exercised here: it is no longer an agent-facing command (the
bridge's motion loop drives it automatically, like presence and breathing) — see specs/robot-control.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.fake_reachy_mini import FakeReachyMini

from reachy_mini_interaction_wica.robot_control import RobotControl


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@contextmanager
def _control() -> Iterator[tuple[RobotControl, FakeReachyMini]]:
    """A ``RobotControl`` over a freshly entered ``fake`` api, plus the underlying fake robot to assert
    on. Exits the api on the way out, matching how the app itself uses it."""
    api = ReachyMiniApi("fake")
    run(api.__aenter__())
    try:
        robot = api.robot
        assert isinstance(robot, FakeReachyMini)
        yield RobotControl(api), robot
    finally:
        run(api.__aexit__(None, None, None))


def _recorded(robot: FakeReachyMini, name: str) -> list[dict[str, Any]]:
    return [args for cmd, args in robot.commands if cmd == name]


def test_list_emotions_returns_the_libraries_names() -> None:
    with _control() as (control, _):
        result = run(control.list_emotions())

        # The fake's stubbed library (see ReachyMiniApi._FAKE_EMOTIONS).
        for name in ("happy", "sad", "curious"):
            assert name in result


def test_play_known_emotion_succeeds_and_moves_the_robot_once_enabled() -> None:
    with _control() as (control, robot):
        run(control.set_motors_state("enabled"))

        result = run(control.play_emotion("happy"))

        assert "happy" in result
        assert "failed" not in result
        # The fake library resolves the name to a recorded move with a sidecar sound
        # ("happy.ogg"); the loop starts it as the trajectory plays.
        assert _recorded(robot, "media.play_sound") == [{"sound_file": "happy.ogg"}]


def test_play_unknown_emotion_is_reported_not_raised() -> None:
    with _control() as (control, robot):
        run(control.set_motors_state("enabled"))

        result = run(control.play_emotion("nonexistent"))

        assert "failed" in result  # a readable failure string, no exception
        assert _recorded(robot, "media.play_sound") == []  # nothing was played


def test_movement_needs_motors_enabled_and_does_not_move_while_off() -> None:
    with _control() as (control, robot):  # the fake starts with motors "disabled"
        play = run(control.play_emotion("happy"))

        # Returns the motor-precondition message (naming the fix) and neither raises nor moves.
        assert "set_motors_state('enabled')" in play
        assert _recorded(robot, "media.play_sound") == []


def test_get_motors_state_reflects_set_motors_state() -> None:
    with _control() as (control, _):
        for state in ("enabled", "gravity_compensation", "disabled"):
            run(control.set_motors_state(state))
            assert state in run(control.get_motors_state())


def test_set_unknown_motor_state_is_reported_not_raised() -> None:
    with _control() as (control, _):
        result = run(control.set_motors_state("sleepy"))

        assert "failed" in result
        # The state was not changed by an invalid request: it stays at the fake's initial "disabled".
        assert "disabled" in run(control.get_motors_state())
