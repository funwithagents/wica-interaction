"""Functional tests for the robot's people input (specs/reachy-embodiment.md, "People: `PeopleInput`").

Driven over a real `ReachyMiniBridge` on its `fake` backend, its face detector a `ScriptedFaces`
(the bridge's `custom` source): a face shown or hidden on the fake's frames goes through the bridge's
own confirmation, tracks, departures, pin and personal space, so what is asserted here is what the
agent would be told about what the bridge actually published. The producer is driven the way the
lifecycle drives it — `start(deliver)` on a running loop, `stop()` — and the test plays the World,
recording each `deliver(key, value)`.

The bridge runs at real time: a person is confirmed in about 0.3 s and dropped about 2 s after the
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
from wica_interaction.reachy.people import (
    PEOPLE_IN_VIEW,
    PERSON_EVENT,
    PERSON_EVENT_TTL,
    PeopleInput,
    about,
    people_prompt_section,
    side,
    standing_text,
)

# A face as the scripted detector draws it: (x, size). Far enough to be outside the personal space
# (about 0.7 m), and one filling the frame, well inside it.
AHEAD = (0.0, 0.2)
CLOSE = (0.0, 0.9)

ARRIVED = re.compile(
    r"A person came into view: person (\d+), about 0\.\d m away, in front of you\."
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

    def events(self) -> list[str]:
        return [v for v in self.values(PERSON_EVENT) if v is not None]


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
) -> AsyncIterator[tuple[ReachyMiniBridge, PeopleInput, World]]:
    """A started `fake` bridge seeing *faces*, a `PeopleInput` over it (not started: the test starts
    it when its scene is set), and the World it delivers to. Stopped on the way out, input first —
    the lifecycle's order."""
    bridge = ReachyMiniBridge(seeing_config(faces))
    await bridge.start()
    people = PeopleInput(bridge, refresh_s=refresh_s)
    try:
        yield bridge, people, World()
    finally:
        await people.stop()
        await bridge.stop()


async def _arrived(world: World) -> int:
    """Wait for the arrival event; the number it gave the person."""
    assert await _until(lambda: any(ARRIVED.search(e) for e in world.events())), (
        world.writes
    )
    match = next(m for e in world.events() if (m := ARRIVED.search(e)))
    return int(match.group(1))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- The entries ------------------------------------------------------------------------------------


def test_the_two_entries_are_an_event_that_wakes_and_a_standing_picture_that_does_not() -> (
    None
):
    async def scenario() -> None:
        async with _seeing(ScriptedFaces()) as (_, people, _world):
            event, standing = people.entries
            assert event.key == PERSON_EVENT and event.triggers_llm_call
            assert event.ttl == PERSON_EVENT_TTL
            assert standing.key == PEOPLE_IN_VIEW and not standing.triggers_llm_call
            assert standing.ttl is None
            assert (
                event.serialize_fn(None, None) == []
            )  # cleared: omitted from the prompt
            assert event.display_fn is not None and event.display_fn("x") == "👤 x"

    run(scenario())


# --- Arrivals and departures ------------------------------------------------------------------------


def test_a_person_arriving_is_an_event_and_listed_in_view() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
            assert await _until(
                lambda: (
                    world.current(PEOPLE_IN_VIEW)
                    == "Nobody is in view. You are not following anyone with your head."
                )
            ), world.writes
            assert world.events() == []  # nobody there: nothing happened

            faces.show(AHEAD)
            person = await _arrived(world)
            assert await _until(
                lambda: (
                    f"person {person} (about 0."
                    in (world.current(PEOPLE_IN_VIEW) or "")
                )
            ), world.writes
            assert "in front of you" in world.current(PEOPLE_IN_VIEW)

    run(scenario())


def test_a_person_leaving_is_an_event() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
            faces.show(AHEAD)
            person = await _arrived(world)

            faces.hide()
            assert await _until(
                lambda: f"Person {person} left your view." in world.events()
            ), world.writes
            assert world.current(PEOPLE_IN_VIEW).startswith("Nobody is in view.")

    run(scenario())


def test_someone_replacing_someone_is_a_departure_and_an_arrival() -> None:
    """The count stays at one, yet the person changed — what the bridge's users tell apart."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
            faces.show((-0.6, 0.2))
            assert await _until(lambda: len(world.events()) == 1), world.writes
            first = int(re.findall(r"person (\d+)", world.events()[0])[0])

            faces.show(
                (0.6, 0.2)
            )  # far from the first: a new track, the first one missed
            assert await _until(
                lambda: (
                    any(f"Person {first} left your view." in e for e in world.events())
                    and any("to your right" in e for e in world.events())
                )
            ), world.writes

    run(scenario())


def test_people_already_in_view_are_announced_at_start() -> None:
    """Detection runs from the session's start, before the agent does: whoever is already there when
    the input starts is told once, as present, not as arriving."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        faces.show(AHEAD)
        async with _seeing(faces) as (bridge, people, world):
            assert await _until(lambda: bool(bridge.users.value.users))
            await people.start(world.deliver)
            assert await _until(lambda: bool(world.events())), world.writes
            (event,) = world.events()
            assert event.startswith("A person is in view as you start: person ")

    run(scenario())


