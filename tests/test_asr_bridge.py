"""Functional tests for ``AsrBridge`` — driven against a fake ``ASREngine`` (no audio, no network).

Tests push real ``SpeechSegment``s through ``bridge._on_segment`` (the engine's segment callback) and
assert on the display view, the ``on_final_segment`` callback, and the dictation commands' non-raising
string contract. See specs/asr-bridge.md ("Testing").

The bridge's methods are coroutines; tests drive them with ``asyncio.run`` (no async-test plugin
required). State lives on the bridge/engine objects, so independent event loops across calls are fine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, cast

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment
from reachy_mini_bridge.api import ReachyMiniApi

from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeASREngine:
    """Minimal stand-in exposing only what ``AsrBridge`` reads/calls.

    ``start_dictation`` / ``stop_dictation`` record their calls and update ``dictating`` /
    ``segmentation_mode``; set ``raise_on_start_dictation`` / ``raise_on_stop_dictation`` to a message
    to exercise the bridge's error path.
    """

    def __init__(
        self, *, running: bool = True, segmentation_mode: str = "utterance"
    ) -> None:
        # AsrBridge.__init__ overwrites this with its own _on_segment.
        self.on_speech_segment = None
        self._running = running
        self.dictating = False
        self.segmentation_mode = segmentation_mode
        self.start_dictation_calls: list[dict] = []
        self.stop_dictation_calls = 0
        self.raise_on_start_dictation: str | None = None
        self.raise_on_stop_dictation: str | None = None

    def status(self) -> dict:
        return {"running": self._running, "connected": True}

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def start_dictation(
        self, end_on_final_segment: bool = True, segmentation_mode: str | None = None
    ) -> None:
        self.start_dictation_calls.append(
            {
                "end_on_final_segment": end_on_final_segment,
                "segmentation_mode": segmentation_mode,
            }
        )
        if self.raise_on_start_dictation is not None:
            raise ValueError(self.raise_on_start_dictation)
        self.dictating = True
        self.segmentation_mode = segmentation_mode or "timeout"

    async def stop_dictation(self) -> None:
        self.stop_dictation_calls += 1
        if self.raise_on_stop_dictation is not None:
            raise ValueError(self.raise_on_stop_dictation)
        self.dictating = False
        self.segmentation_mode = "utterance"


def _make_bridge(engine: FakeASREngine) -> tuple[AsrBridge, list[str]]:
    """Build a bridge over *engine* with a recording ``on_final_segment``; return both."""
    finals: list[str] = []
    bridge = AsrBridge(cast(ASREngine, engine), on_final_segment=finals.append)
    return bridge, finals


def _segment(transcript: str, *, is_final: bool) -> SpeechSegment:
    return SpeechSegment(
        transcript=transcript,
        is_final=is_final,
        end_reason="utterance" if is_final else None,
        utterances=[],
    )


def test_constructor_wires_segment_callback() -> None:
    engine = FakeASREngine()
    bridge, _ = _make_bridge(engine)
    assert engine.on_speech_segment == bridge._on_segment


def test_interim_segment_is_visible_but_inert() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hello wor", is_final=False)))

    assert bridge.display_text == "hello wor"
    assert finals == []  # the World is not touched — the agent is not triggered


def test_final_segment_triggers_once_and_clears_display() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hello world", is_final=True)))

    assert finals == ["hello world"]
    assert bridge.display_text == ""


def test_interim_interim_final_yields_one_call_with_final_text() -> None:
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("hel", is_final=False)))
    run(bridge._on_segment(_segment("hello wor", is_final=False)))
    run(bridge._on_segment(_segment("hello world", is_final=True)))

    assert finals == ["hello world"]  # exactly one utterance, once
    assert bridge.display_text == ""


def test_empty_final_still_fires() -> None:
    """A final that closes on silence still wakes the agent (with "")."""
    engine = FakeASREngine()
    bridge, finals = _make_bridge(engine)

    run(bridge._on_segment(_segment("", is_final=True)))

    assert finals == [""]
    assert bridge.display_text == ""


def test_start_dictation_wires_engine_and_reports_mode() -> None:
    engine = FakeASREngine()
    bridge, _ = _make_bridge(engine)

    result = run(bridge.start_dictation("timeout"))

    assert engine.start_dictation_calls == [
        {"end_on_final_segment": True, "segmentation_mode": "timeout"}
    ]
    assert "timeout" in result
    assert bridge.status() == {
        "running": True,
        "dictating": True,
        "segmentation_mode": "timeout",
        "capture_error": None,
    }


def test_start_dictation_catches_error() -> None:
    engine = FakeASREngine()
    engine.raise_on_start_dictation = "ASR is not running."
    bridge, _ = _make_bridge(engine)

    result = run(bridge.start_dictation())

    assert "ASR is not running." in result  # readable string, not an exception
    assert not bridge.status()["dictating"]


def test_stop_dictation_happy_and_error_paths() -> None:
    engine = FakeASREngine()
    engine.dictating = True
    bridge, _ = _make_bridge(engine)

    assert run(bridge.stop_dictation()) == "Dictation stopped."
    assert engine.stop_dictation_calls == 1
    assert not bridge.status()["dictating"]

    engine.raise_on_stop_dictation = "No dictation in progress."
    result = run(bridge.stop_dictation())
    assert "No dictation in progress." in result


def test_status_composes_engine_accessors() -> None:
    engine = FakeASREngine(running=False, segmentation_mode="trigger_word")
    engine.dictating = True
    bridge, _ = _make_bridge(engine)

    assert bridge.status() == {
        "running": False,
        "dictating": True,
        "segmentation_mode": "trigger_word",
        "capture_error": None,  # no mic source handed to the bridge
    }


def test_start_and_stop_delegate_to_engine() -> None:
    engine = FakeASREngine(running=False)
    bridge, _ = _make_bridge(engine)

    run(bridge.start())
    assert bridge.status()["running"] is True

    run(bridge.stop())
    assert bridge.status()["running"] is False


# --- RobotMicSource (driven against a fake ReachyMiniApi mic stream) ----------------------------


class FakeMicApi:
    """Minimal stand-in for ``ReachyMiniApi.audio_input`` — an async iterator of PCM ``bytes``.

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


