"""Functional tests for the robot's users input (specs/reachy-embodiment.md, "Users: `UsersInput`").

Driven over a real `ReachyMiniBridge` on its `fake` backend, its face detector a `ScriptedFaces`
(the bridge's `custom` source): a face shown or hidden on the fake's frames goes through the bridge's
own confirmation, tracks, departures, pin and personal space, so what is asserted here is what the
agent would be told about what the bridge actually published. The producer is driven the way the
lifecycle drives it — `start(deliver)` on a running loop, `stop()` — and the test plays the World,
recording each `deliver(key, value)` and, as the World does, which writes wake the agent (each
entry's `triggers_llm_call` and `trigger_condition_fn`, against the value it replaces).

The bridge runs at real time: a user is confirmed in about 0.3 s and dropped about 2 s after the
camera stops seeing them, so the waits below are generous timeouts, never sleeps that must be long
enough. The wordings are also pinned on their own, as pure functions.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from reachy_mini_bridge import (
    Face,
    HeadTrackingReport,
    ReachyMiniBridge,
    User,
    UsersReport,
)
from wica import TextPart

from tests.reachy.fakes import ScriptedFaces, seeing_config
from wica_interaction.embodiment import InputEntry
from wica_interaction.reachy.users import (
    PERSONAL_SPACE,
    TRACKED_USER,
    USER_EVENT,
    USER_EVENT_TTL,
    USERS_IN_VIEW,
    PersonalSpace,
    TrackedUser,
    UserChange,
    UserInView,
    UsersInput,
    events_text,
    personal_space,
    personal_space_changed,
    personal_space_line,
    rounded,
    side_of,
    tracked_line,
    tracked_user,
    user_event_wakes,
    users_in_view,
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
    """What the producer delivered, in order — the test's stand-in for the World. It keeps the
    values (objects, as the World does) and reads them as the prompt does, through each entry's
    `serialize_fn`."""

    def __init__(self, entries: Sequence[InputEntry]) -> None:
        self.writes: list[tuple[str, Any]] = []
        self.woke: list[tuple[str, Any]] = []  # the writes that woke the agent
        self._serialize = {e.key: e.serialize_fn for e in entries}
        self._entries = {e.key: e for e in entries}

    def deliver(self, key: str, value: Any) -> None:
        entry = self._entries[key]
        old = self.current(key)
        if entry.triggers_llm_call and (
            entry.trigger_condition_fn is None or entry.trigger_condition_fn(old, value)
        ):
            self.woke.append((key, value))
        self.writes.append((key, value))

    def woken(self, key: str = USER_EVENT) -> list[str]:
        """The rendered writes of *key* that woke the agent."""
        return [self.render(k, v) for k, v in self.woke if k == key]

    def values(self, key: str) -> list[Any]:
        return [v for k, v in self.writes if k == key]

    def current(self, key: str) -> Any:
        values = self.values(key)
        return values[-1] if values else None

    def render(self, key: str, value: Any) -> str:
        """*value* as the prompt words it ("" when the entry would be omitted)."""
        return "".join(
            part.text
            for part in self._serialize[key](value, None)
            if isinstance(part, TextPart)
        )

    def text(self, key: str) -> str:
        return self.render(key, self.current(key))

    def events(self) -> list[str]:
        return [self.render(USER_EVENT, v) for v in self.values(USER_EVENT) if v]


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
        yield bridge, users, World(users.entries)
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


def test_one_entry_per_concept_and_only_the_events_wake() -> None:
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
            assert by_key[USER_EVENT].trigger_condition_fn is user_event_wakes
            assert by_key[USER_EVENT].ttl == USER_EVENT_TTL
            for standing in (USERS_IN_VIEW, PERSONAL_SPACE, TRACKED_USER):
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
                lambda: world.text(USERS_IN_VIEW) == "Nobody is in view."
            ), world.writes
            assert (
                world.text(TRACKED_USER)
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


def test_a_user_nobody_follows_leaving_is_no_event() -> None:
    """Not the tracked user: their departure changes who is in view, and nothing else — no event a
    step woken by another input could read as news."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            await _arrived(world)
            events = len(world.values(USER_EVENT))

            faces.hide()
            assert await _until(
                lambda: world.text(USERS_IN_VIEW) == "Nobody is in view."
            ), world.writes
            assert len(world.values(USER_EVENT)) == events, world.values(USER_EVENT)

    run(scenario())