def test_two_arrivals_together_are_written_together() -> None:
    """Two people confirmed on one report, or a moment apart, reach the step they wake in one write
    — the second never replacing the first."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
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
    event; the standing entry is omitted — not knowing is not *nobody*."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, people, world):
            await people.start(world.deliver)
            faces.show(AHEAD)
            await _arrived(world)

            await bridge.set_face_detection(False)
            assert await _until(lambda: world.current(PEOPLE_IN_VIEW) is None), (
                world.writes
            )
            assert not any("left your view" in e for e in world.events())

    run(scenario())


# --- Whom the head follows, and the personal space -----------------------------------------------


def test_the_standing_entry_says_whom_the_head_follows() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, people, world):
            await people.start(world.deliver)
            faces.show(AHEAD)
            person = await _arrived(world)

            await bridge.start_head_tracking(track_id=person)
            assert await _until(
                lambda: (world.current(PEOPLE_IN_VIEW) or "").endswith(
                    f"Your head is following person {person}, as you chose."
                )
            ), world.writes

            await bridge.start_head_tracking()  # the automatic target, same person
            assert await _until(
                lambda: (world.current(PEOPLE_IN_VIEW) or "").endswith(
                    f"Your head is following person {person}."
                )
            ), world.writes

            await bridge.stop_head_tracking()
            assert await _until(
                lambda: (world.current(PEOPLE_IN_VIEW) or "").endswith(
                    "You are not following anyone with your head."
                )
            ), world.writes

    run(scenario())


def test_coming_very_close_is_one_event_then_a_cooldown() -> None:
    """The followed person crossing into the personal space is told once, with how close; leaning
    out and back in within the cooldown is not told again."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (bridge, people, world):
            await people.start(world.deliver)
            faces.show(AHEAD)
            person = await _arrived(world)
            await bridge.start_head_tracking(track_id=person)

            faces.show(CLOSE)
            assert await _until(
                lambda: any(
                    f"Person {person} came very close to you (about 0." in e
                    for e in world.events()
                )
            ), world.writes
            assert await _until(
                lambda: "very close to you" in (world.current(PEOPLE_IN_VIEW) or "")
            )

            faces.show(AHEAD)
            assert await _until(
                lambda: not bridge.head_tracking.value.in_personal_space
            )
            faces.show(CLOSE)
            assert await _until(lambda: bridge.head_tracking.value.in_personal_space)
            await asyncio.sleep(0.5)
            close = [e for e in world.events() if "came very close" in e]
            assert len(close) == 1, close

    run(scenario())


def test_a_person_moving_a_little_rewrites_nothing() -> None:
    """The standing entry is written when its text changes, not at every observation."""

    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
            faces.show(AHEAD)
            await _arrived(world)
            await asyncio.sleep(0.6)  # the standing entry settled on the person
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
            assert await _until(
                lambda: "to your right" in (world.current(PEOPLE_IN_VIEW) or "")
            ), world.writes[writes:]

    run(scenario())


def test_stop_ends_the_watchers() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        async with _seeing(faces) as (_, people, world):
            await people.start(world.deliver)
            await _until(lambda: bool(world.writes))
            await people.stop()
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


def test_the_standing_text_names_each_person_and_the_head() -> None:
    users = UsersReport(
        users=(_user(3), _user(5, x=-0.8, in_view=False)),
        arrived=(),
        left=(),
        ts=0.0,
        active=True,
    )
    text = standing_text(users, _tracking(track_id=3, in_personal_space=True))
    assert text == (
        "In view: person 3 (about 0.7 m away, in front of you; very close to you), "
        "person 5 (about 0.7 m away, to your left; out of your sight right now). "
        "Your head is following person 3."
    )


def test_a_pinned_person_who_left_leaves_the_head_waiting() -> None:
    users = UsersReport(users=(), arrived=(), left=(), ts=0.0, active=True)
    text = standing_text(
        users, _tracking(attention="watching", track_id=None, pinned_track_id=7)
    )
    assert text == (
        "Nobody is in view. You chose to follow person 7, who is not in view; "
        "your head is waiting for them."
    )


def test_a_pin_on_someone_present_is_about_to_follow_them() -> None:
    """Between the pin and the pinned person's next sighting the head is turning to them — not
    "waiting for someone who left", which they did not."""
    users = UsersReport(users=(_user(7),), arrived=(), left=(), ts=0.0, active=True)
    text = standing_text(
        users, _tracking(attention="watching", track_id=None, pinned_track_id=7)
    )
    assert text is not None
    assert text.endswith("Your head is turning to follow person 7, as you chose.")


def test_not_looking_is_not_nobody() -> None:
    users = UsersReport(users=(), arrived=(), left=(), ts=0.0, active=False)
    assert standing_text(users, _tracking(active=False)) is None


def test_the_persona_section_describes_the_entries_and_the_commands() -> None:
    section = people_prompt_section()
    assert section.startswith("\n\n## People around you")
    for name in (PERSON_EVENT, PEOPLE_IN_VIEW, "start_tracking", "stop_tracking"):
        assert name in section
