"""Robot control: the agent's Reachy Mini commands (``RobotControl``).

**Placeholder — design only.** Specified by [specs/robot-control.md](../../specs/robot-control.md)
(Status: Draft). No implementation yet; see the spec for the intended design: a thin, non-raising
WICA-command wrapper over the sibling ``reachy-mini-bridge`` ``ReachyMiniApi`` verbs — express an
emotion (``play_emotion`` / ``list_emotions``), start/stop autonomous head tracking, and read/set
the motor torque state — so the agent can *act* through the robot the same way ``say`` is its voice.
"""
