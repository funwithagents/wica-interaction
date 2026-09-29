"""Functional tests for the Reachy Mini embodiment (specs/reachy-embodiment.md, "Testing").

Everything runs against a fake `ReachyMiniBridge` — no daemon, no hardware, no cloud: the protocol
members after `enter()`, the non-raising `say`, and the two feeds the embodiment hands the rest of
the app (`RobotMicSource` for ASR, `RobotCameraFeed` for vision — the bridge's own `bridge.camera`
behind an adapter that never touches its lifecycle).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any, cast

import pytest
from reachy_mini_bridge import ReachyMiniBridge
from reachy_mini_bridge.config import ReachyMiniConfig
from wica import Command

from tests.reachy.fakes import FakeReachyMiniBridge
from tests.test_gesture_input import ScriptedClassifier, ScriptedDetector, _scores
from wica_interaction.config import AppConfig
from wica_interaction.gesture_input import GestureInput, GestureModels
from wica_interaction.reachy.config import ReachyConfig
from wica_interaction.reachy.embodiment import (
    ReachyEmbodiment,
    RobotCameraFeed,
    RobotMicSource,
)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _config(**reachy: Any) -> ReachyConfig:
    """A ReachyConfig whose shared sections are irrelevant here (the embodiment reads only
    `.reachy`), so they are `None` behind a cast."""
    return ReachyConfig(
        app=cast(AppConfig, None), reachy=ReachyMiniConfig(backend="fake", **reachy)
    )


def _entered(bridge: FakeReachyMiniBridge, **reachy: Any) -> ReachyEmbodiment:
    """Construct the embodiment over *bridge* and enter it on a throwaway loop, as build_system does."""
    embodiment = ReachyEmbodiment(
        _config(**reachy), bridge=cast(ReachyMiniBridge, bridge)
    )
    run(embodiment.enter())
    return embodiment


# --- enter() and the protocol members ------------------------------------------------------------


def test_enter_brings_the_robot_up_and_wires_every_member() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    assert bridge.started and bridge.running
    assert bridge.emotion_fetches == 1  # one snapshot per session
    assert embodiment.voice is not None
    assert isinstance(embodiment.mic, RobotMicSource)
    assert embodiment.mic_error is None
    assert isinstance(embodiment.camera(fps=5.0), RobotCameraFeed)
    assert embodiment.missing == []


def test_construction_does_no_io() -> None:
    """Nothing happens until `enter()`: the entry point constructs the embodiment from its config,
    the lifecycle enters it on the app loop."""
    bridge = FakeReachyMiniBridge()
    ReachyEmbodiment(_config(), bridge=cast(ReachyMiniBridge, bridge))
    assert not bridge.started
    assert bridge.emotion_fetches == 0


def test_commands_are_the_three_actions_described_by_the_fetched_names() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    commands = embodiment.commands()
    names = [c.tool.name if isinstance(c, Command) else c.__name__ for c in commands]
    assert names == ["play_emotion", "get_motors_state", "set_motors_state"]
    assert (
        "list_emotions" not in names
    )  # the names are known up front, not fetched per turn

    play = commands[0]
    assert isinstance(
        play, Command
    )  # wrapped, so its description can name the emotions
    for name in bridge.emotions:
        assert name in play.tool.description


def test_prompt_addendum_names_every_emotion_and_the_command() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    addendum = embodiment.prompt_addendum
    assert "play_emotion" in addendum
    for name in bridge.emotions:
        assert name in addendum


def test_enter_failure_propagates_as_no_embodiment() -> None:
    """A robot that cannot be connected raises out of `enter()` — the lifecycle's "no embodiment"
    degrade (specs/embodiment.md), not a `missing` line."""
    bridge = FakeReachyMiniBridge(raise_on_start="no daemon reachable")
    embodiment = ReachyEmbodiment(_config(), bridge=cast(ReachyMiniBridge, bridge))

    with pytest.raises(RuntimeError, match="no daemon reachable"):
        run(embodiment.enter())


def test_a_failed_connection_carries_the_offline_hint() -> None:
    """On a backend that needs something outside the process, the "no embodiment" banner line says
    how to develop without it — only this body knows its backends, so the hint rides its error."""
    bridge = FakeReachyMiniBridge(raise_on_start="no daemon reachable")
    config = ReachyConfig(
        app=cast(AppConfig, None), reachy=ReachyMiniConfig(backend="sim")
    )
    embodiment = ReachyEmbodiment(config, bridge=cast(ReachyMiniBridge, bridge))

    with pytest.raises(RuntimeError, match="no daemon reachable") as raised:
        run(embodiment.enter())

    assert 'reachy.backend to "fake"' in str(raised.value)


def test_the_fake_backend_gets_no_offline_hint() -> None:
    """Already offline: pointing at `fake` again would be noise."""
    bridge = FakeReachyMiniBridge(raise_on_start="boom")
    embodiment = ReachyEmbodiment(_config(), bridge=cast(ReachyMiniBridge, bridge))

    with pytest.raises(RuntimeError) as raised:
        run(embodiment.enter())

    assert str(raised.value) == "boom"


def test_failed_emotion_fetch_degrades_only_the_emotions() -> None:
    """An unreadable emotion library is one `missing` line: the robot is up, `play_emotion` is still
    registered (with its plain docstring) and the addendum is empty."""
    bridge = FakeReachyMiniBridge(raise_on_list_emotions="dataset download failed")
    embodiment = _entered(bridge)

    assert any(
        m.startswith("Robot:") and "dataset download failed" in m
        for m in embodiment.missing
    )
    assert embodiment.prompt_addendum == ""
    assert embodiment.voice is not None  # the rest of the body is unaffected
    play = embodiment.commands()[0]
    assert not isinstance(
        play, Command
    )  # the plain bound method, its docstring unchanged


def test_absent_tts_block_is_reported_on_the_tts_axis() -> None:
    """With no `reachy.tts` block the robot still comes up; the missing voice is a `TTS:` line and
    `voice` stays wired (it reports the error when called)."""
    embodiment = ReachyEmbodiment(
        _config()
    )  # no injected bridge: the real fake-backend one is built
    run(embodiment.enter())
    try:
        assert (
            "TTS: no reachy.tts block configured (say is unavailable)"
            in embodiment.missing
        )
        assert not any(m.startswith("Robot: emotions") for m in embodiment.missing)
        assert embodiment.voice is not None
    finally:
        run(embodiment.exit())


def test_failed_synthesizer_build_is_reported_on_the_tts_axis() -> None:
    """A `tts` block naming an unknown module leaves the bridge's `synthesizer_error` set rather
    than raising: one `TTS:` line, the robot up, the voice still wired."""
    # An unknown module type fails inside the bridge with no key and no model download, whichever
    # provider extras happen to be installed — the degrade contract is about *any* failed build.
    embodiment = ReachyEmbodiment(_config(tts={"module": {"type": "no_such_tts"}}))
    run(embodiment.enter())
    try:
        assert any(
            m.startswith("TTS:") and "no_such_tts" in m for m in embodiment.missing
        )
        assert not any(m.startswith("Robot:") for m in embodiment.missing)
        assert embodiment.voice is not None
        # And it degrades to a readable string rather than raising.
        assert run(embodiment.say("hi")).startswith("TTS error:")
    finally:
        run(embodiment.exit())


def test_exit_stops_the_bridge() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    run(embodiment.exit())

    assert bridge.stopped and not bridge.running


# --- say ------------------------------------------------------------------------------------------


def test_say_speaks_through_the_robot_and_returns_ok() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    assert run(embodiment.say("hello")) == "OK"
    assert bridge.said == ["hello"]


def test_empty_text_is_guarded_without_calling_the_robot() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)

    assert run(embodiment.say("")) == "TTS error: text must not be empty"
    assert run(embodiment.say("   ")) == "TTS error: text must not be empty"
    assert bridge.said == []  # never reached the robot


def test_say_error_is_mapped_not_raised() -> None:
    class RaisingBridge(FakeReachyMiniBridge):
        async def say(self, text: str) -> None:
            self.said.append(text)
            raise RuntimeError("boom")

    bridge = RaisingBridge()
    embodiment = _entered(bridge)

    assert run(embodiment.say("hi")) == "TTS error: boom"
    assert bridge.said == ["hi"]  # it did attempt to speak before failing


# --- RobotMicSource -------------------------------------------------------------------------------


class FakeMicBridge:
    """Minimal stand-in for ``ReachyMiniBridge.audio_input`` — an async iterator of PCM ``bytes``.

    Yields the supplied ``chunks``; then raises ``RuntimeError(raise_after)`` if set, else blocks
    forever when ``hold_open`` is set (so the pump task stays alive), else ends. ``started`` counts
    iterations (to prove a restart re-reads the stream), ``active`` counts iterations still open (a
    cancelled pump closes its iterator), and ``yielded`` counts chunks the pump has taken.
    """

    def __init__(
        self,
        chunks: list[bytes],
        *,
        hold_open: bool = False,
        raise_after: str | None = None,
    ) -> None:
        self._chunks = chunks
        self._hold_open = hold_open
        self._raise_after = raise_after
        self.started = 0
        self.active = 0
        self.yielded = 0

    async def audio_input(self, *, mono: bool = True) -> Any:
        self.started += 1
        self.active += 1
        try:
            for chunk in self._chunks:
                yield chunk
                self.yielded += 1  # resumed ⇒ the pump has already enqueued this chunk
            if self._raise_after is not None:
                raise RuntimeError(self._raise_after)
            while self._hold_open:
                await asyncio.sleep(0.01)
        finally:
            self.active -= 1


async def _take(queue: asyncio.Queue[bytes], n: int) -> list[bytes]:
    return [await asyncio.wait_for(queue.get(), timeout=1.0) for _ in range(n)]


async def _eventually(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    """Yield to the loop until *predicate* holds (or *timeout* passes); return its final value."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


