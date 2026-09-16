"""Robot control: the agent's Reachy Mini action commands (``RobotControl``).

The **actions** half of the app (specs/robot-control.md): a thin, **non-raising** adapter that turns
the sibling ``reachy-mini-bridge`` ``ReachyMiniApi`` verbs into WICA Commands the agent chooses among
while reasoning — express an emotion (``play_emotion`` / ``list_emotions``) and read/set the motor
torque state. ``say`` is *not* here (that is WICA's output Command, see tts_output.py); this module
holds the invokable **action** commands. Head tracking is not agent-facing: the bridge's motion loop
drives it automatically, the same as presence and breathing.

``RobotControl`` holds the *same* ``ReachyMiniApi`` instance that backs ``say`` and the mic stream, so
there is one robot connection and one media session. Each command is a bound async method whose
name/signature/docstring become what the model sees (the app registers them via
``wica.register_command``), and — like every Command — each maps the bridge's typed failures onto a
readable string rather than raising, so the agent always gets feedback it can reason over:

- ``play_emotion`` **moves the robot**, so the bridge raises ``MotorsNotEnabledError`` when the motors
  aren't ``"enabled"``. That is caught and turned into a message telling the model to enable and retry
  — the app never silently auto-enables (matching the bridge's fail-fast stance).
- Any other failure (bad emotion/state name → ``ValueError``, connection/other ``BridgeError``) is
  caught and returned as ``"<verb> failed: <message>"``; success returns a short confirmation, the
  read-back state, or the emotion list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reachy_mini_bridge.errors import MotorsNotEnabledError

if TYPE_CHECKING:
    from reachy_mini_bridge.api import ReachyMiniApi

# The one motor-precondition message: it names the exact fix so the agent can enable and retry.
_MOTORS_OFF = (
    "Can't do that: the motors are off. Call set_motors_state('enabled') first."
)


class RobotControl:
    """The agent's robot action commands, over one shared ``ReachyMiniApi``.

    Constructed with the app's single ``ReachyMiniApi`` (the same instance behind ``say`` and the mic
    stream). Every command is a bound coroutine, so registering it preserves its name, signature, and
    docstring — the docstrings below are what the model sees as each action's description.
    """

    def __init__(self, api: ReachyMiniApi) -> None:
        self._api = api

    async def list_emotions(self) -> str:
        """List the emotions you can play (names for play_emotion)."""
        try:
            names = await self._api.list_emotions()
        except Exception as exc:  # noqa: BLE001 - any bridge/connection failure becomes a readable reply
            return f"list_emotions failed: {exc}"
        if not names:
            return "No emotions are available."
        return "Emotions you can play: " + ", ".join(names)

    async def play_emotion(self, name: str) -> str:
        """Play a physical emotion on the robot — a short recorded motion, e.g. "happy" or
        "curious". Call list_emotions first if unsure of the names. Needs the motors enabled."""
        try:
            await self._api.play_emotion(name)
        except MotorsNotEnabledError:
            return _MOTORS_OFF
        except Exception as exc:  # noqa: BLE001 - unknown name / bridge failure → readable reply
            return f"play_emotion failed: {exc}. Call list_emotions to see the valid names."
        return f"OK — played the {name!r} emotion."

    async def get_motors_state(self) -> str:
        """Report whether the motors are enabled, disabled, or in gravity-compensation (limp-but-held)."""
        try:
            state = await self._api.get_motors_state()
        except Exception as exc:  # noqa: BLE001 - any bridge/connection failure becomes a readable reply
            return f"get_motors_state failed: {exc}"
        return f"The motors are {state!r}."

    async def set_motors_state(self, state: str) -> str:
        """Set the motors: "enabled" (ready to move), "disabled" (limp), or "gravity_compensation"
        (a gentle rest — the head holds position). Enable before playing an emotion."""
        try:
            await self._api.set_motors_state(state)
        except Exception as exc:  # noqa: BLE001 - unknown state / bridge failure → readable reply
            return f"set_motors_state failed: {exc}"
        return f"OK — set the motors to {state!r}."
