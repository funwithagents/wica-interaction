"""Robot control: the agent's Reachy Mini action commands (``RobotControl``).

The **actions** half of the app (specs/reachy-embodiment.md): a thin, **non-raising** adapter that turns
the sibling ``reachy-mini-bridge`` ``ReachyMiniApi`` verbs into WICA Commands the agent chooses among
while reasoning — express an emotion (``play_emotion``) and read/set the motor torque state. ``say``
is *not* here (that is WICA's output Command, see embodiment.py); this module holds the invokable
**action** commands. Head tracking is not agent-facing: the bridge's motion loop drives it
automatically, the same as presence and breathing.

``RobotControl`` holds the *same* ``ReachyMiniApi`` instance that backs ``say`` and the mic stream, so
there is one robot connection and one media session. ``commands()`` yields what the app registers via
``wica.register_command``: bound async methods whose name/signature/docstring become what the model
sees — except ``play_emotion``, which, when the emotion names are known, is wrapped as a ``Command``
whose description **lists the valid names**. The names are a startup snapshot the app fetches once
from ``api.list_emotions()`` (the bridge loads its library once per connection, so nothing changes
mid-session) and also folds into the system prompt through ``emotions_prompt_section`` — so the model
knows the names before its first turn, and there is no ``list_emotions`` command to spend a turn on
(specs/reachy-embodiment.md, "`RobotControl` — the action commands").

Like every Command, each maps the bridge's typed failures onto a readable string rather than raising,
so the agent always gets feedback it can reason over:

- ``play_emotion`` **moves the robot**, so the bridge raises ``MotorsNotEnabledError`` when the motors
  aren't ``"enabled"``. That is caught and turned into a message telling the model to enable and retry
  — the app never silently auto-enables (matching the bridge's fail-fast stance). The motors are
  **assumed enabled**: every description tells the model to just move, and to reach for the motor
  commands only once a move has come back saying they are off — no state check before a move.
- Any other failure (bad emotion/state name → ``ValueError``, connection/other ``BridgeError``) is
  caught and returned as ``"<verb> failed: <message>"`` (for an emotion, followed by the valid names
  when known); success returns a short confirmation or the read-back state.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from reachy_mini_bridge.errors import MotorsNotEnabledError
from wica import Command

if TYPE_CHECKING:
    from reachy_mini_bridge.api import ReachyMiniApi

# The one motor-precondition message: it names the exact fix so the agent can enable and retry.
_MOTORS_OFF = (
    "Can't do that: the motors are off. Call set_motors_state('enabled') first."
)

# `play_emotion`'s description when the names are known — the model reads the valid names right
# where it picks the tool (the docstring is the fallback when they aren't).
_PLAY_EMOTION_DESCRIPTION = (
    "Play a physical emotion on the robot — a short recorded motion. Valid names: {names}. "
    "Use exactly one of these. The motors are on by default: just play it — only if the reply "
    "says they are off, enable them and play it again."
)


def emotions_prompt_section(names: Sequence[str]) -> str:
    """The persona addendum naming the emotions the robot can play — appended by the app to the
    resolved system prompt at startup (specs/app.md, build step 3). The persona file itself only
    says the list is "further down"; this is that list. Starts with a blank line so it reads as a
    new section after the persona's last line."""
    return (
        "\n\n## Your emotions\n\n"
        "The emotions you can play with `play_emotion`, by exact name: "
        + ", ".join(names)
        + ". These are the only ones; do not invent others."
    )


class RobotControl:
    """The agent's robot action commands, over one shared ``ReachyMiniApi``.

    Constructed with the app's single ``ReachyMiniApi`` (the same instance behind ``say`` and the mic
    stream) and, when the app could fetch them, the ``emotions`` the library holds. ``commands()`` is
    what the app registers; the docstrings below are what the model sees as each action's
    description (``play_emotion``'s is replaced by one naming the emotions when they are known).
    """

    def __init__(
        self, api: ReachyMiniApi, *, emotions: Sequence[str] | None = None
    ) -> None:
        self._api = api
        self._emotions: tuple[str, ...] | None = (
            tuple(emotions) if emotions is not None else None
        )

    def commands(self) -> tuple[Command | Callable[..., Any], ...]:
        """The Commands to register with the agent, in order: ``play_emotion`` (as a ``Command``
        whose description names the valid emotions, when known), ``get_motors_state``,
        ``set_motors_state``."""
        play: Command | Callable[..., Any] = self.play_emotion
        if self._emotions:
            play = Command(
                self.play_emotion,
                description=_PLAY_EMOTION_DESCRIPTION.format(
                    names=", ".join(self._emotions)
                ),
            )
        return (play, self.get_motors_state, self.set_motors_state)

    async def play_emotion(self, name: str) -> str:
        """Play a physical emotion on the robot — a short recorded motion, e.g. "happy" or
        "curious". The motors are on by default: just play it — only if the reply says they are
        off, enable them and play it again."""
        try:
            await self._api.play_emotion(name)
        except MotorsNotEnabledError:
            return _MOTORS_OFF
        except Exception as exc:  # noqa: BLE001 - unknown name / bridge failure → readable reply
            reply = f"play_emotion failed: {exc}."
            if self._emotions:
                reply += " Valid names: " + ", ".join(self._emotions) + "."
            return reply
        return f"OK — played the {name!r} emotion."

    async def get_motors_state(self) -> str:
        """Report whether the motors are enabled, disabled, or in gravity-compensation
        (limp-but-held). Assume they are enabled; only read them back when a move has just
        reported them off or moved in an unexpected way."""
        try:
            state = await self._api.get_motors_state()
        except Exception as exc:  # noqa: BLE001 - any bridge/connection failure becomes a readable reply
            return f"get_motors_state failed: {exc}"
        return f"The motors are {state!r}."

    async def set_motors_state(self, state: str) -> str:
        """Set the motors: "enabled" (ready to move), "disabled" (limp), or "gravity_compensation"
        (a gentle rest — the head holds position). They are enabled by default, so call this only
        to recover after a move reported them off, or to rest them."""
        try:
            await self._api.set_motors_state(state)
        except Exception as exc:  # noqa: BLE001 - unknown state / bridge failure → readable reply
            return f"set_motors_state failed: {exc}"
        return f"OK — set the motors to {state!r}."