def test_mic_source_streams_chunks_in_order() -> None:
    async def scenario() -> list[bytes]:
        bridge = FakeMicBridge([b"aa", b"bbbb", b"c"])
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        queue = source.start()
        try:
            return await _take(queue, 3)
        finally:
            source.stop()

    assert run(scenario()) == [b"aa", b"bbbb", b"c"]


def test_mic_source_stop_cancels_pump() -> None:
    async def scenario() -> tuple[int, bool, str | None]:
        bridge = FakeMicBridge([b"x"], hold_open=True)
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        queue = source.start()
        await _take(queue, 1)  # the stream is live
        assert bridge.active == 1
        source.stop()
        closed = await _eventually(
            lambda: bridge.active == 0
        )  # the cancelled pump closed the stream
        return bridge.active, closed, source.capture_error

    active, closed, error = run(scenario())
    assert closed and active == 0
    assert error is None  # a deliberate stop() is not a capture failure


def test_pump_error_is_recorded_and_reaches_the_embodiment() -> None:
    """A mic stream that raises is not silent: the source records why, and the embodiment's
    `mic_error` — what the ASR panel renders through the bridge — carries it."""

    async def scenario() -> tuple[list[bytes], RobotMicSource]:
        bridge = FakeMicBridge([b"a"], raise_after="usb unplugged")
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        queue = source.start()
        got = await _take(queue, 1)
        assert await _eventually(lambda: source.capture_error is not None)
        return got, source

    got, source = run(scenario())
    assert got == [b"a"]  # audio before the failure still reached the engine
    assert source.capture_error is not None and "usb unplugged" in source.capture_error