def test_robot_mic_source_streams_chunks_in_order() -> None:
    async def scenario() -> list[bytes]:
        api = FakeMicApi([b"aa", b"bbbb", b"c"])
        source = RobotMicSource(cast(ReachyMiniApi, api))
        queue = source.start()
        try:
            return await _take(queue, 3)
        finally:
            source.stop()

    assert run(scenario()) == [b"aa", b"bbbb", b"c"]


def test_robot_mic_source_stop_cancels_pump() -> None:
    async def scenario() -> tuple[int, bool, str | None]:
        api = FakeMicApi([b"x"], hold_open=True)
        source = RobotMicSource(cast(ReachyMiniApi, api))
        queue = source.start()
        await _take(queue, 1)  # the stream is live
        assert api.active == 1
        source.stop()
        closed = await _eventually(
            lambda: api.active == 0
        )  # the cancelled pump closed the stream
        return api.active, closed, source.capture_error

    active, closed, error = run(scenario())
    assert closed and active == 0
    assert error is None  # a deliberate stop() is not a capture failure


def test_pump_error_is_recorded_and_surfaced() -> None:
    """A mic stream that raises is not silent: the source records why, and the bridge's status (which
    the ASR panel renders) carries it even though the engine itself still reports running."""

    async def scenario() -> tuple[list[bytes], RobotMicSource]:
        api = FakeMicApi([b"a"], raise_after="usb unplugged")
        source = RobotMicSource(cast(ReachyMiniApi, api))
        queue = source.start()
        got = await _take(queue, 1)
        assert await _eventually(lambda: source.capture_error is not None)
        return got, source

    got, source = run(scenario())
    assert got == [b"a"]  # audio before the failure still reached the engine
    assert source.capture_error is not None and "usb unplugged" in source.capture_error

    bridge = AsrBridge(
        cast(ASREngine, FakeASREngine(running=True)),
        on_final_segment=lambda _t: None,
        mic=source,
    )
    status = bridge.status()
    assert status["running"] is True
    assert (
        status["capture_error"] is not None
        and "usb unplugged" in status["capture_error"]
    )


def test_stream_end_is_recorded_and_a_restart_clears_it() -> None:
    async def scenario() -> tuple[str | None, str | None]:
        api = FakeMicApi([b"a"], hold_open=False)  # ends after one chunk
        source = RobotMicSource(cast(ReachyMiniApi, api))
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
        api = FakeMicApi(chunks, hold_open=True)
        source = RobotMicSource(cast(ReachyMiniApi, api), maxsize=4)
        queue = source.start()
        try:
            assert await _eventually(lambda: api.yielded == len(chunks))
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
        api = FakeMicApi([b"x"], hold_open=True)
        source = RobotMicSource(cast(ReachyMiniApi, api))
        first = source.start()
        await _take(first, 1)
        second = source.start()
        try:
            got = await _take(second, 1)
            assert await _eventually(lambda: api.active == 1)
            return api.started, api.active, first.qsize(), got
        finally:
            source.stop()

    started, active, first_backlog, got = run(scenario())
    assert started == 2
    assert active == 1  # only the second pump's stream is still open
    assert first_backlog == 0  # nothing more reached the abandoned queue
    assert got == [b"x"]


def test_robot_mic_source_is_restartable_with_a_fresh_queue() -> None:
    async def scenario() -> tuple[bool, int, list[bytes]]:
        api = FakeMicApi([b"1", b"2"])
        source = RobotMicSource(cast(ReachyMiniApi, api))
        first = source.start()
        got_first = await _take(first, 2)
        source.stop()
        await asyncio.sleep(0.01)
        second = source.start()  # restart after stop
        try:
            got_second = await _take(second, 2)
            return (second is not first, api.started, got_first + got_second)
        finally:
            source.stop()

    fresh_queue, starts, chunks = run(scenario())
    assert fresh_queue  # each start() hands back a new queue
    assert starts == 2  # the upstream stream was re-read
    assert chunks == [b"1", b"2", b"1", b"2"]
