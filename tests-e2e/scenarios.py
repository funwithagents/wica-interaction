"""The end-to-end scenarios (specs/e2e.md, "The scenarios"), written once for both embodiments:
each test file runs them on its own body's fixture — the focused scenarios on a fresh app each, the
conversation's steps one after another on one app.

Every scenario reads what the app's inputs delivered (`InputLog`) before the agent's reaction (the
harness's timeline), so a perception failure shows as one and an LLM failure cannot hide it. The
framework's checks then run over the whole timeline. Budgets are generous timeouts, never latency
thresholds.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from person import HAND_PHOTOS, PERSON_FAR, PERSON_NEAR
from support import (
    E2E,
    READY_TIMEOUT_S,
    SCENARIO_CHECKS,
    command_spans,
    echoes,
    failed_checks,
    forget_earlier,
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
# A person: the sim's render, the face detector, the bridge confirming them (0.3 s).
PERSON_WITHIN_S = 15.0
# A person gone: the bridge drops them once missing 2 s where the camera looks.
GONE_WITHIN_S = 15.0
# How long after an emotion a departure it caused would have shown.
AFTER_A_MOVE_S = 5.0


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


def assert_checks(
    e2e: E2E, checks: tuple[str, ...] = SCENARIO_CHECKS, *, since: float = 0.0
) -> None:
    failed = failed_checks(e2e.run, checks, since=since)
    assert not failed, [f"{f.expectation}: {f.detail}" for f in failed]


def begin(e2e: E2E, step: str) -> float:
    """Start a step from where a person would: every input up, the agent settled, dictation off and
    no hand in view — whatever an earlier step left — then mark it in the report. Returns its
    wall-clock start, for the checks over its own window."""
    wait_ready(e2e)
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled before the step"
    )
    bridge, loop = e2e.system.bridge, e2e.system.loop
    if bridge is not None and loop is not None and bridge.status()["dictating"]:
        asyncio.run_coroutine_threadsafe(bridge.stop_dictation(), loop).result(10.0)
    e2e.person.hide()
    e2e.run.note(f"step: {step}")
    forget_earlier(e2e.run)
    return time.time()


def startup(e2e: E2E) -> None:
    """Scenario 1 and the conversation's first step: the real system builds with nothing missing
    (the fixture asserts it) and every input comes up — the ASR on the cable, the camera through the
    hand models."""
    since = begin(e2e, "ready")
    assert_checks(e2e, since=since)


def question(e2e: E2E) -> None:
    """Scenario 2 and a conversation step: the person asks a question out loud; the ASR delivers it
    and the app answers through its voice."""
    since = begin(e2e, "a question")
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
    assert_checks(e2e, since=since)


def hand_signs(e2e: E2E) -> None:
    """Scenario 3: nothing is delivered while no hand is in view; then each photo, shown in turn,
    is delivered as its sign on hand 1, and the agent reacts to it."""
    since = begin(e2e, "hand signs")

    t = time.monotonic()
    time.sleep(QUIET_S)
    shown = [d for d in e2e.inputs.delivered("hand_gesture_1") if d.t >= t and d.value]
    assert not shown, f"a sign with no hand in view: {shown}"

    for photo, sign in HAND_PHOTOS.items():
        _show_and_expect(e2e, photo, sign)

    assert_checks(e2e, since=since)


def sign(e2e: E2E) -> None:
    """A conversation step: the person shows three fingers; the sign is delivered and the agent
    reacts."""
    since = begin(e2e, "a sign")
    _show_and_expect(e2e, "hand_three2", "three")
    assert_checks(e2e, since=since)


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
    """A conversation step (the robot): asked for an emotion out loud, the agent plays one to its
    end and speaks too.

    The persona asks for the two in turn, never at once (one body, one speaker). Whether the model
    keeps that rule is its behaviour, not the product's wiring — Claude Haiku issued both together in
    2 runs of 3 on 2026-11-10 — so an overlap is recorded in the run's report, not failed on."""
    since = begin(e2e, "an emotion")
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
    spans = [s for s in command_spans(e2e.run) if s[1] >= since]
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
    assert_checks(e2e, since=since)