def test_mic_error_reads_the_sources_capture_error() -> None:
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)
    mic = embodiment.mic
    assert isinstance(mic, RobotMicSource)

    assert embodiment.mic_error is None
    mic._error = "mic stream ended"  # what a dead stream records
    assert embodiment.mic_error == "mic stream ended"


def test_stream_end_is_recorded_and_a_restart_clears_it() -> None:
    async def scenario() -> tuple[str | None, str | None]:
        bridge = FakeMicBridge([b"a"], hold_open=False)  # ends after one chunk
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        source.start()
        assert await _eventually(lambda: source.capture_error is not None)
        ended = source.capture_error
        source.start()  # the engine restarting capture resets the error
        after_restart = source.capture_error
        source.stop()
        return ended, after_restart

    ended, after_restart = run(scenario())
    assert ended == "mic stream ended"
    assert after_restart is None


def test_queue_drops_oldest_under_backpressure() -> None:
    """With nobody draining, the queue holds at most `maxsize` chunks — the newest ones — and the
    overflow is dropped quietly (not raised as a capture failure)."""

    async def scenario() -> tuple[int, list[bytes], str | None]:
        chunks = [str(i).encode() for i in range(10)]
        bridge = FakeMicBridge(chunks, hold_open=True)
        source = RobotMicSource(cast(ReachyMiniBridge, bridge), maxsize=4)
        queue = source.start()
        try:
            assert await _eventually(lambda: bridge.yielded == len(chunks))
            size = queue.qsize()
            kept = [queue.get_nowait() for _ in range(size)]
            return size, kept, source.capture_error
        finally:
            source.stop()

    size, kept, error = run(scenario())
    assert size == 4
    assert kept == [b"6", b"7", b"8", b"9"]  # oldest dropped, order preserved
    assert error is None


