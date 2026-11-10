"""The end-to-end scenarios (specs/e2e.md, "The scenarios"), written once for both embodiments:
each test file runs them on its own body's fixture.

Every scenario reads what the app's inputs delivered (`InputLog`) before the agent's reaction (the
harness's timeline), so a perception failure shows as one and an LLM failure cannot hide it. The
framework's checks then run over the whole timeline. Budgets are generous timeouts, never latency
thresholds.
"""

from __future__ import annotations

import time

from person import HAND_PHOTOS
from support import (
    E2E,
    READY_TIMEOUT_S,
    command_spans,
    failed_checks,
    reactions_to,
    settled,
    wait_until,
)

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


def wait_ready(e2e: E2E) -> None:
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


def assert_checks(e2e: E2E) -> None:
    failed = failed_checks(e2e.run)
    assert not failed, [f"{f.expectation}: {f.detail}" for f in failed]


def startup(e2e: E2E) -> None:
    """Scenario 1: the real system builds with nothing missing (the fixture asserts it) and every
    input comes up — the ASR on the cable, the camera through the hand models."""
    wait_ready(e2e)
    assert_checks(e2e)


def spoken_turn(e2e: E2E) -> None:
    """Scenario 2: the person asks a question out loud; the ASR delivers it and the app answers
    through its voice."""
    wait_ready(e2e)
    voice = e2e.run.probe.output_command_name
    assert voice is not None

    t = time.monotonic()
    e2e.person.say("Hello, what is your name?")
    heard = e2e.inputs.wait_for(
        "speech_input",
        lambda value: isinstance(value, str) and "name" in value.lower(),
        within=HEARD_WITHIN_S,
        after=t,
    )
    assert heard is not None, e2e.inputs.delivered("speech_input")

    issued = e2e.run.expect_action(voice, within=REPLY_WITHIN_S)
    assert issued.ok, issued.detail
    spoken = e2e.run.expect_action(voice, state="complete", within=SPOKEN_WITHIN_S)
    assert spoken.ok, spoken.detail
    assert_checks(e2e)


def hand_signs(e2e: E2E) -> None:
    """Scenario 3: nothing is delivered while no hand is in view; then each photo, shown in turn,
    is delivered as its sign on hand 1, and the agent reacts to it."""
    wait_ready(e2e)

    t = time.monotonic()
    time.sleep(QUIET_S)
    shown = [d for d in e2e.inputs.delivered("hand_gesture_1") if d.t >= t and d.value]
    assert not shown, f"a sign with no hand in view: {shown}"

    for photo, sign in HAND_PHOTOS.items():
        _show_and_expect(e2e, photo, sign)

    assert_checks(e2e)


def _show_and_expect(e2e: E2E, photo: str, sign: str) -> None:
    """Show *photo*: hand 1 carries *sign*, the agent reacts to it; then take the hand away and wait
    for the agent to settle. WICA drops a trigger that arrives while a reaction is in flight, so a
    sign shown while the agent still reasons about its last reply would be lost — a person waits
    for the app to finish too."""
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
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )


def emotion(e2e: E2E) -> None:
    """Scenario 4 (the robot): asked for an emotion out loud, the agent plays one to its end and
    speaks too.

    The persona asks for the two in turn, never at once (one body, one speaker). Whether the model
    keeps that rule is its behaviour, not the product's wiring — Claude Haiku issued both together in
    2 runs of 3 on 2026-11-10 — so an overlap is recorded in the run's report, not failed on."""
    wait_ready(e2e)
    voice = e2e.run.probe.output_command_name
    assert voice is not None

    t = time.monotonic()
    e2e.person.say("Can you show me that you are happy?")
    heard = e2e.inputs.wait_for(
        "speech_input",
        lambda value: isinstance(value, str) and "happy" in value.lower(),
        within=HEARD_WITHIN_S,
        after=t,
    )
    assert heard is not None, e2e.inputs.delivered("speech_input")

    played = e2e.run.expect_action("play_emotion", within=REPLY_WITHIN_S)
    assert played.ok, played.detail
    done = e2e.run.expect_action(
        "play_emotion", state="complete", within=SPOKEN_WITHIN_S
    )
    assert done.ok, done.detail
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    spans = command_spans(e2e.run)
    voices = [s for s in spans if s[0] == voice]
    emotions = [s for s in spans if s[0] == "play_emotion"]
    assert voices, "the agent never spoke"
    overlaps = [(v, m) for v in voices for m in emotions if v[1] < m[2] and m[1] < v[2]]
    if overlaps:
        e2e.run.report_issue(
            "minor",
            "the voice and an emotion ran at once (the persona's one-body rule)",
            repr(overlaps),
        )
    assert_checks(e2e)
