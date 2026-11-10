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
import time

from person import HAND_PHOTOS
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
    # Dictate as soon as the app's "go ahead" has been said, as a person would, not over it.
    assert wait_until(lambda: not _voice_running(e2e), SPOKEN_WITHIN_S), (
        "the agent's reply never ended"
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
    """The conversation's last step: no `speech_input` of the whole conversation is the app's own
    words. Without the room's echo nothing can come back, and the report says so."""
    if not e2e.echo:
        e2e.run.note("no echo in this run: the app's voice never reached its mic")
    heard = echoes(e2e.run, e2e.inputs, e2e.person.said)
    assert not heard, f"the app heard itself: {heard}"


def _voice_running(e2e: E2E) -> bool:
    voice = e2e.run.probe.output_command_name
    return any(
        name == voice and ended == float("inf")
        for name, _start, ended in command_spans(e2e.run)
    )