def test_second_start_cancels_the_first_pump() -> None:
    """start() twice without stop() leaves one live pump (the first is cancelled, not orphaned), and
    only the second queue is fed."""

    async def scenario() -> tuple[int, int, int, list[bytes]]:
        bridge = FakeMicBridge([b"x"], hold_open=True)
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        first = source.start()
        await _take(first, 1)
        second = source.start()
        try:
            got = await _take(second, 1)
            assert await _eventually(lambda: bridge.active == 1)
            return bridge.started, bridge.active, first.qsize(), got
        finally:
            source.stop()

    started, active, first_backlog, got = run(scenario())
    assert started == 2
    assert active == 1  # only the second pump's stream is still open
    assert first_backlog == 0  # nothing more reached the abandoned queue
    assert got == [b"x"]


def test_mic_source_is_restartable_with_a_fresh_queue() -> None:
    async def scenario() -> tuple[bool, int, list[bytes]]:
        bridge = FakeMicBridge([b"1", b"2"])
        source = RobotMicSource(cast(ReachyMiniBridge, bridge))
        first = source.start()
        got_first = await _take(first, 2)
        source.stop()
        await asyncio.sleep(0.01)
        second = source.start()  # restart after stop
        try:
            got_second = await _take(second, 2)
            return (second is not first, bridge.started, got_first + got_second)
        finally:
            source.stop()

    fresh_queue, starts, chunks = run(scenario())
    assert fresh_queue  # each start() hands back a new queue
    assert starts == 2  # the upstream stream was re-read
    assert chunks == [b"1", b"2", b"1", b"2"]


# --- RobotCameraFeed ------------------------------------------------------------------------------


def test_camera_feed_forwards_the_bridges_feed_without_copying() -> None:
    """`latest()` is the bridge's very frame object (shared, read-only, the one its detector read),
    `published_count` its count; `None` before the session's first frame."""
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)
    feed = embodiment.camera(fps=5.0)
    assert feed.latest() is None and feed.published_count == 0
    published = bridge.camera.publish()
    assert feed.latest() is published  # the same object, not a copy
    assert feed.published_count == 1
    bridge.camera.publish()
    latest = feed.latest()
    assert latest is not None and latest.frame_id == 2 and feed.published_count == 2
    assert latest.image is bridge.camera.frame
    assert feed.capture_error is None  # the feed has no error of its own to report


def test_camera_feed_lifecycle_never_touches_the_bridges_feed() -> None:
    """`open()`/`close()` are no-ops: the feed is the session's, and stopping it would blind the
    bridge's face detection."""
    bridge = FakeReachyMiniBridge()
    feed = RobotCameraFeed(cast(ReachyMiniBridge, bridge))
    feed.open()
    feed.close()
    feed.open()
    assert (bridge.camera.starts, bridge.camera.stops) == (0, 0)


def test_a_sign_on_the_robots_feed_reaches_the_gesture_input() -> None:
    """The robot path end to end: the bridge's feed → `RobotCameraFeed` → the real hand stage and
    classifier (scripted backends) → a delivered sign. No `StreamProvider`, no loop hop."""
    bridge = FakeReachyMiniBridge()
    embodiment = _entered(bridge)
    deliveries: list[tuple[int, str | None]] = []
    gesture = GestureInput(
        embodiment.camera(fps=100.0),
        on_gesture=lambda hand, sign: deliveries.append((hand, sign)),
        fps=100.0,
        hold_s=0.0,
        models=GestureModels(
            detector=ScriptedDetector(),
            classifier=ScriptedClassifier(_scores("fist", 0.9)),
        ),
    )
    gesture.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and deliveries != [(1, "stop")]:
            bridge.camera.publish()  # the bridge's reader thread, played by the test
            time.sleep(0.01)
        assert deliveries == [(1, "stop")]
        assert gesture.status()["capture_error"] is None
    finally:
        gesture.stop()
    assert (bridge.camera.starts, bridge.camera.stops) == (0, 0)
