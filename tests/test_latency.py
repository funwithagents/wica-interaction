"""The latency collector (`latency.py`): spans and WICA's records joined into one record per
reaction (specs/latency-view.md).

Each test builds the spans a real run would produce — the recognizer's, WICA's, a voice's —
with explicit times through the process's provider, and emits WICA's events through a stand-in
for the agent, then reads the collector the way the page does (`snapshot()`).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from wica.events import Event
from wica.instrumentation import CommandTrace, ReactionTrace, TokenUsage, TriggerTrace
from wica.world import WorldEntry, WorldEntryVersion

from wica_interaction import latency
from wica_interaction.latency import (
    EffectStart,
    LatencyCollector,
    PerceptionStep,
    ReactionRecord,
    install_tracing,
    nearest_rank,
    perception,
    wall_clock_ns,
)

T0 = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ns(seconds: float) -> int:
    return int(at(seconds).timestamp() * 1e9)


class FakeWica:
    """The four events the collector subscribes to, emitted by the test."""

    def __init__(self) -> None:
        self.on_agent_reaction_ended: Event[ReactionTrace] = Event()
        self.on_agent_command_ended: Event[CommandTrace] = Event()
        self.on_agent_trigger: Event[WorldEntry] = Event()
        self.on_agent_trigger_dropped: Event[WorldEntry] = Event()


SAY = EffectStart(
    "tts_engine.utterance",
    "playback_started",
    latency_attribute="tts_engine.output_latency_seconds",
    note="the device started",
    label="the first sound",
)
EMOTE = EffectStart("reachy_mini_bridge.play_emotion", "motion_started", note="moves")


@pytest.fixture
def collector() -> Iterator[LatencyCollector]:
    collector = LatencyCollector()
    assert install_tracing(collector, environ={}) == []
    collector.set_effects({"say": SAY, "play_emotion": EMOTE}, output_command="say")
    yield collector
    install_tracing(
        LatencyCollector(), environ={}
    )  # detach: later tests get a fresh target


@pytest.fixture
def wica(collector: LatencyCollector) -> FakeWica:
    fake = FakeWica()
    collector.attach(fake)  # type: ignore[arg-type]
    return fake


_tracer = trace.get_tracer("tests")


def span(
    name: str,
    start: float,
    *,
    parent: Any = None,
    attributes: dict[str, Any] | None = None,
    events: dict[str, float | tuple[float, dict[str, Any]]] | None = None,
    end: float | None = None,
) -> Any:
    context = (
        trace.set_span_in_context(parent, otel_context.Context())
        if parent is not None
        else otel_context.Context()
    )
    s = _tracer.start_span(
        name, context=context, start_time=ns(start), attributes=attributes
    )
    for event, when in (events or {}).items():
        when, attrs = when if isinstance(when, tuple) else (when, {})
        s.add_event(event, attributes=attrs, timestamp=ns(when))
    if end is not None:
        s.end(end_time=ns(end))
    return s


def reaction_trace(
    rid: int,
    *,
    key: str = "speech_input",
    version: int = 1,
    written: float,
    calls: tuple[str, ...] = (),
    first_output: float | None = None,
    ended: float | None = None,
    **fields: Any,
) -> ReactionTrace:
    """A reaction 0.01 s after its write, through a 0.2 s window, a 0.4 s model call."""
    w = written
    values: dict[str, Any] = {
        "reaction_id": rid,
        "triggers": (
            TriggerTrace(
                key, version, at(w), at(w + 0.01), key.startswith("agent:command:")
            ),
        ),
        "window_opened_at": at(w + 0.01),
        "window_closed_at": at(w + 0.21),
        "prompt_ready_at": at(w + 0.22),
        "model_started_at": at(w + 0.22),
        "model_ended_at": at(w + 0.62),
        "outcome": "ok",
        "error": None,
        "text_length": 10,
        "command_call_ids": calls,
        "noop": not calls,
        "usage": None,
        "ended_at": at(ended if ended is not None else w + 0.62),
        "trace_id": None,
        "span_id": None,
        "first_content_at": at(w + 0.42),
        "first_output_at": at(first_output) if first_output is not None else None,
    }
    values.update(fields)
    return ReactionTrace(**values)


def entry(key: str, value: Any, version: int = 1) -> WorldEntry:
    return WorldEntry(key, str, False, WorldEntryVersion(version, value, T0), None)


def spoken_turn(
    wica: FakeWica,
    *,
    rid: int = 1,
    start: float = 0.0,
    vad: bool = True,
    playback: bool = True,
    say_ends: float | None = 3.0,
) -> None:
    """A spoken turn: speech ends at *start*, decided 0.3 s later, finalized at 0.5 s, written at
    0.52 s; the reaction speaks from 1.2 s, its first sound at 1.5 s plus a 20 ms device latency."""
    s = start
    asr_attrs: dict[str, Any] = {
        "asr_engine.speech_end_source": "vad" if vad else "none"
    }
    if vad:
        asr_attrs["asr_engine.speech_started_unix_ns"] = ns(s - 1.0)
    asr = span(
        "asr_engine.segment",
        s if vad else s + 0.5,
        attributes=asr_attrs,
        events={"end_decided": s + 0.3, "finalized": s + 0.5}
        if vad
        else {"finalized": s + 0.5},
    )
    update = span("wica.world.update", s + 0.52, parent=asr, end=s + 0.52)
    asr.end(end_time=ns(s + 0.53))
    reaction = span(
        "wica.agent.reaction",
        s + 0.73,
        parent=update,
        attributes={"wica.reaction_id": rid},
    )
    command = span(
        "wica.agent.command",
        s + 1.2,
        parent=reaction,
        attributes={"wica.call_id": f"c{rid}", "wica.command.name": "say"},
    )
    events: dict[str, Any] = {
        "admitted": s + 1.2,
        "first_text": s + 1.21,
        "first_segment": s + 1.3,
        "first_audio": s + 1.45,
    }
    if playback:
        events["playback_started"] = (
            s + 1.5,
            {"tts_engine.output_latency_seconds": 0.02},
        )
    utterance = span("tts_engine.utterance", s + 1.2, parent=command, events=events)
    wica.on_agent_trigger.emit(entry("speech_input", "hello"))
    reaction.end(end_time=ns(s + 1.6))
    wica.on_agent_reaction_ended.emit(
        reaction_trace(
            rid,
            written=s + 0.52,
            calls=(f"c{rid}",),
            first_output=s + 1.2,
            ended=s + 1.6,
        )
    )
    if say_ends is not None:
        utterance.end(end_time=ns(s + say_ends))
        command.end(end_time=ns(s + say_ends))
        wica.on_agent_command_ended.emit(
            CommandTrace(
                f"c{rid}",
                "say",
                rid,
                at(s + 1.2),
                at(s + say_ends),
                "complete",
                is_output=True,
            )
        )


def only(collector: LatencyCollector) -> ReactionRecord:
    records = collector.snapshot().records
    assert len(records) == 1
    return records[0]


def step(record: ReactionRecord, name: str) -> latency.Step:
    return next(s for s in record.steps if s.name == name)


def seconds(s: latency.Step) -> float:
    assert s.ended_at is not None
    return round((s.ended_at - s.started_at).total_seconds(), 3)


# --- Speech -------------------------------------------------------------------------------------


def test_a_spoken_turn_runs_from_the_end_of_speech_to_the_first_sound(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica)
    record = only(collector)
    assert record.trigger == "speech" and record.label == "speech_input = 'hello'"
    assert record.origin_at == at(0) and record.origin_measured
    # The first sound: playback started at 1.5 s, plus the device's reported 20 ms.
    assert record.total == pytest.approx(1.52)
    assert record.effect_command == "say" and record.effect_note == "the device started"
    assert (
        seconds(step(record, "waiting for end of turn")),
        step(record, "waiting for end of turn").kind,
    ) == (
        0.3,
        "wait",
    )
    assert seconds(step(record, "final transcript")) == 0.2
    assert seconds(step(record, "gathering inputs")) == 0.2
    speaking = step(record, "you speaking")
    assert (speaking.lane, seconds(speaking)) == ("You", 1.0)
    # The voice's own steps, in time order, up to the effect, then what was heard.
    voice = [s.name for s in record.steps if s.lane == "Voice"]
    assert voice == [
        "admitted",
        "first_text",
        "first_segment",
        "first_audio",
        "playback_started",
        "device latency",
        "heard",
    ]
    shares = record.shares
    assert shares is not None
    assert (
        round(shares.input, 3),
        round(shares.agent, 3),
        round(shares.output, 3),
    ) == (0.52, 0.68, 0.32)
    # The two waits apart: the end of turn in the input, the gathering of inputs in the agent.
    assert (round(shares.input_wait, 3), round(shares.agent_wait, 3)) == (0.3, 0.2)
    assert (round(shares.input_work, 3), round(shares.agent_work, 3)) == (0.22, 0.48)
    assert record.complete and record.counted is False  # the first reaction is cold


def test_without_an_end_of_speech_the_measure_starts_at_the_final(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, vad=False)
    record = only(collector)
    assert record.origin_at == at(0.5) and not record.origin_measured
    assert "origin unknown" in record.flags
    assert step(record, "before the final transcript").kind == "not_measured"
    assert not any(s.name == "waiting for end of turn" for s in record.steps)


def test_a_dictation_closed_by_its_silence_timer_shows_the_timer_as_a_wait(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    asr = span(
        "asr_engine.segment",
        0,
        attributes={
            "asr_engine.speech_end_source": "vad",
            "asr_engine.segmentation.end_of_speech_timeout_s": 2.0,
        },
        events={"end_decided": 0.3, "finalized": 2.6},
    )
    update = span("wica.world.update", 2.62, parent=asr, end=2.62)
    asr.end(end_time=ns(2.63))
    span(
        "wica.agent.reaction",
        2.83,
        parent=update,
        attributes={"wica.reaction_id": 1},
        end=3.3,
    )
    wica.on_agent_reaction_ended.emit(reaction_trace(1, written=2.62))
    record = only(collector)
    assert seconds(step(record, "final transcript")) == 0.3
    timer = step(record, "dictation silence timer")
    assert (timer.kind, seconds(timer)) == ("wait", 2.0)


# --- The other triggers ---------------------------------------------------------------------------


def test_typed_text_is_measured_from_its_write(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    update = span("wica.world.update", 0, end=0)
    reaction = span(
        "wica.agent.reaction", 0.21, parent=update, attributes={"wica.reaction_id": 1}
    )
    command = span(
        "wica.agent.command",
        0.5,
        parent=reaction,
        attributes={"wica.call_id": "c1", "wica.command.name": "wave"},
    )
    reaction.end(end_time=ns(0.62))
    command.end(end_time=ns(0.9))
    wica.on_agent_reaction_ended.emit(reaction_trace(1, written=0, calls=("c1",)))
    wica.on_agent_command_ended.emit(
        CommandTrace("c1", "wave", 1, at(0.5), at(0.9), "complete")
    )
    record = only(collector)
    assert (record.trigger, record.origin_at, record.origin_measured) == (
        "typed",
        at(0),
        True,
    )
    # No declared effect: the Command's effect starts with it, and it has no steps of its own.
    assert record.total == pytest.approx(0.5)
    assert [s.name for s in record.steps if s.lane == "Actions"] == ["running"]


def test_a_hand_sign_is_measured_from_its_first_frame(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    origin = time.time_ns() - 1_000_000_000
    steps = [
        PerceptionStep("HandStage queue", "wait", origin, origin + 20_000_000),
        PerceptionStep("HandStage", "work", origin + 20_000_000, origin + 60_000_000),
        PerceptionStep(
            "holding the sign", "wait", origin + 60_000_000, origin + 360_000_000
        ),
    ]
    with perception("hand sign", origin, steps):
        update = _tracer.start_span(
            "wica.world.update", start_time=origin + 370_000_000
        )
        update.end(end_time=origin + 370_000_000)
    written = datetime.fromtimestamp((origin + 370_000_000) / 1e9, UTC)
    reaction = _tracer.start_span(
        "wica.agent.reaction",
        context=trace.set_span_in_context(update),
        attributes={"wica.reaction_id": 1},
    )
    reaction.end()
    w = (written - T0).total_seconds()
    wica.on_agent_reaction_ended.emit(
        reaction_trace(1, key="hand_gesture_1", written=w)
    )
    record = only(collector)
    assert record.trigger == "hand sign" and record.origin_measured
    assert (
        abs(
            (
                record.origin_at - datetime.fromtimestamp(origin / 1e9, UTC)
            ).total_seconds()
        )
        < 1e-5
    )
    seen = [(s.name, s.kind) for s in record.steps if s.lane == "Seeing"]
    assert seen == [
        ("HandStage queue", "wait"),
        ("HandStage", "work"),
        ("holding the sign", "wait"),
        ("handed to the agent", "work"),
    ]
    assert seconds(step(record, "holding the sign")) == 0.3


def test_the_perception_span_is_a_root_whatever_was_current() -> None:
    collector = LatencyCollector()
    install_tracing(collector, environ={})
    with (
        _tracer.start_as_current_span("an earlier reaction's command"),
        perception("user event", time.time_ns()),
    ):
        current = trace.get_current_span()
    assert current.get_span_context().is_valid
    readable: Any = current
    assert readable.parent is None and readable.name == latency.SPAN_PERCEPTION


def test_a_sign_written_with_no_span_of_its_own_lacks_its_origin(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    update = span("wica.world.update", 0, end=0)
    span(
        "wica.agent.reaction",
        0.21,
        parent=update,
        attributes={"wica.reaction_id": 1},
        end=0.62,
    )
    wica.on_agent_reaction_ended.emit(
        reaction_trace(1, key="hand_gesture_2", written=0)
    )
    record = only(collector)
    assert record.trigger == "hand sign" and "origin unknown" in record.flags


def test_a_command_completion_and_a_next_note_are_follow_ups(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, rid=1)
    # The say ended at 3.0 s: its completion wakes reaction 2, which writes a <next> note …
    wica.on_agent_reaction_ended.emit(
        reaction_trace(2, key="agent:command:c1", written=3.0, outcome="ok")
    )
    # … that wakes reaction 3.
    wica.on_agent_reaction_ended.emit(reaction_trace(3, key="agent:next", written=4.0))
    records = {r.reaction_id: r for r in collector.snapshot().records}
    assert (records[2].trigger, records[2].parent_reaction_id) == ("follow-up", 1)
    assert (records[3].trigger, records[3].parent_reaction_id) == ("follow-up", 2)
    assert records[2].origin_at == at(3.0)
    assert not records[2].counted


# --- Effects ---------------------------------------------------------------------------------


def test_a_reaction_that_speaks_and_acts_ends_at_whichever_is_perceived_first(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    update = span("wica.world.update", 0, end=0)
    reaction = span(
        "wica.agent.reaction", 0.21, parent=update, attributes={"wica.reaction_id": 1}
    )
    say = span(
        "wica.agent.command",
        0.5,
        parent=reaction,
        attributes={"wica.call_id": "s", "wica.command.name": "say"},
    )
    span(
        "tts_engine.utterance",
        0.5,
        parent=say,
        events={"playback_started": 1.4},
        end=2.0,
    )
    emote = span(
        "wica.agent.command",
        0.9,
        parent=reaction,
        attributes={"wica.call_id": "e", "wica.command.name": "play_emotion"},
    )
    span(
        "reachy_mini_bridge.play_emotion",
        0.9,
        parent=emote,
        events={"queued": 0.95, "motion_started": 1.1, "trajectory_started": 1.5},
        end=3.0,
    )
    for s in (reaction, say, emote):
        s.end(end_time=ns(3.0))
    wica.on_agent_reaction_ended.emit(
        reaction_trace(1, written=0, calls=("s", "e"), first_output=0.5)
    )
    wica.on_agent_command_ended.emit(
        CommandTrace("s", "say", 1, at(0.5), at(2.0), "complete", is_output=True)
    )
    wica.on_agent_command_ended.emit(
        CommandTrace("e", "play_emotion", 1, at(0.9), at(3.0), "complete")
    )
    record = only(collector)
    assert (record.effect_command, record.total) == ("play_emotion", pytest.approx(1.1))
    actions = [s.name for s in record.steps if s.lane == "Actions"]
    # The steps up to the motion, not after it.
    assert actions == ["queued", "motion_started", "moving"]


def test_a_declared_effect_that_never_came_is_not_the_command_start(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, playback=False)
    record = only(collector)
    assert record.effect_at is None and record.total is None
    assert step(record, "never perceived").kind == "not_measured"


def test_a_record_is_completed_in_place_while_the_voice_still_speaks(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, say_ends=None)
    record = only(collector)
    # The sound has started: the total is known though the say is still running.
    assert record.total == pytest.approx(1.52) and not record.complete
    assert step(record, "heard").ended_at is None


def test_a_reaction_still_running_shows_on_the_timeline(
    collector: LatencyCollector,
) -> None:
    update = span("wica.world.update", 0, end=0)
    reaction = span(
        "wica.agent.reaction", 0.21, parent=update, attributes={"wica.reaction_id": 9}
    )
    span(
        "wica.agent.command",
        0.5,
        parent=reaction,
        attributes={"wica.call_id": "x", "wica.command.name": "wave"},
    )
    snapshot = collector.snapshot()
    assert snapshot.records == ()
    assert [(s.name, s.lane, s.ended_at) for s in snapshot.running] == [
        ("the agent so far", "Agent", None),
        ("running", "Actions", None),
    ]


# --- Flags ---------------------------------------------------------------------------------------


def test_the_flags_explain_a_slow_reaction(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, rid=1)  # the cold one
    update = span("wica.world.update", 10, end=10)
    reaction = span(
        "wica.agent.reaction", 10.21, parent=update, attributes={"wica.reaction_id": 2}
    )
    say = span(
        "wica.agent.command",
        10.5,
        parent=reaction,
        attributes={"wica.call_id": "c2", "wica.command.name": "say"},
    )
    # The voice waited 0.3 s behind another utterance before it was admitted.
    span(
        "tts_engine.utterance",
        10.5,
        parent=say,
        events={"admitted": 10.8, "playback_started": 11.0},
        end=12,
    )
    say.end(end_time=ns(12))
    reaction.end(end_time=ns(10.9))
    wica.on_agent_trigger_dropped.emit(
        entry("speech_input", "ignored")
    )  # now: outside both
    wica.on_agent_reaction_ended.emit(
        reaction_trace(
            2,
            written=10,
            calls=("c2",),
            attempts=2,
            guard_duration=0.08,
            usage=TokenUsage(100, 10, 0),
        )
    )
    wica.on_agent_command_ended.emit(
        CommandTrace("c2", "say", 2, at(10.5), at(12), "complete", is_output=True)
    )
    records = {r.reaction_id: r for r in collector.snapshot().records}
    assert "first (cold)" in records[1].flags
    assert records[2].flags == ("2 attempts", "guards 80 ms", "uncached", "queued")


def test_an_input_dropped_while_a_reaction_ran_is_flagged(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    now = datetime.now(UTC)
    w = (
        now - T0
    ).total_seconds() - 0.3  # the drop lands inside the reaction's busy time
    update = span("wica.world.update", w, end=w)
    span(
        "wica.agent.reaction",
        w + 0.21,
        parent=update,
        attributes={"wica.reaction_id": 1},
        end=w + 0.62,
    )
    wica.on_agent_trigger_dropped.emit(entry("speech_input", "too soon"))
    wica.on_agent_reaction_ended.emit(reaction_trace(1, written=w, noop=True))
    record = only(collector)
    assert {"input dropped", "noop"} <= set(record.flags)
    assert collector.snapshot().dropped


# --- Statistics -------------------------------------------------------------------------------------


def test_the_statistics_count_complete_measured_reactions_by_trigger(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    for rid in range(1, 6):  # five spoken turns, 10 s apart: the first is cold
        spoken_turn(wica, rid=rid, start=10.0 * rid)
    spoken_turn(
        wica, rid=6, start=60.0, vad=False
    )  # origin unknown: listed, not counted
    snapshot = collector.snapshot()
    assert len(snapshot.records) == 6
    (speech,) = snapshot.statistics
    assert (speech.trigger, speech.count) == ("speech", 4)
    assert speech.median == pytest.approx(1.52) and speech.p90 == pytest.approx(1.52)
    collector.reset_statistics()
    assert collector.snapshot().statistics == ()
    spoken_turn(wica, rid=7, start=70.0)
    assert collector.snapshot().statistics[0].count == 1


def test_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert nearest_rank(values, 0.9) == 9.0
    assert nearest_rank([3.0], 0.9) == 3.0
    assert nearest_rank([5.0, 1.0], 0.5) == 1.0


def test_the_records_kept_are_bounded(wica: FakeWica) -> None:
    collector = LatencyCollector(max_records=3)
    collector.attach(wica)  # type: ignore[arg-type]
    for rid in range(1, 6):
        wica.on_agent_reaction_ended.emit(reaction_trace(rid, written=float(rid)))
    assert [r.reaction_id for r in collector.snapshot().records] == [5, 4, 3]


# --- Installing the SDK, the clock -----------------------------------------------------------------


def test_a_second_system_takes_the_collection_over() -> None:
    first, second = LatencyCollector(), LatencyCollector()
    install_tracing(first, environ={})
    install_tracing(second, environ={})
    span("wica.agent.reaction", 0, attributes={"wica.reaction_id": 1})
    assert second.snapshot().running and not first.snapshot().running


def test_an_unknown_exporter_is_a_banner_line_and_collection_goes_on() -> None:
    collector = LatencyCollector()
    lines = install_tracing(collector, environ={latency.TRACES_ENV: "jaeger"})
    assert lines == [
        "Latency: WICA_INTERACTION_TRACES='jaeger' is not `console` or `otlp`; no traces are exported"
    ]
    span("wica.agent.reaction", 0, attributes={"wica.reaction_id": 1})
    assert collector.snapshot().running


def test_a_perf_counter_stamp_converts_to_wall_clock() -> None:
    stamp = time.perf_counter() - 0.25
    converted = wall_clock_ns(stamp)
    assert abs((time.time_ns() - converted) / 1e9 - 0.25) < 0.01


# --- The speech head, in plain words ---------------------------------------------------------------


def test_the_end_of_turn_names_who_decided_it(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    asr = span(
        "asr_engine.segment",
        0,
        attributes={
            "asr_engine.speech_end_source": "vad",
            "asr_engine.end_decided_by": "vad_gate",
            "asr_engine.vad.min_silence_duration_ms": 300,
        },
        events={"end_decided": 0.3, "finalized": 0.9},
    )
    update = span("wica.world.update", 0.92, parent=asr, end=0.92)
    asr.end(end_time=ns(0.93))
    span(
        "wica.agent.reaction",
        1.1,
        parent=update,
        attributes={"wica.reaction_id": 1},
        end=1.5,
    )
    wica.on_agent_reaction_ended.emit(reaction_trace(1, written=0.92))
    record = only(collector)
    assert step(record, "waiting for end of turn").detail == "silence 300 ms"
    # No partial before the end of turn: the recognizer transcribed it all afterwards.
    assert (
        step(record, "final transcript").detail
        == "the whole transcription: no partial came before"
    )


def test_a_streaming_recognizer_transcribes_as_the_person_speaks(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    # The first partial arrived 0.6 s into the speech, which started a second before it ended.
    collector.speech(at(-1.0), None, partial_at=at(-0.4))
    spoken_turn(wica)
    record = only(collector)
    bar = step(record, "transcribing as you speak")
    assert (bar.started_at, bar.ended_at, bar.context) == (at(-0.4), at(0.5), True)
    shares = record.shares
    # Context: it overlaps the speech and the end of turn, and counts in no share.
    assert shares is not None
    assert (round(shares.input_wait, 3), round(shares.agent_wait, 3)) == (0.3, 0.2)
    assert (
        step(record, "final transcript").detail
        == "what was left to transcribe after the end of turn"
    )


# --- The timeline while things run ----------------------------------------------------------------


def test_a_running_command_whose_effect_is_not_stamped_yet_is_drawn_up_to_now(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    update = span("wica.world.update", 0, end=0)
    reaction = span(
        "wica.agent.reaction", 0.21, parent=update, attributes={"wica.reaction_id": 1}
    )
    emote = span(
        "wica.agent.command",
        0.9,
        parent=reaction,
        attributes={"wica.call_id": "e", "wica.command.name": "play_emotion"},
    )
    # The bridge stamps `queued` live; the motion only when the call ends.
    span("reachy_mini_bridge.play_emotion", 0.9, parent=emote, events={"queued": 0.95})
    reaction.end(end_time=ns(1.0))
    wica.on_agent_reaction_ended.emit(reaction_trace(1, written=0, calls=("e",)))
    record = only(collector)
    actions = [(s.name, s.ended_at) for s in record.steps if s.lane == "Actions"]
    assert actions == [("queued", at(0.95)), ("in progress", None)]


def test_a_follow_up_does_not_borrow_the_input_of_the_reaction_before(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, rid=1, say_ends=None)
    # The say's completion is written from inside its span: the next reaction descends from it.
    command = collector._spans[collector._command_span["c1"]].span  # pyright: ignore[reportPrivateUsage]
    completion = span("wica.world.update", 3.0, parent=command, end=3.0)
    span(
        "wica.agent.reaction",
        3.2,
        parent=completion,
        attributes={"wica.reaction_id": 2},
    )
    running = collector.snapshot().running
    assert running and not any(s.lane == "Hearing" for s in running)


def test_a_record_says_where_its_measure_starts_and_ends(
    collector: LatencyCollector, wica: FakeWica
) -> None:
    spoken_turn(wica, rid=1)
    wica.on_agent_reaction_ended.emit(
        reaction_trace(2, key="agent:command:c1", written=3.0)
    )
    records = {r.reaction_id: r for r in collector.snapshot().records}
    assert (records[1].origin_label, records[1].effect_label) == (
        "your last word",
        "the first sound",
    )
    assert records[2].origin_label == "the end of say"
    # A Command with no declaration ends the measure as it starts.
    update = span("wica.world.update", 10, end=10)
    reaction = span(
        "wica.agent.reaction", 10.2, parent=update, attributes={"wica.reaction_id": 3}
    )
    span(
        "wica.agent.command",
        10.5,
        parent=reaction,
        attributes={"wica.call_id": "w", "wica.command.name": "wave"},
        end=10.9,
    )
    reaction.end(end_time=ns(10.6))
    wica.on_agent_reaction_ended.emit(reaction_trace(3, written=10, calls=("w",)))
    wica.on_agent_command_ended.emit(
        CommandTrace("w", "wave", 3, at(10.5), at(10.9), "complete")
    )
    typed = next(r for r in collector.snapshot().records if r.reaction_id == 3)
    assert (typed.origin_label, typed.effect_label) == (
        "the message sent",
        "wave starting",
    )
