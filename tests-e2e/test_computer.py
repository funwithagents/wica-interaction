"""The end-to-end tier on the computer embodiment (specs/e2e.md): the real app — Deepgram Flux,
Claude Haiku, phonon, the hand models — with the cable as its mic and an image file as its webcam.

Every scenario reads what the app's inputs delivered (`InputLog`) before the agent's reaction (the
harness's timeline), so a perception failure shows as one and an LLM failure cannot hide it. The
framework's checks then run over the whole timeline. Budgets are generous timeouts, never latency
thresholds.
"""

from __future__ import annotations

import time

from person import HAND_PHOTOS
from support import E2E, READY_TIMEOUT_S, failed_checks, reactions_to, wait_until

# Spoken input: the person's speech played, then the ASR closing the turn.
HEARD_WITHIN_S = 20.0
# The agent's reply: the model's step, then the voice starting.
REPLY_WITHIN_S = 30.0
# The reply spoken to its end.
SPOKEN_WITHIN_S = 60.0
# A sign: the camera's next poll, the hand and classifier stages, the 0.5 s hold.
SIGN_WITHIN_S = 15.0
# How long an empty camera must deliver nothing.
QUIET_S = 3.0


def _wait_ready(e2e: E2E) -> None:
    """Every input running: the ASR listening, the camera's frames reaching the hand stage."""
    bridge, gesture = e2e.system.bridge, e2e.system.gesture
    assert bridge is not None and gesture is not None

    def ready() -> bool:
        vision = gesture.status()
        return (
            bridge.status()["running"]
            and bridge.status()["capture_error"] is None
            and vision["error"] is None
            and vision["capture_error"] is None
            and (vision["hand_fps"] or 0) > 0
        )

    assert wait_until(ready, READY_TIMEOUT_S), (
        f"not ready: asr {bridge.status()}, vision {gesture.status()}"
    )


def _assert_checks(e2e: E2E) -> None:
    failed = failed_checks(e2e.run)
    assert not failed, [f"{f.expectation}: {f.detail}" for f in failed]


def test_startup(computer: E2E) -> None:
    """Scenario 1: the real system builds with nothing missing (the fixture asserts it) and every
    input comes up — the ASR on the cable, the webcam file through the hand models."""
    _wait_ready(computer)
    _assert_checks(computer)


def test_a_spoken_turn_is_heard_and_answered(computer: E2E) -> None:
    """Scenario 2: the person asks a question out loud; the ASR delivers it and the app answers
    through its voice."""
    _wait_ready(computer)
    voice = computer.run.probe.output_command_name
    assert voice is not None

    t = time.monotonic()
    computer.person.say("Hello, what is your name?")
    heard = computer.inputs.wait_for(
        "speech_input",
        lambda value: isinstance(value, str) and "name" in value.lower(),
        within=HEARD_WITHIN_S,
        after=t,
    )
    assert heard is not None, computer.inputs.delivered("speech_input")

    issued = computer.run.expect_action(voice, within=REPLY_WITHIN_S)
    assert issued.ok, issued.detail
    spoken = computer.run.expect_action(voice, state="complete", within=SPOKEN_WITHIN_S)
    assert spoken.ok, spoken.detail
    _assert_checks(computer)


def test_each_hand_sign_reaches_the_agent(computer: E2E) -> None:
    """Scenario 3: nothing is delivered while no hand is in view; then each photo, shown in turn,
    is delivered as its sign on hand 1, and the agent reacts to it."""
    _wait_ready(computer)

    t = time.monotonic()
    time.sleep(QUIET_S)
    shown = [
        d for d in computer.inputs.delivered("hand_gesture_1") if d.t >= t and d.value
    ]
    assert not shown, f"a sign with no hand in view: {shown}"

    for photo, sign in HAND_PHOTOS.items():
        _show_and_expect(computer, photo, sign)

    _assert_checks(computer)


def _show_and_expect(e2e: E2E, photo: str, sign: str) -> None:
    """Show *photo*: hand 1 carries *sign*, the agent reacts to it; then take the hand away and let
    the reaction's voice finish, so the next sign (a `stop`) cancels nothing of it."""
    reacted = reactions_to(e2e.run, "hand_gesture_1")
    t = time.monotonic()
    e2e.person.show(photo)
    delivered = e2e.inputs.wait_for(
        "hand_gesture_1", lambda value: value == sign, within=SIGN_WITHIN_S, after=t
    )
    assert delivered is not None, (
        photo,
        e2e.inputs.delivered("hand_gesture_1"),
        e2e.system.gesture and e2e.system.gesture.status(),
    )
    assert wait_until(
        lambda: reactions_to(e2e.run, "hand_gesture_1") > reacted, REPLY_WITHIN_S
    ), f"the agent never reacted to {sign!r} ({photo})"
    e2e.person.hide()
    wait_until(lambda: not _speaking(e2e), SPOKEN_WITHIN_S)


def _speaking(e2e: E2E) -> bool:
    """Whether a call of the voice is still running."""
    voice = e2e.run.probe.output_command_name
    states: dict[str, str] = {}
    for event in e2e.run.timeline():
        if event.kind == "command_state" and event.name == voice and event.call_id:
            states[event.call_id] = event.state or ""
        elif event.kind == "command_issued" and event.name == voice and event.call_id:
            states.setdefault(event.call_id, "running")
    return any(state == "running" for state in states.values())
