"""Functional tests for the robot's users input (specs/reachy-embodiment.md, "Users: `UsersInput`").

Driven over a real `ReachyMiniBridge` on its `fake` backend, its face detector a `ScriptedFaces`
(the bridge's `custom` source): a face shown or hidden on the fake's frames goes through the bridge's
own confirmation, tracks, departures, pin and personal space, so what is asserted here is what the
agent would be told about what the bridge actually published. The producer is driven the way the
lifecycle drives it — `start(deliver)` on a running loop, `stop()` — and the test plays the World,
recording each `deliver(key, value)`; `personal_space`'s trigger rule is asserted on its own, as
the World applies it.

The bridge runs at real time: a user is confirmed in about 0.3 s and dropped about 2 s after the
camera stops seeing them, so the waits below are generous timeouts, never sleeps that must be long
enough. The wordings are also pinned on their own, as pure functions.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from reachy_mini_bridge import (
    Face,
    HeadTrackingReport,
    ReachyMiniBridge,
    User,
    UsersReport,
)

from tests.reachy.fakes import ScriptedFaces, seeing_config
from wica_interaction.reachy.users import (
    PERSONAL_SPACE,
    TRACKED_USER,
    USER_EVENT,
    USER_EVENT_TTL,
    USERS_IN_VIEW,
    PersonalSpace,
    UsersInput,
    about,
    personal_space,
    personal_space_changed,
    personal_space_line,
    side,
    tracked_text,
    users_prompt_section,
    users_text,
)

# A face as the scripted detector draws it: (x, size). Far enough to be outside the personal space
# (about 0.7 m), and one filling the frame, well inside it.
AHEAD = (0.0, 0.2)
CLOSE = (0.0, 0.9)

ARRIVED = re.compile(
    r"A user came into view: user (\d+), about 0\.\d m away, in front of you\."
)


class World:
    """What the producer delivered, in order — the test's stand-in for the World."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, Any]] = []

    def deliver(self, key: str, value: Any) -> None:
        self.writes.append((key, value))

    def values(self, key: str) -> list[Any]:
        return [v for k, v in self.writes if k == key]

    def current(self, key: str) -> Any:
        values = self.values(key)
        return values[-1] if values else None

    def text(self, key: str) -> str:
        value = self.current(key)
        return value if isinstance(value, str) else ""

    def events(self) -> list[str]:
        return [v for v in self.values(USER_EVENT) if v is not None]


