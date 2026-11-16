"""The users in front of the Reachy Mini, as the agent's input: ``UsersInput``.

The robot's own perception (specs/reachy-embodiment.md, "Users: ``UsersInput``"), delivered through
the embodiment's ``inputs()`` — the mirror of its Commands (specs/embodiment.md): this module
*describes* four World entries and *produces* their values; the lifecycle registers the entries and
hands it a guarded ``deliver``. It never sees the World.

The word is the bridge's: a *user* is a person the bridge holds present (reachy-mini-bridge
specs/vision/user_perception.md, "The users present") — a face confirmed over 0.3 s, kept while a
move of the robot's own looks away, dropped after 2 s missing where the camera looks (``"left"``) or
20 s unseen (``"unseen"``). Whom the head follows, and whether that user is inside the personal
space, is its head tracker's (specs/motion/head_tracking.md). This module only *words* it, one entry
per concept:

- ``user_event`` — what just happened, one-shot like ``speech_input``: a user came into view, or
  left it. It wakes the agent.
- ``users_in_view`` — who is present now, and where. Standing; never wakes the agent.
- ``personal_space`` — which user, if any, is inside the personal space. A state that wakes the
  agent when it changes: someone entering, leaving, another taking their place.
- ``tracked_user`` — whom the head follows, or that it follows nobody. Standing; never wakes.

The standing entries are the app's exception to "no mirrored embodiment state" (specs/app.md, "World,
Commands, persona"): the agent holds the tracking Commands, and decides on what they say.

Every line says what was perceived and no more: a number is a track, not an identity — the same
person coming back after a break gets a new one — so nothing here says *the same person* or a name.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Literal

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

USER_EVENT = "user_event"
USERS_IN_VIEW = "users_in_view"
PERSONAL_SPACE = "personal_space"
TRACKED_USER = "tracked_user"

# A user event is one-shot, like an utterance: the step it wakes observes it, then the World clears
# it (specs/app.md, "Inputs are transient").
USER_EVENT_TTL = timedelta(seconds=2)
# A second event this soon after the first is written *with* it, so two arrivals on one coalescing
# window both reach the step they wake rather than the second replacing the first.
EVENT_JOIN_S = 0.5
# How often the standing entries are re-rendered between changes (a user moving to the other side,
# coming nearer); each is written only when its value changed.
STANDING_REFRESH_S = 1.0
# How long the personal space says what just changed (someone came in, stepped out) before it is
# rewritten settled — a state alone reads the same in the step it wakes as in every later one.
SPACE_CHANGE_S = 2.0

# The side a face is on, from its normalised x in [-1, 1]: the outer thirds are left and right. The
# robot's camera is not mirrored, so the image's right is the robot's right.
_SIDE_THRESHOLD = 1.0 / 3.0

Side = Literal["left", "front", "right"]


# --- The values the entries hold -------------------------------------------------------------------
#
# The World holds what was perceived, as objects: each serializer words it for the prompt, each
# display_fn for the transcript. Positions are kept as coarsely as they are said — a side, a distance
# to the 0.1 m (0.5 m past a metre) — so a user swaying changes no value and writes nothing.


@dataclass(frozen=True)
class UserChange:
    """One thing that just happened to a user: ``arrived`` (came into view), ``present`` (already
    in view when the input started), ``left`` (seen walking away)."""

    kind: Literal["arrived", "present", "left"]
    user: int
    side: Side | None = None
    distance_m: float | None = None


@dataclass(frozen=True)
class UserInView:
    """A user present: where they were last seen, and whether the camera can see them now."""

    user: int
    side: Side
    distance_m: float | None
    in_view: bool = True


@dataclass(frozen=True)
class PersonalSpace:
    """The ``personal_space`` entry's value while it is judged (the head following someone):
    the user inside it, or ``None`` for nobody, and how far they are; for ``SPACE_CHANGE_S`` after
    a change, what just happened: ``came_in``, or ``stepped_out`` with the user who ``left``."""

    user: int | None
    distance_m: float | None = None
    change: Literal["came_in", "stepped_out"] | None = None
    left: int | None = None


@dataclass(frozen=True)
class TrackedUser:
    """Whom the head follows: ``active`` (tracking on), the ``user`` followed, the ``pinned`` one the
    agent chose (``None``: the bridge's automatic choice), and whether that pinned user is present."""

    active: bool
    user: int | None = None
    pinned: int | None = None
    pinned_present: bool = False


# --- From the bridge's reports -----------------------------------------------------------------------


def side_of(face: Face) -> Side:
    if face.x < -_SIDE_THRESHOLD:
        return "left"
    if face.x > _SIDE_THRESHOLD:
        return "right"
    return "front"


def rounded(distance_m: float | None) -> float | None:
    """A distance as coarsely as it is said: to 0.1 m under a metre, to 0.5 m from there; ``None``
    when the bridge has none (no camera model, a detector without a size)."""
    if distance_m is None or not math.isfinite(distance_m) or distance_m <= 0:
        return None
    if distance_m < 0.95:
        return max(round(distance_m, 1), 0.1)
    return round(distance_m * 2) / 2


def user_in_view(user: User) -> UserInView:
    return UserInView(
        user.track_id, side_of(user.face), rounded(user.face.distance_m), user.in_view
    )


def change(kind: Literal["arrived", "present"], user: User) -> UserChange:
    return UserChange(
        kind, user.track_id, side_of(user.face), rounded(user.face.distance_m)
    )


def users_in_view(users: UsersReport) -> tuple[UserInView, ...] | None:
    """``users_in_view``: who is present; ``None`` (the entry omitted) while detection is not
    running — not knowing is not *nobody*."""
    if not users.active:
        return None
    return tuple(user_in_view(u) for u in users.users)


def tracked_user(tracking: HeadTrackingReport, present: frozenset[int]) -> TrackedUser:
    """``tracked_user``: whom the head follows, or that it follows nobody."""
    if not tracking.active:
        return TrackedUser(active=False)
    pinned = tracking.pinned_track_id
    return TrackedUser(
        active=True,
        user=tracking.track_id,
        pinned=pinned,
        pinned_present=pinned is not None and pinned in present,
    )


def personal_space(
    users: UsersReport, tracking: HeadTrackingReport
) -> PersonalSpace | None:
    """``personal_space``: judged only while the head follows someone (the bridge judges it for the
    followed user), so ``None`` — the entry omitted — with tracking off."""
    if not tracking.active or not users.active:
        return None
    if not tracking.in_personal_space or tracking.track_id is None:
        return PersonalSpace(None)
    distance = next(
        (u.face.distance_m for u in users.users if u.track_id == tracking.track_id),
        None,
    )
    return PersonalSpace(tracking.track_id, rounded(distance))


def personal_space_changed(old: Any, new: Any) -> bool:
    """``personal_space`` wakes the agent when who is inside changes — someone entering, leaving,
    another taking their place — while it is judged; not when tracking starts or stops, nor when the
    user inside only moves."""
    if not isinstance(new, PersonalSpace):
        return False
    before = old.user if isinstance(old, PersonalSpace) else None
    return before != new.user


# --- The wordings ------------------------------------------------------------------------------------

_SIDES: dict[Side, str] = {
    "left": "to your left",
    "front": "in front of you",
    "right": "to your right",
}


def about(distance_m: float | None) -> str | None:
    """A (rounded) distance said: ``about 0.7 m away``; ``None`` without one."""
    return None if distance_m is None else f"about {distance_m:.1f} m away"


def place(side: Side | None, distance_m: float | None) -> str:
    """How far and where: ``about 0.7 m away, in front of you``."""
    parts = [p for p in (about(distance_m), _SIDES[side] if side else None) if p]
    return ", ".join(parts)


def change_line(event: UserChange) -> str:
    if event.kind == "left":
        return f"User {event.user} left your view."
    where = place(event.side, event.distance_m)
    lead = (
        "A user came into view"
        if event.kind == "arrived"
        else "A user is in view as you start"
    )
    return f"{lead}: user {event.user}{', ' + where if where else ''}."


def events_text(events: tuple[UserChange, ...]) -> str:
    return "\n".join(change_line(e) for e in events)


def users_text(users: tuple[UserInView, ...]) -> str:
    if not users:
        return "Nobody is in view."
    return f"In view: {', '.join(_user_text(u) for u in users)}."


def _user_text(user: UserInView) -> str:
    details = [place(user.side, user.distance_m)]
    if not user.in_view:
        details.append("out of your sight right now")
    return f"user {user.user} ({'; '.join(d for d in details if d)})"


def tracked_line(tracked: TrackedUser) -> str:
    if not tracked.active:
        return "You are not following anyone with your head."
    if tracked.user is not None:
        if tracked.pinned == tracked.user:
            return f"Your head is following user {tracked.user}, as you chose."
        return f"Your head is following user {tracked.user}."
    if tracked.pinned is not None:
        # The bridge reports the pin from the call and the follow from the pinned user's next
        # sighting: present means "about to", gone means the head waits on someone who left.
        if tracked.pinned_present:
            return (
                f"Your head is turning to follow user {tracked.pinned}, as you chose."
            )
        return (
            f"You chose to follow user {tracked.pinned}, who is not in view; "
            "your head is waiting for them."
        )
    return "Your head will follow whoever comes in front of you; it follows nobody yet."


def personal_space_line(value: PersonalSpace) -> str:
    distance = about(value.distance_m)
    where = f" ({distance})" if distance else ""
    if value.change == "stepped_out" and value.left is not None:
        return (
            f"User {value.left} just stepped back out of your personal space; "
            "nobody is in it now."
        )
    if value.user is None:
        return "Nobody is in your personal space."
    if value.change == "came_in":
        return (
            f"User {value.user} just came into your personal space: "
            f"they are very close to you{where}."
        )
    return f"User {value.user} is in your personal space, very close to you{where}."


def _serializer(render: Callable[[Any], str]) -> Callable[[Any, Any], Content]:
    """An entry's serialize_fn: *render* words the value; ``None`` renders nothing (the entry
    omitted)."""

    def serialize(value: Any, previous: Any) -> Content:
        if value is None:
            return []
        return [TextPart(render(value))]

    return serialize


def users_prompt_section() -> str:
    """The persona addendum describing the users the robot sees, appended at startup when the
    users input is up (specs/reachy-embodiment.md, "Persona"). Starts with a blank line so it reads
    as a new section after the persona's last line."""
    return (
        "\n\n## The users around you\n\n"
        "A camera in your head sees the faces of the people in front of you: your users. Each "
        "user gets a number while they stay in view (user 3). A number is a label, not a name: "
        "never say it aloud, and someone who comes back later may get a new one. You cannot tell "
        "who anyone is.\n\n"
        f"- `{USER_EVENT}` tells you what just happened: a user came into view, or left your "
        "view. You may greet someone who arrives or say goodbye to someone who leaves, or do "
        "nothing; don't break off a conversation to comment on every coming and going.\n"
        f"- `{USERS_IN_VIEW}` tells you who is in view right now, and where.\n"
        f"- `{PERSONAL_SPACE}` tells you which user, if any, is very close to you, inside your "
        "personal space, and says when someone just came in or just stepped back out. Answer "
        "each out loud, briefly and lightly: a word about them coming so close, then about them "
        "stepping back. It is only known while your head follows someone.\n"
        f"- `{TRACKED_USER}` tells you whom your head is following, if anyone.\n\n"
        "Following a user with your head shows them you are paying attention: "
        "`start_tracking(user)` follows that user, `start_tracking()` follows whoever is in "
        "front of you, and `stop_tracking()` stops. When someone comes into view and your head is "
        "not following anyone, start following them. When several users are in view, follow the "
        "one you are talking with. When everyone has left, stop. Following collides with nothing: "
        "it may go in the same turn as `say` or an emotion."
    )


# --- The producer --------------------------------------------------------------------------------


class UsersInput:
    """The users in view, as an ``Input`` (specs/embodiment.md): four entries and the producer that
    writes them from the bridge's ``users`` and ``head_tracking``.

    ``start`` spawns three tasks on the running loop — a watcher per observable, each reading every
    published report in order (``changes(every=True)``: a busy loop misses no arrival), and the
    standing entries' refresher — and ``stop`` cancels and awaits them. The users watcher snapshots
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
        self._written: dict[str, Any] = {}  # each standing entry's value last delivered
        self._event: tuple[UserChange, ...] = ()  # the events last delivered, and when
        self._event_at = -math.inf
        self._space_changed_at = (
            -math.inf
        )  # when personal_space last said what just changed
        self._entries = (
            InputEntry(
                USER_EVENT,
                tuple,
                serialize_fn=_serializer(events_text),
                triggers_llm_call=True,
                ttl=USER_EVENT_TTL,
                display_fn=lambda value: f"👤 {events_text(value)}",
            ),
            InputEntry(
                USERS_IN_VIEW,
                tuple,
                serialize_fn=_serializer(users_text),
                display_fn=lambda value: f"👥 {users_text(value)}",
            ),
            InputEntry(
                PERSONAL_SPACE,
                PersonalSpace,
                serialize_fn=_serializer(personal_space_line),
                triggers_llm_call=True,
                trigger_condition_fn=personal_space_changed,
                display_fn=lambda value: f"📏 {personal_space_line(value)}",
            ),
            InputEntry(
                TRACKED_USER,
                TrackedUser,
                serialize_fn=_serializer(tracked_line),
                display_fn=lambda value: f"🎯 {tracked_line(value)}",
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
        self._written = {}
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
                "the users input's watcher failed; its entries stop changing"
            )

    async def _watch_users(self) -> None:
        # The snapshot and the subscription in one loop step: no await between reading who is
        # present and the iterator's first drive, which is when its buffer is created.
        present = self._bridge.users.value
        self._refresh()
        if present.active and present.users:
            self._events(tuple(change("present", u) for u in present.users))
        async for report in self._bridge.users.changes(every=True):
            self._on_users(report)

    async def _watch_tracking(self) -> None:
        async for _report in self._bridge.head_tracking.changes(every=True):
            self._refresh()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_s)
            self._refresh()

    def _on_users(self, report: UsersReport) -> None:
        arrived = set(report.arrived)
        events = [change("arrived", u) for u in report.users if u.track_id in arrived]
        # Only someone seen walking away is an event: "unseen" (not looked at for a while) and
        # "stopped" (detection stopped) say nothing about the user; the standing entries follow.
        events += [
            UserChange("left", d.track_id) for d in report.left if d.reason == "left"
        ]
        if events:
            self._events(tuple(events))
        self._refresh()

    def _events(self, events: tuple[UserChange, ...]) -> None:
        """Write *events* as the user event — appended to the previous ones when they landed less
        than ``EVENT_JOIN_S`` ago, so a step woken by both reads both."""
        now = self._clock()
        if self._event and now - self._event_at < EVENT_JOIN_S:
            events = self._event + events
        self._event, self._event_at = events, now
        self._write(USER_EVENT, events)

    def _refresh(self) -> None:
        """Re-render the standing entries from the bridge's current reports; write each only when
        its value changed (``personal_space`` wakes the agent by its own trigger rule)."""
        users = self._bridge.users.value
        tracking = self._bridge.head_tracking.value
        present = frozenset(u.track_id for u in users.users)
        self._standing(USERS_IN_VIEW, users_in_view(users))
        self._standing(PERSONAL_SPACE, self._space(personal_space(users, tracking)))
        self._standing(TRACKED_USER, tracked_user(tracking, present))

    def _space(self, now: PersonalSpace | None) -> PersonalSpace | None:
        """*now* (settled), saying what just changed: a change of who is inside is written with it
        (and wakes the agent, by ``personal_space_changed``), kept for ``SPACE_CHANGE_S``, then
        rewritten settled (waking no one: who is inside is the same)."""
        last = self._written.get(PERSONAL_SPACE)
        if now is None:
            return None
        clock = self._clock()
        if personal_space_changed(last, now):
            self._space_changed_at = clock
            if now.user is not None:
                return replace(now, change="came_in")
            left = last.user if isinstance(last, PersonalSpace) else None
            return replace(now, change="stepped_out", left=left)
        if (
            isinstance(last, PersonalSpace)
            and last.change is not None
            and clock - self._space_changed_at < SPACE_CHANGE_S
        ):
            return replace(now, change=last.change, left=last.left)
        return now

    def _standing(self, key: str, value: Any) -> None:
        if key in self._written and self._written[key] == value:
            return
        if key not in self._written and value is None:
            self._written[key] = (
                None  # nothing to say yet: the entry starts empty anyway
            )
            return
        self._written[key] = value
        self._write(key, value)

    def _write(self, key: str, value: Any) -> None:
        if self._deliver is not None:
            self._deliver(key, value)
