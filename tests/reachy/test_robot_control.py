"""Functional tests for ``RobotControl`` — driven against the bridge's ``fake`` backend (no daemon,
no hardware, no network).

Tests drive the public ``RobotControl`` coroutines the way WICA's Commands will, using a real,
**started** ``ReachyMiniBridge("fake")`` so the *actual* verb wiring runs: the fake honors motor-state
changes and records the moves it is handed, so the motor precondition is genuinely enforced (not
mocked). They assert on the non-raising Command contract — a readable string in every case, never an
exception — and on the side effects the fake records, rather than on exact wording. See
specs/reachy-embodiment.md ("Testing").

The verbs use ``asyncio.to_thread`` internally, so each coroutine is driven with ``asyncio.run`` to
provide a running loop (no async-test plugin required). ``bridge.robot`` is only available while running
(``reachy-mini-bridge``'s start/stop lifecycle — construction never connects), so each test starts
the ``fake`` bridge itself and exits it in a ``finally``; the ``fake`` backend needs no daemon, so this
stays offline and fast. ``play_emotion`` plays through the bridge's motion loop and awaits until the
trajectory ends (see reachy-mini-bridge specs/motion.md, "Emotions through the loop"), so a successful
play is observed here as its sidecar sound reaching the fake's media layer (``media.play_sound``), not
as a discrete "move" command.

The tracking pair runs over a ``fake`` bridge that sees people: its face detector a scripted one
(``ScriptedFaces``, the bridge's ``custom`` source), so the pin is the bridge's own, accepted only for
a user it holds present.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

from reachy_mini_bridge import ReachyMiniBridge
from reachy_mini_bridge.fake_reachy_mini import FakeReachyMini
from wica import Command

from tests.reachy.fakes import ScriptedFaces, seeing_config
from wica_interaction.reachy.robot_control import (
    RobotControl,
    emotions_prompt_section,
)

# The fake's stubbed library (see ReachyMiniBridge._FAKE_EMOTIONS) — what the app would fetch at startup.
_FAKE_NAMES = ("happy", "sad", "curious")


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@contextmanager
def _control(
    emotions: tuple[str, ...] | None = None,
) -> Iterator[tuple[RobotControl, FakeReachyMini]]:
    """A ``RobotControl`` over a freshly started ``fake`` bridge, plus the underlying fake robot to assert
    on. Stops the bridge on the way out, matching how the app itself uses it. *emotions* plays the
    startup snapshot the app fetches (None: the names are unknown)."""
    bridge = ReachyMiniBridge("fake")
    run(bridge.start())
    try:
        robot = bridge.robot
        assert isinstance(robot, FakeReachyMini)
        yield RobotControl(bridge, emotions=emotions), robot
    finally:
        run(bridge.stop())


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


# --- Following a person: start_tracking / stop_tracking (specs/reachy-embodiment.md) ---------------


def _names(control: RobotControl) -> list[str]:
    return [
        c.name if isinstance(c, Command) else c.__name__ for c in control.commands()
    ]


def test_the_tracking_pair_is_registered_only_when_the_robot_sees_people() -> None:
    with _control(_FAKE_NAMES) as (control, _):
        assert "start_tracking" not in _names(control)
    bridge = ReachyMiniBridge("fake")
    names = _names(RobotControl(bridge, emotions=_FAKE_NAMES, tracking=True))
    assert names[-2:] == ["start_tracking", "stop_tracking"]


async def _until(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_start_tracking_pins_a_person_present_and_stop_tracking_lets_go() -> None:
    """The agent's choice of whom reaches the bridge's head tracker: a number pins that user, none
    returns to the automatic target, `stop_tracking` turns tracking off; the replies say so."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        bridge = ReachyMiniBridge(seeing_config(faces))
        await bridge.start()
        try:
            control = RobotControl(bridge, tracking=True)
            faces.show((0.0, 0.2))
            assert await _until(lambda: bool(bridge.users.value.users))
            person = bridge.users.value.users[0].track_id

            reply = await control.start_tracking(person)
            assert reply == f"OK — your head now follows person {person}."
            tracking = bridge.head_tracking.value
            assert tracking.active and tracking.pinned_track_id == person
            assert await _until(lambda: bridge.head_tracking.value.track_id == person)

            reply = await control.start_tracking()
            assert reply == "OK — your head now follows whoever is in front of you."
            assert bridge.head_tracking.value.pinned_track_id is None

            reply = await control.stop_tracking()
            assert reply == "OK — your head no longer follows anyone."
            assert not bridge.head_tracking.value.active
            # Detection keeps running for the people input: the person is still present.
            assert [u.track_id for u in bridge.users.value.users] == [person]
        finally:
            await bridge.stop()

    run(scenario())


def test_following_someone_who_left_is_answered_not_raised() -> None:
    """The person left between the step that read them and the call: the bridge refuses the pin,
    the agent reads why, tracking is left as it was."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        bridge = ReachyMiniBridge(seeing_config(faces))
        await bridge.start()
        try:
            control = RobotControl(bridge, tracking=True)
            reply = await control.start_tracking(42)
            assert reply == "Can't follow person 42: they are not in view."
            assert not bridge.head_tracking.value.active
        finally:
            await bridge.stop()

    run(scenario())


def test_tracking_without_a_detector_is_reported_not_raised() -> None:
    bridge = ReachyMiniBridge("fake")
    run(bridge.start())
    try:
        reply = run(RobotControl(bridge, tracking=True).start_tracking())
        assert reply.startswith("start_tracking failed:")
    finally:
        run(bridge.stop())
