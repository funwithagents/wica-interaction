"""The people in front of the Reachy Mini, as the agent's input: ``PeopleInput``.

The robot's own perception (specs/reachy-embodiment.md, "People: ``PeopleInput``"), delivered through
the embodiment's ``inputs()`` — the mirror of its Commands (specs/embodiment.md): this module
*describes* two World entries and *produces* their values; the lifecycle registers the entries and
hands it a guarded ``deliver``. It never sees the World.

Who is present is the bridge's decision (reachy-mini-bridge specs/vision/user_perception.md, "The
users present"): a face confirmed over 0.3 s, a person kept while a move of the robot's own looks
away, dropped after 2 s missing where the camera looks (``"left"``) or 20 s unseen (``"unseen"``).
Whom the head follows, and whether that person is inside the personal space, is its head
tracker's (specs/motion/head_tracking.md). This module only *words* it:

- ``person_event`` — what just happened, one-shot like ``speech_input``: a person came into view,
  left it, came very close; it wakes the agent.
- ``people_in_view`` — who is present now and whom the head follows: the app's one standing entry
  (specs/app.md, "World, Commands, persona"), never waking the agent, rewritten only when its text
  changes.

Every line says what was perceived and no more: a number is a track, not a person — the same person
coming back after a break gets a new one — so nothing here says *the same person*, *a new user* or a
name.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from reachy_mini_bridge import (
    Face,
    HeadTrackingReport,
    ReachyMiniBridge,
    User,
    UsersReport,
)
from wica import Content, TextPart

from wica_interaction.embodiment import Deliver, InputEntry

logger = logging.getLogger(__name__)

PERSON_EVENT = "person_event"
PEOPLE_IN_VIEW = "people_in_view"

# A person event is one-shot, like an utterance: the step it wakes observes it, then the World clears
# it (specs/app.md, "Inputs are transient").
PERSON_EVENT_TTL = timedelta(seconds=2)
# A second event this soon after the first is written *with* it, so two arrivals on one coalescing
# window both reach the step they wake rather than the second replacing the first.
EVENT_JOIN_S = 0.5
# Someone leaning in and out of the personal space is remarked on once, not at every crossing.
PERSONAL_SPACE_COOLDOWN_S = 30.0
# How often the standing entry is re-rendered between changes (a person moving to the other side);
# it is written only when its text changed.
STANDING_REFRESH_S = 1.0

# The side a face is on, from its normalised x in [-1, 1]: the outer thirds are left and right. The
# robot's camera is not mirrored, so the image's right is the robot's right.
_SIDE_THRESHOLD = 1.0 / 3.0


# --- The wordings --------------------------------------------------------------------------------


def side(face: Face) -> str:
    """Where *face* was, seen from the robot: ``to your left``, ``in front of you``, ``to your right``."""
    if face.x < -_SIDE_THRESHOLD:
        return "to your left"
    if face.x > _SIDE_THRESHOLD:
        return "to your right"
    return "in front of you"


def about(distance_m: float | None) -> str | None:
    """A distance as a person would say it: to 0.1 m under a metre, to 0.5 m from there; ``None``
    when the bridge has none (no camera model, a detector without a size)."""
    if distance_m is None or not math.isfinite(distance_m) or distance_m <= 0:
        return None
    if distance_m < 0.95:
        return f"about {max(round(distance_m, 1), 0.1):.1f} m away"
    return f"about {round(distance_m * 2) / 2:.1f} m away"


def place(face: Face) -> str:
    """How far and where: ``about 0.7 m away, in front of you``."""
    distance = about(face.distance_m)
    return f"{distance}, {side(face)}" if distance else side(face)


def arrived_line(user: User) -> str:
    return f"A person came into view: person {user.track_id}, {place(user.face)}."


def present_at_start_line(user: User) -> str:
    return (
        f"A person is in view as you start: person {user.track_id}, {place(user.face)}."
    )


def left_line(track_id: int) -> str:
    return f"Person {track_id} left your view."


def close_line(track_id: int, distance_m: float | None) -> str:
    distance = about(distance_m)
    if distance is None:
        return f"Person {track_id} came very close to you."
    return f"Person {track_id} came very close to you ({distance})."


def standing_text(users: UsersReport, tracking: HeadTrackingReport) -> str | None:
    """The standing entry: who is present and whom the head follows; ``None`` (the entry omitted)
    while detection is not running — not knowing is not *nobody*."""
    if not users.active:
        return None
    if users.users:
        people = ", ".join(_person(u, tracking) for u in users.users)
        seen = f"In view: {people}."
    else:
        seen = "Nobody is in view."
    present = frozenset(u.track_id for u in users.users)
    return f"{seen} {_following(tracking, present)}"


def _person(user: User, tracking: HeadTrackingReport) -> str:
    details = [place(user.face)]
    if tracking.in_personal_space and tracking.track_id == user.track_id:
        details.append("very close to you")
    if not user.in_view:
        details.append("out of your sight right now")
    return f"person {user.track_id} ({'; '.join(details)})"


def _following(tracking: HeadTrackingReport, present: frozenset[int]) -> str:
    if not tracking.active:
        return "You are not following anyone with your head."
    if tracking.track_id is not None:
        if tracking.pinned_track_id == tracking.track_id:
            return f"Your head is following person {tracking.track_id}, as you chose."
        return f"Your head is following person {tracking.track_id}."
    pinned = tracking.pinned_track_id
    if pinned is not None:
        # The bridge reports the pin from the call and the follow from the pinned person's next
        # sighting: present means "about to", gone means the head waits on someone who left.
        if pinned in present:
            return f"Your head is turning to follow person {pinned}, as you chose."
        return (
            f"You chose to follow person {pinned}, who is not in view; "
            "your head is waiting for them."
        )
    return "Your head will follow whoever comes in front of you."


def _serialize(value: Any, previous: Any) -> Content:
    """Both entries hold their rendered text; ``None`` renders nothing (the entry is omitted)."""
    if value is None:
        return []
    return [TextPart(str(value))]


def people_prompt_section() -> str:
    """The persona addendum describing the people the robot sees, appended at startup when the
    people input is up (specs/reachy-embodiment.md, "Persona"). Starts with a blank line so it reads
    as a new section after the persona's last line."""
    return (
        "\n\n## People around you\n\n"
        "A camera in your head sees the faces of the people in front of you. Each person gets a "
        "number while they stay in view (person 3). A number is a label, not a name: never say it "
        "aloud, and someone who comes back later may get a new one. You cannot tell who anyone is.\n\n"
        f"- `{PERSON_EVENT}` tells you what just happened: a person came into view, left your "
        "view, or came very close to you. You may greet someone who arrives, say goodbye to someone "
        "who leaves, or make a light remark when someone comes very close, or do nothing; don't "
        "break off a conversation to comment on every coming and going.\n"
        f"- `{PEOPLE_IN_VIEW}` tells you who is in view right now, where, and whom your head is "
        "following.\n\n"
        "Following a person with your head shows them you are paying attention: "
        "`start_tracking(person)` follows that person, `start_tracking()` follows whoever is in "
        "front of you, and `stop_tracking()` stops. When someone comes into view and your head is "
        "not following anyone, start following them. When several people are in view, follow the "
        "one you are talking with. When everyone has left, stop. Following collides with nothing: "
        "it may go in the same turn as `say` or an emotion."
    )