async def _until(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@asynccontextmanager
async def _seeing(
    faces: ScriptedFaces, *, refresh_s: float = 0.2
) -> AsyncIterator[tuple[ReachyMiniBridge, UsersInput, World]]:
    """A started `fake` bridge seeing *faces*, a `UsersInput` over it (not started: the test starts
    it when its scene is set), and the World it delivers to. Stopped on the way out, input first —
    the lifecycle's order."""
    bridge = ReachyMiniBridge(seeing_config(faces))
    await bridge.start()
    users = UsersInput(bridge, refresh_s=refresh_s)
    try:
        yield bridge, users, World()
    finally:
        await users.stop()
        await bridge.stop()


async def _arrived(world: World) -> int:
    """Wait for the arrival event; the number it gave the user."""
    assert await _until(lambda: any(ARRIVED.search(e) for e in world.events())), (
        world.writes
    )
    match = next(m for e in world.events() if (m := ARRIVED.search(e)))
    return int(match.group(1))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- The entries ------------------------------------------------------------------------------------


def test_one_entry_per_concept_and_only_events_and_the_personal_space_wake() -> None:
    async def scenario() -> None:
        async with _seeing(ScriptedFaces()) as (_, users, _world):
            by_key = {e.key: e for e in users.entries}
            assert list(by_key) == [
                USER_EVENT,
                USERS_IN_VIEW,
                PERSONAL_SPACE,
                TRACKED_USER,
            ]
            assert by_key[USER_EVENT].triggers_llm_call
            assert by_key[USER_EVENT].ttl == USER_EVENT_TTL
            assert by_key[PERSONAL_SPACE].triggers_llm_call
            assert by_key[PERSONAL_SPACE].trigger_condition_fn is personal_space_changed
            for standing in (USERS_IN_VIEW, TRACKED_USER):
                assert not by_key[standing].triggers_llm_call
                assert by_key[standing].ttl is None
            # Cleared: omitted from the prompt.
            assert by_key[USER_EVENT].serialize_fn(None, None) == []
            assert by_key[PERSONAL_SPACE].serialize_fn(None, None) == []

    run(scenario())


# --- Arrivals and departures ------------------------------------------------------------------------


def test_a_user_arriving_is_an_event_and_listed_in_view() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            assert await _until(
                lambda: world.current(USERS_IN_VIEW) == "Nobody is in view."
            ), world.writes
            assert (
                world.current(TRACKED_USER)
                == "You are not following anyone with your head."
            )
            assert world.events() == []  # nobody there: nothing happened

            faces.show(AHEAD)
            user = await _arrived(world)
            assert await _until(
                lambda: f"user {user} (about 0." in world.text(USERS_IN_VIEW)
            ), world.writes
            assert "in front of you" in world.text(USERS_IN_VIEW)

    run(scenario())


def test_a_user_leaving_is_an_event() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            user = await _arrived(world)

            faces.hide()
            assert await _until(
                lambda: f"User {user} left your view." in world.events()
            ), world.writes
            assert world.current(USERS_IN_VIEW) == "Nobody is in view."

    run(scenario())


def test_someone_replacing_someone_is_a_departure_and_an_arrival() -> None:
    """The count stays at one, yet the user changed — what the bridge's users tell apart."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show((-0.6, 0.2))
            assert await _until(lambda: len(world.events()) == 1), world.writes
            first = int(re.findall(r"user (\d+)", world.events()[0])[0])

            faces.show(
                (0.6, 0.2)
            )  # far from the first: a new track, the first one missed
            assert await _until(
                lambda: (
                    any(f"User {first} left your view." in e for e in world.events())
                    and any("to your right" in e for e in world.events())
                )
            ), world.writes

    run(scenario())


def test_users_already_in_view_are_announced_at_start() -> None:
    """Detection runs from the session's start, before the agent does: whoever is already there when
    the input starts is told once, as present, not as arriving."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        faces.show(AHEAD)
        async with _seeing(faces) as (bridge, users, world):
            assert await _until(lambda: bool(bridge.users.value.users))
            await users.start(world.deliver)
            assert await _until(lambda: bool(world.events())), world.writes
            (event,) = world.events()
            assert event.startswith("A user is in view as you start: user ")

    run(scenario())


def test_two_arrivals_together_are_written_together() -> None:
    """Two users confirmed on one report, or a moment apart, reach the step they wake in one write —
    the second never replacing the first."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show((-0.6, 0.2))
            await asyncio.sleep(0.1)
            faces.show((-0.6, 0.2), (0.6, 0.2))
            assert await _until(
                lambda: any(
                    "to your left" in e and "to your right" in e for e in world.events()
                )
            ), world.writes

    run(scenario())


def test_detection_stopping_says_nobody_left() -> None:
    """Every user leaves with `"stopped"` when detection stops: nobody was seen walking away, so no
    event; the users entry is omitted — not knowing is not *nobody*."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            await _arrived(world)

            await bridge.set_face_detection(False)
            assert await _until(lambda: world.current(USERS_IN_VIEW) is None), (
                world.writes
            )
            assert not any("left your view" in e for e in world.events())

    run(scenario())


# --- Whom the head follows, and the personal space -----------------------------------------------


def test_the_tracked_user_says_whom_the_head_follows() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            user = await _arrived(world)

            await bridge.start_head_tracking(track_id=user)
            assert await _until(
                lambda: (
                    world.current(TRACKED_USER)
                    == f"Your head is following user {user}, as you chose."
                )
            ), world.writes

            await bridge.start_head_tracking()  # the automatic target, same user
            assert await _until(
                lambda: (
                    world.current(TRACKED_USER)
                    == f"Your head is following user {user}."
                )
            ), world.writes

            await bridge.stop_head_tracking()
            assert await _until(
                lambda: (
                    world.current(TRACKED_USER)
                    == "You are not following anyone with your head."
                )
            ), world.writes

    run(scenario())


def test_the_personal_space_changes_when_the_followed_user_comes_in_and_steps_out() -> (
    None
):
    """Judged only while the head follows someone: tracking on writes "nobody" (which wakes no one),
    the followed user coming very close writes them inside (a change: it wakes), stepping back out
    writes nobody again (a change: it wakes); tracking off omits the entry."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            user = await _arrived(world)
            assert world.values(PERSONAL_SPACE) == []  # not judged: tracking off

            await bridge.start_head_tracking(track_id=user)
            assert await _until(
                lambda: world.current(PERSONAL_SPACE) == PersonalSpace(None)
            ), world.writes

            faces.show(CLOSE)
            assert await _until(
                lambda: getattr(world.current(PERSONAL_SPACE), "user", None) == user
            ), world.writes
            inside = world.current(PERSONAL_SPACE)
            assert inside.distance_m is not None and inside.distance_m < 0.4

            faces.show(AHEAD)
            assert await _until(
                lambda: world.current(PERSONAL_SPACE) == PersonalSpace(None)
            ), world.writes

            # What the World would have woken the agent on: each change of who is inside.
            values = world.values(PERSONAL_SPACE)
            woken = [
                new
                for old, new in zip([None, *values], values, strict=False)
                if personal_space_changed(old, new)
            ]
            assert [v.user for v in woken] == [user, None]

            await bridge.stop_head_tracking()
            assert await _until(lambda: world.current(PERSONAL_SPACE) is None)

    run(scenario())


def test_a_user_moving_a_little_rewrites_nothing() -> None:
    """The standing entries are written when their values change, not at every observation."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            await _arrived(world)
            await asyncio.sleep(0.6)  # the standing entries settled on the user
            writes = len(world.writes)

            for x in (0.02, 0.05, 0.08, 0.05):
                faces.show((x, 0.2))
                await asyncio.sleep(0.15)
            await asyncio.sleep(0.5)  # past two refreshes
            assert len(world.writes) == writes, world.writes[writes:]

            for x in (
                0.15,
                0.25,
                0.35,
                0.45,
                0.55,
                0.65,
            ):  # step by step: one track, now right
                faces.show((x, 0.2))
                await asyncio.sleep(0.15)
            assert await _until(lambda: "to your right" in world.text(USERS_IN_VIEW)), (
                world.writes[writes:]
            )

    run(scenario())


def test_stop_ends_the_watchers() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            await _until(lambda: bool(world.writes))
            await users.stop()
            writes = len(world.writes)
            faces.show(AHEAD)
            await asyncio.sleep(1.0)  # well past a confirmation
            assert len(world.writes) == writes

    run(scenario())


# --- The wordings ---------------------------------------------------------------------------------


def _face(x: float = 0.0, distance_m: float | None = 0.7) -> Face:
    return Face(x=x, y=0.0, roll=None, size=0.2, track_id=3, distance_m=distance_m)


def _user(track_id: int, *, x: float = 0.0, in_view: bool = True) -> User:
    return User(
        track_id=track_id,
        seen=in_view,
        in_view=in_view,
        last_seen_ts=0.0,
        missing_s=0.0,
        face=Face(x=x, y=0.0, roll=None, size=0.2, track_id=track_id, distance_m=0.66),
    )


def _report(*users: User, active: bool = True) -> UsersReport:
    return UsersReport(users=users, arrived=(), left=(), ts=0.0, active=active)


def _tracking(**fields: Any) -> HeadTrackingReport:
    base: dict[str, Any] = {
        "active": True,
        "focus": False,
        "attention": "engaged",
        "track_id": None,
        "ts": 0.0,
    }
    return HeadTrackingReport(**{**base, **fields})


def test_a_distance_is_said_the_way_a_person_would() -> None:
    assert about(0.66) == "about 0.7 m away"
    assert about(0.04) == "about 0.1 m away"
    assert about(1.3) == "about 1.5 m away"
    assert about(2.1) == "about 2.0 m away"
    assert about(None) is None


def test_the_side_is_the_robots() -> None:
    assert side(_face(x=-0.8)) == "to your left"
    assert side(_face(x=0.1)) == "in front of you"
    assert side(_face(x=0.8)) == "to your right"


def test_the_users_text_names_each_user_and_where() -> None:
    text = users_text(_report(_user(3), _user(5, x=-0.8, in_view=False)))
    assert text == (
        "In view: user 3 (about 0.7 m away, in front of you), "
        "user 5 (about 0.7 m away, to your left; out of your sight right now)."
    )


def test_not_looking_is_not_nobody() -> None:
    assert users_text(_report(active=False)) is None


def test_the_tracked_text_for_each_state_of_the_head() -> None:
    present = frozenset({7})
    assert tracked_text(_tracking(active=False), present) == (
        "You are not following anyone with your head."
    )
    assert tracked_text(_tracking(track_id=7), present) == (
        "Your head is following user 7."
    )
    assert tracked_text(_tracking(track_id=7, pinned_track_id=7), present) == (
        "Your head is following user 7, as you chose."
    )
    # Between the pin and the pinned user's next sighting the head is turning to them…
    assert tracked_text(
        _tracking(attention="watching", pinned_track_id=7), present
    ) == ("Your head is turning to follow user 7, as you chose.")
    # …and once they left, it waits.
    assert tracked_text(
        _tracking(attention="watching", pinned_track_id=7), frozenset()
    ) == (
        "You chose to follow user 7, who is not in view; your head is waiting for them."
    )
    assert tracked_text(_tracking(attention="watching"), present).startswith(
        "Your head will follow whoever comes in front of you"
    )


def test_the_personal_space_is_judged_only_while_the_head_follows() -> None:
    users = _report(_user(3))
    assert personal_space(users, _tracking(active=False)) is None
    assert personal_space(users, _tracking(track_id=3)) == PersonalSpace(None)
    assert personal_space(users, _tracking(track_id=3, in_personal_space=True)) == (
        PersonalSpace(3, 0.7)
    )
    assert personal_space_line(PersonalSpace(3, 0.3)) == (
        "User 3 is in your personal space, very close to you (about 0.3 m away)."
    )
    assert (
        personal_space_line(PersonalSpace(None)) == "Nobody is in your personal space."
    )


def test_the_personal_space_wakes_on_who_is_inside_changing() -> None:
    assert personal_space_changed(
        None, PersonalSpace(3, 0.3)
    )  # tracking on, someone inside
    assert personal_space_changed(
        PersonalSpace(None), PersonalSpace(3, 0.3)
    )  # entering
    assert personal_space_changed(
        PersonalSpace(3, 0.3), PersonalSpace(None)
    )  # stepping out
    assert personal_space_changed(
        PersonalSpace(3), PersonalSpace(5)
    )  # another took the place
    assert not personal_space_changed(
        None, PersonalSpace(None)
    )  # tracking on, nobody inside
    assert not personal_space_changed(
        PersonalSpace(3, 0.3), PersonalSpace(3, 0.2)
    )  # moving
    assert not personal_space_changed(PersonalSpace(3), None)  # tracking off


def test_the_persona_section_describes_the_entries_and_the_commands() -> None:
    section = users_prompt_section()
    assert section.startswith("\n\n## The users around you")
    for name in (
        USER_EVENT,
        USERS_IN_VIEW,
        PERSONAL_SPACE,
        TRACKED_USER,
        "start_tracking",
        "stop_tracking",
    ):
        assert name in section