def test_someone_replacing_someone_nobody_follows_is_announced_as_an_arrival() -> None:
    """The count stays at one, yet the user changed — what the bridge's users tell apart. Nobody
    is followed: the newcomer is an event, the departure only leaves `users_in_view`."""

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
                    any("to your right" in e for e in world.events())
                    and f"user {first} " not in world.text(USERS_IN_VIEW)
                )
            ), world.writes
            assert not any("left your view" in e for e in world.events())

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
                    world.text(TRACKED_USER)
                    == f"Your head is following user {user}, as you chose."
                )
            ), world.writes

            await bridge.start_head_tracking()  # the automatic target, same user
            assert await _until(
                lambda: (
                    world.text(TRACKED_USER) == f"Your head is following user {user}."
                )
            ), world.writes

            await bridge.stop_head_tracking()
            assert await _until(
                lambda: (
                    world.text(TRACKED_USER)
                    == "You are not following anyone with your head."
                )
            ), world.writes

    run(scenario())


def test_the_personal_space_changes_when_the_followed_user_comes_in_and_steps_out() -> (
    None
):
    """Judged only while the head follows someone: tracking on writes "nobody", the followed user
    coming very close writes them inside, saying they just came in, stepping back out writes nobody
    again, saying they just stepped out — none of it waking the agent; tracking off omits the
    entry."""

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

            assert world.woken(PERSONAL_SPACE) == []  # read, never woken on
            # Each change of who is inside says what just changed; the settling rewrite does not.
            values = world.values(PERSONAL_SPACE)
            changed = [
                new
                for old, new in zip([None, *values], values, strict=False)
                if personal_space_changed(old, new)
            ]
            assert [v.user for v in changed] == [user, None]
            assert [(v.change, v.left) for v in changed] == [
                ("came_in", None),
                ("stepped_out", user),
            ]
            assert world.render(PERSONAL_SPACE, changed[0]).startswith(
                f"User {user} just came into your personal space"
            )
            assert world.render(PERSONAL_SPACE, changed[1]) == (
                f"User {user} just stepped back out of your personal space; nobody is in it now."
            )
            assert world.current(PERSONAL_SPACE).change is None  # settled

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


# --- What wakes the agent ---------------------------------------------------------------------------
#
# Only whom the robot attends to: a user arriving while nobody is tracked, and the tracked user
# (followed or pinned) leaving. Every other change is written and wakes nothing.

RIGHT = (0.6, 0.2)


def _user_on_the_right(world: World) -> int | None:
    """The user `users_in_view` lists to the robot's right, if any."""
    value = world.current(USERS_IN_VIEW)
    for user in value or ():
        if user.side == "right":
            return user.user
    return None


def test_an_arrival_wakes_the_agent_while_nobody_is_tracked() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            user = await _arrived(world)
            assert any(f"user {user}" in e for e in world.woken()), world.woke

    run(scenario())


def test_the_automatic_target_grabbing_a_newcomer_still_wakes_the_agent() -> None:
    """With tracking on and nobody followed, the tracker may follow a newcomer before the report
    saying they arrived is read: that newcomer is not *somebody tracked* already."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await bridge.start_head_tracking()
            await users.start(world.deliver)
            faces.show(AHEAD)
            user = await _arrived(world)
            assert any(f"user {user}" in e for e in world.woken()), world.woke

    run(scenario())


def test_around_the_followed_user_comings_and_goings_are_no_events() -> None:
    """The head follows user A: B arriving and B leaving change who is in view, and are no event
    at all (nothing to wake on, nothing a later step could read as news); A leaving wakes the
    agent, worded as the user it followed."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            followed = await _arrived(world)
            await bridge.start_head_tracking(track_id=followed)
            assert await _until(
                lambda: f"following user {followed}" in world.text(TRACKED_USER)
            ), world.writes
            events = len(world.values(USER_EVENT))

            faces.show(AHEAD, RIGHT)
            assert await _until(lambda: _user_on_the_right(world) is not None), (
                world.writes
            )
            faces.show(AHEAD)
            assert await _until(lambda: _user_on_the_right(world) is None), world.writes
            assert len(world.values(USER_EVENT)) == events, world.values(USER_EVENT)

            faces.hide()
            line = f"User {followed}, whom your head was following, left your view."
            assert await _until(lambda: any(line in e for e in world.woken())), (
                world.writes
            )

    run(scenario())