# --- The producer --------------------------------------------------------------------------------


class PeopleInput:
    """The people in view, as an ``Input`` (specs/embodiment.md): two entries and the producer that
    writes them from the bridge's ``users`` and ``head_tracking``.

    ``start`` spawns three tasks on the running loop — a watcher per observable, each reading every
    published report in order (``changes(every=True)``: a busy loop misses no arrival), and the
    standing entry's refresher — and ``stop`` cancels and awaits them. The users watcher snapshots
    who is already present in the same loop step as it subscribes (a subscription sees only what is
    published after it), so whoever stands in front of the robot at launch is announced too.
    Everything runs on the one loop, so the bookkeeping needs no lock. *clock* is a test seam.
    """

    def __init__(
        self,
        bridge: ReachyMiniBridge,
        *,
        clock: Callable[[], float] = time.monotonic,
        refresh_s: float = STANDING_REFRESH_S,
    ) -> None:
        self._bridge = bridge
        self._clock = clock
        self._refresh_s = refresh_s
        self._deliver: Deliver | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._standing: str | None = None  # the standing text last delivered
        self._event: str | None = None  # the event text last delivered, and when
        self._event_at = -math.inf
        self._close = False  # the followed person inside the personal space, and who
        self._close_id: int | None = None
        self._close_at: dict[
            int, float
        ] = {}  # when each person's last "very close" was said
        self._entries = (
            InputEntry(
                PERSON_EVENT,
                str,
                serialize_fn=_serialize,
                triggers_llm_call=True,
                ttl=PERSON_EVENT_TTL,
                display_fn=lambda value: f"👤 {value}",
            ),
            InputEntry(
                PEOPLE_IN_VIEW,
                str,
                serialize_fn=_serialize,
                display_fn=lambda value: f"👥 {value}",
            ),
        )

    @property
    def entries(self) -> Sequence[InputEntry]:
        return self._entries

    async def start(self, deliver: Deliver) -> None:
        """Begin delivering: the watchers and the refresher, on the running loop. Returns once each
        has taken its first step — the snapshot delivered, the subscriptions open."""
        await self.stop()
        self._deliver = deliver
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._guarded(self._watch_users())),
            loop.create_task(self._guarded(self._watch_tracking())),
            loop.create_task(self._guarded(self._refresh_loop())),
        ]
        await asyncio.sleep(0)

    async def stop(self) -> None:
        """Cancel the watchers and the refresher and wait for them; nothing is delivered after."""
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._deliver = None

    async def _guarded(self, work: Any) -> None:
        """A watcher that fails is logged and ends: the robot keeps running, the entries stop
        changing. Cancellation (``stop``) propagates untouched."""
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "the people input's watcher failed; its entries stop changing"
            )

    async def _watch_users(self) -> None:
        # The snapshot and the subscription in one loop step: no await between reading who is
        # present and the iterator's first drive, which is when its buffer is created.
        present = self._bridge.users.value
        self._refresh()
        if present.active and present.users:
            self._event_lines([present_at_start_line(u) for u in present.users])
        async for report in self._bridge.users.changes(every=True):
            self._on_users(report)

    async def _watch_tracking(self) -> None:
        current = self._bridge.head_tracking.value
        self._close, self._close_id = current.in_personal_space, current.track_id
        async for report in self._bridge.head_tracking.changes(every=True):
            self._on_tracking(report)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_s)
            self._refresh()

    def _on_users(self, report: UsersReport) -> None:
        arrived = set(report.arrived)
        lines = [arrived_line(u) for u in report.users if u.track_id in arrived]
        for departure in report.left:
            self._close_at.pop(departure.track_id, None)
            # Only someone seen walking away is an event: "unseen" (not looked at for a while) and
            # "stopped" (detection stopped) say nothing about the person; the standing entry follows.
            if departure.reason == "left":
                lines.append(left_line(departure.track_id))
        if lines:
            self._event_lines(lines)
        self._refresh()

    def _on_tracking(self, report: HeadTrackingReport) -> None:
        entering = report.in_personal_space and not (
            self._close and self._close_id == report.track_id
        )
        self._close, self._close_id = report.in_personal_space, report.track_id
        if entering and report.track_id is not None:
            now = self._clock()
            last = self._close_at.get(report.track_id, -math.inf)
            if now - last >= PERSONAL_SPACE_COOLDOWN_S:
                self._close_at[report.track_id] = now
                self._event_lines(
                    [close_line(report.track_id, self._distance(report.track_id))]
                )
        self._refresh()

    def _distance(self, track_id: int) -> float | None:
        for user in self._bridge.users.value.users:
            if user.track_id == track_id:
                return user.face.distance_m
        return None

    def _event_lines(self, lines: Sequence[str]) -> None:
        """Write *lines* as the person event — appended to the previous one when it landed less than
        ``EVENT_JOIN_S`` ago, so a step woken by both reads both."""
        now = self._clock()
        text = "\n".join(lines)
        if self._event is not None and now - self._event_at < EVENT_JOIN_S:
            text = f"{self._event}\n{text}"
        self._event, self._event_at = text, now
        self._write(PERSON_EVENT, text)

    def _refresh(self) -> None:
        """Re-render the standing entry; write it only when its text changed."""
        text = standing_text(self._bridge.users.value, self._bridge.head_tracking.value)
        if text != self._standing:
            self._standing = text
            self._write(PEOPLE_IN_VIEW, text)

    def _write(self, key: str, value: str | None) -> None:
        if self._deliver is not None:
            self._deliver(key, value)
