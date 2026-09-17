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
from wica import Command

from wica_interaction.robot_control import (
    RobotControl,
    emotions_prompt_section,
)

# The fake's stubbed library (see ReachyMiniApi._FAKE_EMOTIONS) — what the app would fetch at startup.
_FAKE_NAMES = ("happy", "sad", "curious")


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@contextmanager
def _control(
    emotions: tuple[str, ...] | None = None,
) -> Iterator[tuple[RobotControl, FakeReachyMini]]:
    """A ``RobotControl`` over a freshly entered ``fake`` api, plus the underlying fake robot to assert
    on. Exits the api on the way out, matching how the app itself uses it. *emotions* plays the
    startup snapshot the app fetches (None: the names are unknown)."""
    api = ReachyMiniApi("fake")
    run(api.__aenter__())
    try:
        robot = api.robot
        assert isinstance(robot, FakeReachyMini)
        yield RobotControl(api, emotions=emotions), robot
    finally:
        run(api.__aexit__(None, None, None))


def _recorded(robot: FakeReachyMini, name: str) -> list[dict[str, Any]]:
    return [args for cmd, args in robot.commands if cmd == name]


def _description(command: Command | Any) -> str:
    """What the model reads as the command's description — a Command's tool description, or a
    bare callable's docstring (what WICA derives it from)."""
    if isinstance(command, Command):
        return command.tool.description
    return command.__doc__ or ""


def test_commands_seed_the_emotion_names_into_play_emotion_and_drop_list_emotions() -> (
    None
):
    """With the startup snapshot, `play_emotion` is registered as a Command whose description names
    every valid emotion (the model reads them where it picks the tool); there is no `list_emotions`
    to spend a turn on; the motor commands come through as bound methods."""
    with _control(_FAKE_NAMES) as (control, _):
        commands = control.commands()

        by_name = {
            (c.name if isinstance(c, Command) else c.__name__): c for c in commands
        }
        assert set(by_name) == {"play_emotion", "get_motors_state", "set_motors_state"}
        description = _description(by_name["play_emotion"])
        for name in _FAKE_NAMES:
            assert name in description
        assert "motors" in description  # the precondition survives the override


def test_commands_without_a_snapshot_fall_back_to_the_docstring() -> None:
    """Unknown names (the library failed to load): `play_emotion` keeps its plain docstring, which
    must not send the model to a `list_emotions` that no longer exists."""
    with _control(None) as (control, _):
        by_name = {
            (c.name if isinstance(c, Command) else c.__name__): c
            for c in control.commands()
        }
        description = _description(by_name["play_emotion"])
        assert "list_emotions" not in description
        assert "Valid names" not in description  # no invented list


def test_emotions_prompt_section_names_every_emotion_and_the_command() -> None:
    section = emotions_prompt_section(_FAKE_NAMES)
    for name in _FAKE_NAMES:
        assert name in section
    assert "play_emotion" in section


def test_seeded_play_emotion_command_routes_to_the_robot() -> None:
    """The description override wraps the same bound method: invoking the Command's tool plays
    the emotion on the robot exactly as the bare method does."""
    with _control(_FAKE_NAMES) as (control, robot):
        run(control.set_motors_state("enabled"))
        play = control.commands()[0]
        assert isinstance(play, Command)

        result = run(play.tool.ainvoke({"name": "curious"}))

        assert "failed" not in str(result)
        assert _recorded(robot, "media.play_sound") == [{"sound_file": "curious.ogg"}]


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
        assert "list_emotions" not in result  # no such command to send the model to
        assert _recorded(robot, "media.play_sound") == []  # nothing was played


def test_play_unknown_emotion_names_the_valid_ones_when_known() -> None:
    """With the startup snapshot, the failure reply repeats the valid names so the model can
    correct itself in the next turn without any round trip."""
    with _control(_FAKE_NAMES) as (control, _):
        run(control.set_motors_state("enabled"))

        result = run(control.play_emotion("nonexistent"))

        assert "failed" in result
        for name in _FAKE_NAMES:
            assert name in result


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