def test_an_arrival_wakes_the_agent_once_the_pinned_user_has_left() -> None:
    """The bridge keeps a pin on a user who left, the head waiting on them: nobody present is
    followed, so a newcomer wakes the agent."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, users, world):
            await users.start(world.deliver)
            faces.show(AHEAD)
            pinned = await _arrived(world)
            await bridge.start_head_tracking(track_id=pinned)
            faces.hide()
            assert await _until(
                lambda: any(f"User {pinned}, whom" in e for e in world.woken())
            ), world.writes

            faces.show(AHEAD)
            assert await _until(
                lambda: any(
                    "came into view" in e and f"user {pinned}," not in e
                    for e in world.woken()
                )
            ), world.writes

    run(scenario())


def test_users_in_view_at_start_wake_the_agent() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        faces.show(AHEAD)
        async with _seeing(faces) as (bridge, users, world):
            assert await _until(lambda: bool(bridge.users.value.users))
            await users.start(world.deliver)
            assert await _until(lambda: bool(world.woken())), world.writes
            assert world.woken()[0].startswith("A user is in view as you start")

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


def test_a_distance_is_kept_as_coarsely_as_it_is_said() -> None:
    assert rounded(0.66) == 0.7
    assert rounded(0.04) == 0.1
    assert rounded(1.3) == 1.5
    assert rounded(2.1) == 2.0
    assert rounded(None) is None


def test_the_side_is_the_robots() -> None:
    assert side_of(_face(x=-0.8)) == "left"
    assert side_of(_face(x=0.1)) == "front"
    assert side_of(_face(x=0.8)) == "right"


def test_the_world_holds_who_is_present_and_the_prompt_words_it() -> None:
    present = users_in_view(_report(_user(3), _user(5, x=-0.8, in_view=False)))
    assert present == (
        UserInView(3, "front", 0.7),
        UserInView(5, "left", 0.7, in_view=False),
    )
    assert present is not None
    assert users_text(present) == (
        "In view: user 3 (about 0.7 m away, in front of you), "
        "user 5 (about 0.7 m away, to your left; out of your sight right now)."
    )
    assert users_text(()) == "Nobody is in view."
    # Not looking is not nobody: the entry is omitted.
    assert users_in_view(_report(active=False)) is None


def test_the_events_are_worded_one_line_each() -> None:
    events = (
        UserChange("arrived", 3, "front", 0.7),
        UserChange("present", 4, "right", 1.5),
        UserChange("left", 5),
        UserChange("left", 6, wakes=True),  # the tracked user
    )
    assert events_text(events) == (
        "A user came into view: user 3, about 0.7 m away, in front of you.\n"
        "A user is in view as you start: user 4, about 1.5 m away, to your right.\n"
        "User 5 left your view.\n"
        "User 6, whom your head was following, left your view."
    )


def test_a_write_wakes_on_a_waking_change_it_brings() -> None:
    welcome = UserChange("arrived", 3, "front", 0.7, wakes=True)
    quiet = UserChange("arrived", 4, "right", 0.7)
    assert user_event_wakes(None, (welcome,))
    assert not user_event_wakes(None, (quiet,))
    # A quiet change appended to a waking one already written does not wake again…
    assert not user_event_wakes((welcome,), (welcome, quiet))
    # …a waking one appended does.
    gone = UserChange("left", 5, wakes=True)
    assert user_event_wakes((quiet,), (quiet, gone))
    assert not user_event_wakes((welcome,), None)  # the TTL clearing it


def test_the_world_holds_whom_the_head_follows_and_the_prompt_words_it() -> None:
    present = frozenset({7})
    cases = [
        (
            _tracking(active=False),
            TrackedUser(active=False),
            "You are not following anyone with your head.",
        ),
        (
            _tracking(track_id=7),
            TrackedUser(True, user=7),
            "Your head is following user 7.",
        ),
        (
            _tracking(track_id=7, pinned_track_id=7),
            TrackedUser(True, 7, 7, True),
            "Your head is following user 7, as you chose.",
        ),
        # Between the pin and the pinned user's next sighting the head is turning to them…
        (
            _tracking(attention="watching", pinned_track_id=7),
            TrackedUser(True, None, 7, True),
            "Your head is turning to follow user 7, as you chose.",
        ),
    ]
    for report, value, line in cases:
        assert tracked_user(report, present) == value
        assert tracked_line(value) == line
    # …and once they left, it waits.
    gone = tracked_user(_tracking(attention="watching", pinned_track_id=7), frozenset())
    assert tracked_line(gone) == (
        "You chose to follow user 7, who is not in view; your head is waiting for them."
    )
    assert tracked_line(
        tracked_user(_tracking(attention="watching"), present)
    ).startswith("Your head will follow whoever comes in front of you")


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
    assert personal_space_line(PersonalSpace(3, 0.3, change="came_in")) == (
        "User 3 just came into your personal space: they are very close to you "
        "(about 0.3 m away)."
    )
    assert personal_space_line(PersonalSpace(None, change="stepped_out", left=3)) == (
        "User 3 just stepped back out of your personal space; nobody is in it now."
    )
    # Settling a change is not a change of who is inside.
    assert not personal_space_changed(
        PersonalSpace(3, 0.3, change="came_in"), PersonalSpace(3, 0.3)
    )
    assert (
        personal_space_line(PersonalSpace(None)) == "Nobody is in your personal space."
    )


def test_the_personal_space_changes_on_who_is_inside() -> None:
    assert personal_space_changed(
        None, PersonalSpace(3, 0.3)
    )  # tracking starts on a user already inside: the agent's first news of it
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