# A reply long enough to be interrupted: a story told at speaking pace.
LONG_REQUEST = (
    "Please tell me a long story about a dragon who learns to bake bread, "
    "at least ten sentences long."
)

# What the person says to interrupt the app.
STOP_SENTENCE = "Please stop talking now, that is enough for me."


def _speaking_started(e2e: E2E) -> str:
    """Ask for a long story and wait until the voice is running with it; the voice's name."""
    voice = e2e.run.probe.output_command_name
    assert voice is not None
    e2e.person.say(LONG_REQUEST)
    running = e2e.run.expect_action(voice, state="running", within=REPLY_WITHIN_S)
    assert running.ok, running.detail
    time.sleep(2.0)  # well into the story
    return voice


def spoken_stop(e2e: E2E) -> None:
    """A conversation step: the person says "stop" while the app tells a long story; the running
    `say` is cancelled. With the room's echo, the stop reaches the ASR through the canceller."""
    since = begin(e2e, "a spoken stop")
    voice = _speaking_started(e2e)
    # One full sentence: the ASR does poorly on short ones (a bare "Stop." delivered nothing).
    e2e.person.say(STOP_SENTENCE)
    cancelled = e2e.run.expect_action(voice, state="cancelled", within=REPLY_WITHIN_S)
    assert cancelled.ok, cancelled.detail
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    assert_checks(e2e, since=since)


def shown_stop(e2e: E2E) -> None:
    """A conversation step: the person shows `stop` while the app tells a long story; the running
    `say` is cancelled — the one sign the persona fixes."""
    since = begin(e2e, "a shown stop")
    voice = _speaking_started(e2e)
    e2e.person.show("hand_stop")
    cancelled = e2e.run.expect_action(voice, state="cancelled", within=REPLY_WITHIN_S)
    e2e.person.hide()
    assert cancelled.ok, cancelled.detail
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    assert_checks(e2e, since=since)


# The sentence that ends a dictated passage: both configs end one on a trigger word ("send"). A full
# sentence, since the ASR does poorly on short ones; asr-engine drops the text of the sentence that
# holds the trigger word, so it carries nothing of the note.
END_SENTENCE = "That is the end of my note, please send it."


