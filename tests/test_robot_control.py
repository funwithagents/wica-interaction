"""Functional tests for ``RobotControl`` — driven against the bridge's ``fake`` backend (no daemon,
no hardware, no network).

Tests drive the public ``RobotControl`` coroutines the way WICA's Commands will, using a real
``ReachyMiniApi("fake")`` so the *actual* verb wiring runs: the fake honors motor-state changes and
records the moves it is handed, so the motor precondition is genuinely enforced (not mocked). They
assert on the non-raising Command contract — a readable string in every case, never an exception —
and on the side effects the fake records, rather than on exact wording. See specs/robot-control.md
("Testing").

The verbs use ``asyncio.to_thread`` internally, so each coroutine is driven with ``asyncio.run`` to
provide a running loop (no async-test plugin required). The verbs exercised here (motors, emotions,
head tracking) touch no media, so the api is used without entering its context manager.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.fake_reachy_mini import FakeReachyMini

from reachy_mini_interaction_wica.robot_control import RobotControl


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _control() -> tuple[RobotControl, FakeReachyMini]:
    """A ``RobotControl`` over a fresh ``fake`` api, plus the underlying fake robot to assert on."""
    api = ReachyMiniApi("fake")
    robot = api.robot
    assert isinstance(robot, FakeReachyMini)
    return RobotControl(api), robot


def _recorded(robot: FakeReachyMini, name: str) -> list[dict[str, Any]]:
    return [args for cmd, args in robot.commands if cmd == name]


def test_list_emotions_returns_the_libraries_names() -> None:
    control, _ = _control()

    result = run(control.list_emotions())

    # The fake's stubbed library (see ReachyMiniApi._FAKE_EMOTIONS).
    for name in ("happy", "sad", "curious"):
        assert name in result


def test_play_known_emotion_succeeds_and_moves_the_robot_once_enabled() -> None:
    control, robot = _control()
    run(control.set_motors_state("enabled"))

    result = run(control.play_emotion("happy"))

    assert "happy" in result
    assert "failed" not in result
    plays = _recorded(robot, "async_play_move")
    assert len(plays) == 1
    assert plays[0]["move"] == "happy"  # the fake library returns the name as the move


def test_play_unknown_emotion_is_reported_not_raised() -> None:
    control, robot = _control()
    run(control.set_motors_state("enabled"))

    result = run(control.play_emotion("nonexistent"))

    assert "failed" in result  # a readable failure string, no exception
    assert _recorded(robot, "async_play_move") == []  # nothing was played


def test_movement_needs_motors_enabled_and_does_not_move_while_off() -> None:
    control, robot = _control()  # the fake starts with motors "disabled"

    play = run(control.play_emotion("happy"))
    track = run(control.start_head_tracking())

    # Both return the motor-precondition message (naming the fix) and neither raises nor moves.
    assert "set_motors_state('enabled')" in play
    assert "set_motors_state('enabled')" in track
    assert _recorded(robot, "async_play_move") == []
    assert _recorded(robot, "start_head_tracking") == []


def test_movement_succeeds_after_enabling_motors() -> None:
    control, robot = _control()

    assert "set_motors_state('enabled')" in run(
        control.start_head_tracking()
    )  # off ⇒ refused

    run(control.set_motors_state("enabled"))
    result = run(control.start_head_tracking())

    assert "failed" not in result
    assert len(_recorded(robot, "start_head_tracking")) == 1


def test_stop_head_tracking_works_without_motors() -> None:
    control, robot = _control()  # motors off — stopping is not a movement

    result = run(control.stop_head_tracking())

    assert "failed" not in result
    assert len(_recorded(robot, "stop_head_tracking")) == 1


def test_get_motors_state_reflects_set_motors_state() -> None:
    control, _ = _control()

    for state in ("enabled", "gravity_compensation", "disabled"):
        run(control.set_motors_state(state))
        assert state in run(control.get_motors_state())


def test_set_unknown_motor_state_is_reported_not_raised() -> None:
    control, _ = _control()

    result = run(control.set_motors_state("sleepy"))

    assert "failed" in result
    # The state was not changed by an invalid request: it stays at the fake's initial "disabled".
    assert "disabled" in run(control.get_motors_state())