def note(e2e: E2E) -> None:
    """A conversation step: asked to take a note, the agent starts dictation; the passage, two
    sentences with a pause between them, arrives as one input, closed by a sentence holding a
    trigger word; dictation is over after it. Then the person asks what the note said: the answer
    is recorded, not failed on (the persona asks nothing of it — open question 3)."""
    since = begin(e2e, "a note")
    bridge = e2e.system.bridge
    voice = e2e.run.probe.output_command_name
    assert bridge is not None and voice is not None

    e2e.person.say("I would like to dictate a note, please.")
    started = e2e.run.expect_action(
        "start_dictation", state="complete", within=REPLY_WITHIN_S
    )
    assert started.ok, started.detail
    assert bridge.status()["dictating"], bridge.status()
    # Dictate once the app has finished answering, as a person would: it may say more than one
    # thing (a "go ahead", then "dictation is on"), and a passage ending on a trigger word runs no
    # timer, so waiting costs nothing.
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )

    t = time.monotonic()
    e2e.person.say("Buy milk and eggs.")
    time.sleep(1.5)  # a pause that would close a conversational turn
    e2e.person.say("Call the doctor tomorrow morning.")
    time.sleep(1.5)
    e2e.person.say(END_SENTENCE)
    passage = e2e.inputs.wait_for(
        "speech_input",
        lambda value: isinstance(value, str) and value.strip() != "",
        within=HEARD_WITHIN_S,
        after=t,
    )
    # The passage still open, if it never closed: what the ASR heard, short of the trigger word.
    assert passage is not None, (
        e2e.inputs.delivered("speech_input"),
        f"open: {bridge.display_text!r}",
        bridge.status(),
    )
    text = str(passage.value).lower()
    assert "milk" in text and "tomorrow" in text, (
        f"the passage did not arrive as one input: {e2e.inputs.delivered('speech_input')}"
    )
    assert wait_until(lambda: not bridge.status()["dictating"], HEARD_WITHIN_S), (
        bridge.status()
    )
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )

    forget_earlier(e2e.run)
    asked = time.time()
    e2e.person.say("What was in my note?")
    answered = e2e.run.expect_action(voice, state="complete", within=SPOKEN_WITHIN_S)
    said = [
        str((e.args or {}).get("text", ""))
        for e in e2e.run.timeline()
        if e.kind == "command_issued" and e.name == voice and e.t >= asked
    ]
    if not answered.ok or not any("milk" in s.lower() for s in said):
        e2e.run.report_issue(
            "minor", "the answer about the note did not recall it", repr(said)
        )
    # Nothing asks the agent to answer a note: the persona lets it stay silent, and Claude Haiku
    # does, every time. So the note may be met with `noop` (specs/e2e.md, open question 3).
    assert_checks(
        e2e,
        tuple(c for c in SCENARIO_CHECKS if c != "no_noop_on_input"),
        since=since,
    )


def never_heard_itself(e2e: E2E) -> None:
    """The conversation's last step: which `speech_input`s of the whole conversation were the app's
    own words. The computer's canceller still lets short bursts through (specs/e2e.md, open question
    4), so what came back is recorded in the report as an issue, not failed on, until a better
    canceller is in. Without the room's echo nothing can come back, and the report says so."""
    if not e2e.echo:
        e2e.run.note("no echo in this run: the app's voice never reached its mic")
        return
    heard = echoes(e2e.run, e2e.inputs, e2e.person.said)
    if heard:
        e2e.run.report_issue(
            "major", "the app heard its own voice through the canceller", repr(heard)
        )


# --- The users (the robot's; specs/e2e.md, scenarios 4 and 5) -------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _inside(value: Any) -> int | None:
    """The user a `personal_space` value has inside, if any."""
    return getattr(value, "user", None)


def _user_arrives(e2e: E2E) -> int:
    """Put a person in front of the robot: the arrival is delivered, `users_in_view` lists them,
    and the agent reacts to the arrival. Their user number."""
    reacted = reactions_to(e2e.run, "user_event")
    t = time.monotonic()
    e2e.person.arrive(PERSON_FAR)
    arrived = e2e.inputs.wait_for(
        "user_event",
        lambda value: "came into view" in _text(value),
        within=PERSON_WITHIN_S,
        after=t,
    )
    assert arrived is not None, e2e.inputs.delivered("user_event")
    match = re.search(r"user (\d+)", _text(arrived.value))
    assert match is not None, arrived.value
    user = int(match.group(1))
    listed = e2e.inputs.wait_for(
        "users_in_view",
        lambda value: f"user {user} (" in _text(value),
        within=PERSON_WITHIN_S,
        after=t,
    )
    assert listed is not None, e2e.inputs.delivered("users_in_view")
    assert wait_until(
        lambda: reactions_to(e2e.run, "user_event") > reacted, REPLY_WITHIN_S
    ), "the agent never reacted to the user arriving"
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    return user


def users(e2e: E2E) -> None:
    """Scenario 4: nobody is announced while nobody is there; a user arriving is delivered, listed
    and reacted to; the agent follows them (asked out loud when it did not on its own); coming into
    the personal space and stepping back out are each delivered and reacted to; leaving is delivered
    and reacted to."""
    since = begin(e2e, "users")

    t = time.monotonic()
    time.sleep(QUIET_S)
    announced = [d for d in e2e.inputs.delivered("user_event") if d.t >= t and d.value]
    assert not announced, f"a user announced with nobody there: {announced}"

    arrived_at = time.monotonic()
    user = _user_arrives(e2e)

    def following(value: Any) -> bool:
        return "Your head is following user" in _text(value)

    if not any(
        following(d.value)
        for d in e2e.inputs.delivered("tracked_user")
        if d.t >= arrived_at
    ):
        e2e.run.report_issue(
            "minor",
            "the agent did not follow the user who came into view on its own",
            repr([d.value for d in e2e.inputs.delivered("tracked_user")]),
        )
        e2e.person.say("Please look at me while we talk.")
    tracked = e2e.inputs.wait_for(
        "tracked_user", following, within=SPOKEN_WITHIN_S, after=arrived_at
    )
    assert tracked is not None, (
        e2e.inputs.delivered("tracked_user"),
        [e.name for e in e2e.run.timeline() if e.kind == "command_issued"],
    )
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )

    reacted = reactions_to(e2e.run, "personal_space")
    t = time.monotonic()
    e2e.person.step_to(PERSON_NEAR)
    inside = e2e.inputs.wait_for(
        "personal_space",
        lambda value: _inside(value) is not None,
        within=PERSON_WITHIN_S,
        after=t,
    )
    assert inside is not None, (
        e2e.inputs.delivered("personal_space"),
        e2e.inputs.delivered("tracked_user")[-3:],
    )
    assert wait_until(
        lambda: reactions_to(e2e.run, "personal_space") > reacted, REPLY_WITHIN_S
    ), "the agent never reacted to the user coming into its personal space"
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )

    reacted = reactions_to(e2e.run, "personal_space")
    t = time.monotonic()
    e2e.person.step_to(PERSON_FAR)
    out = e2e.inputs.wait_for(
        "personal_space",
        lambda value: value is not None and _inside(value) is None,
        within=PERSON_WITHIN_S,
        after=t,
    )
    assert out is not None, e2e.inputs.delivered("personal_space")
    assert wait_until(
        lambda: reactions_to(e2e.run, "personal_space") > reacted, REPLY_WITHIN_S
    ), "the agent never reacted to the user stepping back out of its personal space"
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )

    reacted = reactions_to(e2e.run, "user_event")
    t = time.monotonic()
    e2e.person.leave()
    left = e2e.inputs.wait_for(
        "user_event",
        lambda value: re.search(r"User \d+ left your view\.", _text(value)) is not None,
        within=GONE_WITHIN_S,
        after=t,
    )
    assert left is not None, e2e.inputs.delivered("user_event")
    assert wait_until(
        lambda: reactions_to(e2e.run, "user_event") > reacted, REPLY_WITHIN_S
    ), "the agent never reacted to the user leaving"
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    e2e.run.note(f"the person was user {user}")
    assert_checks(e2e, since=since)


def a_move_keeps_the_user(e2e: E2E) -> None:
    """Scenario 5: with a user in front of it, the robot plays an emotion — its head turned away for
    part of it — and the user is not reported gone."""
    since = begin(e2e, "a move keeps the user")
    _user_arrives(e2e)

    asked = time.monotonic()
    e2e.person.say("Can you show me that you are happy?")
    done = e2e.run.expect_action(
        "play_emotion", state="complete", within=SPOKEN_WITHIN_S
    )
    assert done.ok, done.detail
    time.sleep(AFTER_A_MOVE_S)
    gone = [
        d
        for d in e2e.inputs.delivered("user_event")
        if d.t >= asked and "left your view" in _text(d.value)
    ]
    assert not gone, f"the user was reported gone during an emotion: {gone}"
    assert wait_until(lambda: settled(e2e.run), SPOKEN_WITHIN_S), (
        "the agent never settled"
    )
    assert_checks(e2e, since=since)
