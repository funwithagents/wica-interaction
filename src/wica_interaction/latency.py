"""How long the person waited, reaction by reaction (specs/latency-view.md).

The agent (WICA) and the libraries the bodies run (``asr-engine``, ``tts-engine``,
``reachy-mini-bridge``) open OpenTelemetry spans with an event at each step they know of. This
module installs the SDK once per process, collects those spans in memory together with WICA's own
records, and joins them per reaction — by span parentage only — into a ``ReactionRecord``: an origin
(the moment the person finished giving the input), every step after it, and the effect start (the
moment the reaction could first be perceived). The page draws records (latency_view.py); this module
draws nothing.

It also opens the app's own span for what it perceives itself (a hand sign, the robot's users):
``perception``, a root span backdated to the input's origin and current while the input is written
to the World, so the reaction joins it like a speech reaction joins the recognizer's span.

It imports WICA, OpenTelemetry and the recognizer's published names, and nothing else of this app.
"""

from __future__ import annotations

import logging
import math
import os
import statistics
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from asr_engine import segment_timing as asr_names
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from wica.instrumentation import (
    COMMAND_KEY_PREFIX,
    CommandTrace,
    ReactionOutcome,
    ReactionTrace,
    TriggerTrace,
    parent_trigger,
)
from wica.world import WorldEntry

if TYPE_CHECKING:
    from wica import Wica

logger = logging.getLogger(__name__)

# --- The names this app publishes: its own spans ------------------------------------------------

TRACER_NAME = "wica_interaction"
SPAN_PERCEPTION = "wica_interaction.perception"
SPAN_STEP = "wica_interaction.step"
ATTR_TRIGGER = "wica_interaction.trigger"
ATTR_STEP_NAME = "wica_interaction.step.name"
ATTR_STEP_KIND = "wica_interaction.step.kind"

# Adds an exporter on top of the in-process collection: `console` or `otlp`.
TRACES_ENV = "WICA_INTERACTION_TRACES"

# --- The names this module reads -----------------------------------------------------------------

# WICA's (wica.agent.*), the framework's own published spans.
_WICA_REACTION = "wica.agent.reaction"
_WICA_COMMAND = "wica.agent.command"
_WICA_REACTION_ID = "wica.reaction_id"
_WICA_CALL_ID = "wica.call_id"
_WICA_COMMAND_NAME = "wica.command.name"
# The World entry holding a <next> note: a reaction it wakes is a follow-up of the one before.
_NEXT_NOTE_KEY = "agent:next"
# tts-engine's utterance (specs/utterance-timing.md there), for the `queued` flag. Not imported:
# tts-engine is an extra, not a base dependency; tests/computer pins these against its constants.
TTS_UTTERANCE_SPAN = "tts_engine.utterance"
TTS_ADMITTED_EVENT = "admitted"

# --- Thresholds (specs/latency-view.md, "Flags", "The record") --------------------------------

MAX_RECORDS = 200
MAX_SPANS = 20_000
QUEUED_FLAG_S = 0.05
GUARDS_FLAG_S = 0.05
_MAX_SPEECH = 64
_MAX_DROPPED = 256
_MAX_LABELS = 1024

StepKind = Literal["work", "wait", "not_measured", "effect"]
Lane = Literal["You", "Hearing", "Seeing", "Agent", "Voice", "Actions"]
LANES: tuple[Lane, ...] = ("You", "Hearing", "Seeing", "Agent", "Voice", "Actions")


@dataclass(frozen=True)
class EffectStart:
    """When a Command's effect can first be perceived, as an embodiment declares it: an event on
    a span nested under the Command's own span (specs/latency-view.md, "`EffectStart`")."""

    span: str
    event: str
    # An attribute of that event, in seconds, added to its time (a device's output latency).
    latency_attribute: str | None = None
    # A delay the body knows, in seconds, added to its time.
    latency_s: float | None = None
    # What leaves out or adds to this moment, shown under the waterfall's title.
    note: str = ""
    # The moment in a few words, ending the waterfall's title ("the first sound").
    label: str = ""


@dataclass(frozen=True)
class Step:
    name: str
    lane: Lane
    started_at: datetime
    ended_at: datetime | None  # None while it runs
    kind: StepKind
    command: str | None = None  # the Command a tail step belongs to
    # What this step was, said plainly: who decided an end of turn, what a step leaves out.
    detail: str = ""
    # Drawn as context, overlapping the others, and never counted in a share.
    context: bool = False


@dataclass(frozen=True)
class Shares:
    """A total split into its phases — input (origin → the World write), agent (→ the effect
    Command's start), output (→ the effect) — and the input and the agent each into the
    configured wait inside it and the work around it, so the end of turn or a sign's hold is
    told apart from the agent gathering its inputs. The five sum to the total."""

    input_wait: float
    input_work: float
    agent_wait: float
    agent_work: float
    output: float

    @property
    def input(self) -> float:
        return self.input_wait + self.input_work

    @property
    def agent(self) -> float:
        return self.agent_wait + self.agent_work


@dataclass(frozen=True)
class ReactionRecord:
    reaction_id: int
    trigger: str  # "speech", "typed", "hand sign", "user event", "follow-up", or the entry's key
    label: str
    parent_reaction_id: int | None
    origin_at: datetime
    origin_measured: bool
    written_at: datetime
    steps: tuple[Step, ...]
    effect_at: datetime | None
    effect_command: str | None
    effect_note: str
    effect_command_started_at: datetime | None
    flags: tuple[str, ...]
    outcome: ReactionOutcome
    complete: bool  # every Command of the reaction has ended
    counted: bool = False  # enters the statistics (see Statistics)
    # Where the measure starts and where it ends, in a few words, for the waterfall's title.
    origin_label: str = ""
    effect_label: str = ""

    @property
    def total(self) -> float | None:
        if self.effect_at is None:
            return None
        return (self.effect_at - self.origin_at).total_seconds()

    @property
    def shares(self) -> Shares | None:
        if self.effect_at is None or self.effect_command_started_at is None:
            return None
        boundary = max(
            self.written_at, min(self.effect_command_started_at, self.effect_at)
        )
        written = max(self.written_at, self.origin_at)

        def waited(lo: datetime, hi: datetime) -> float:
            return sum(
                _overlap(s.started_at, s.ended_at, lo, hi)
                for s in self.steps
                if s.kind == "wait" and not s.context
            )

        input_total = max(0.0, (written - self.origin_at).total_seconds())
        agent_total = max(0.0, (boundary - written).total_seconds())
        input_wait = min(waited(self.origin_at, written), input_total)
        agent_wait = min(waited(written, boundary), agent_total)
        return Shares(
            input_wait=input_wait,
            input_work=input_total - input_wait,
            agent_wait=agent_wait,
            agent_work=agent_total - agent_wait,
            output=max(0.0, (self.effect_at - boundary).total_seconds()),
        )


@dataclass(frozen=True)
class KindStatistics:
    trigger: str
    count: int
    median: float
    p90: float
    shares: Shares  # the median of each share


@dataclass(frozen=True)
class LatencySnapshot:
    """What the page draws, read at ``now``: the records newest first, the steps of what is still
    running (a reaction not yet ended), the person's speech, the dropped inputs, the statistics."""

    now: datetime
    records: tuple[ReactionRecord, ...]
    running: tuple[Step, ...]
    speech: tuple[tuple[datetime, datetime | None], ...]
    dropped: tuple[datetime, ...]
    statistics: tuple[KindStatistics, ...]


def _overlap(
    start: datetime, end: datetime | None, lo: datetime, hi: datetime
) -> float:
    if end is None:
        return 0.0
    return max(0.0, (min(end, hi) - max(start, lo)).total_seconds())


def _dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, UTC)


def wall_clock_ns(perf_ts: float) -> int:
    """A ``time.perf_counter()`` stamp (a frame's ``ts``, a stage timing) as wall-clock nanoseconds,
    reading both clocks now: exact for a recent stamp."""
    return time.time_ns() - int((time.perf_counter() - perf_ts) * 1e9)


# --- The app's own span: one delivered perception ------------------------------------------------


@dataclass(frozen=True)
class PerceptionStep:
    name: str
    kind: StepKind
    started_ns: int
    ended_ns: int


@contextmanager
def perception(
    trigger: str, origin_ns: int, steps: Sequence[PerceptionStep] = ()
) -> Iterator[None]:
    """A root ``wica_interaction.perception`` span from *origin_ns*, one ``wica_interaction.step``
    child per step, current for the ``with`` block: write the perception to the World inside it."""
    tracer = trace.get_tracer(TRACER_NAME)
    span = tracer.start_span(
        SPAN_PERCEPTION,
        context=otel_context.Context(),  # always a root: whatever was current is not its cause
        start_time=origin_ns,
        attributes={ATTR_TRIGGER: trigger},
    )
    parent = trace.set_span_in_context(span, otel_context.Context())
    for step in steps:
        child = tracer.start_span(
            SPAN_STEP,
            context=parent,
            start_time=step.started_ns,
            attributes={ATTR_STEP_NAME: step.name, ATTR_STEP_KIND: step.kind},
        )
        child.end(end_time=max(step.started_ns, step.ended_ns))
    with trace.use_span(span, end_on_exit=True):
        yield


# --- The statistics ---------------------------------------------------------------------------


def nearest_rank(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


class Statistics:
    """Per trigger kind, over the session (or since ``reset``): every counted record's total and
    shares, kept apart from the records so they are not limited to the ones kept."""

    def __init__(self) -> None:
        self._by_kind: dict[str, list[tuple[float, Shares]]] = {}
        self._seen: set[int] = set()

    @staticmethod
    def counts(record: ReactionRecord, *, cold: bool) -> bool:
        return (
            record.complete
            and record.total is not None
            and record.shares is not None
            and record.origin_measured
            and record.outcome == "ok"
            and record.trigger != "follow-up"
            and not cold
        )

    def add(self, record: ReactionRecord) -> None:
        shares = record.shares
        total = record.total
        if record.reaction_id in self._seen or total is None or shares is None:
            return
        self._seen.add(record.reaction_id)
        self._by_kind.setdefault(record.trigger, []).append((total, shares))

    def reset(self) -> None:
        self._by_kind.clear()
        # The records already counted stay out after a reset: only what happens next counts.

    def summary(self) -> tuple[KindStatistics, ...]:
        out = []
        for kind, rows in self._by_kind.items():
            totals = [t for t, _ in rows]
            out.append(
                KindStatistics(
                    trigger=kind,
                    count=len(rows),
                    median=statistics.median(totals),
                    p90=nearest_rank(totals, 0.9),
                    shares=Shares(
                        input_wait=statistics.median(s.input_wait for _, s in rows),
                        input_work=statistics.median(s.input_work for _, s in rows),
                        agent_wait=statistics.median(s.agent_wait for _, s in rows),
                        agent_work=statistics.median(s.agent_work for _, s in rows),
                        output=statistics.median(s.output for _, s in rows),
                    ),
                )
            )
        return tuple(sorted(out, key=lambda k: (-k.count, k.trigger)))


# --- The collector ------------------------------------------------------------------------------


@dataclass
class _Node:
    span: ReadableSpan  # the SDK's live Span while it runs, its ReadableSpan once ended
    parent_id: int | None
    children: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.span.name

    def attr(self, key: str) -> Any:
        attributes = self.span.attributes
        return attributes.get(key) if attributes is not None else None

    @property
    def start_ns(self) -> int:
        return self.span.start_time or 0

    @property
    def end_ns(self) -> int | None:
        return self.span.end_time

    def event_ns(self, name: str) -> tuple[int, Mapping[str, Any]] | None:
        for event in self.span.events:
            if event.name == name:
                return event.timestamp, dict(event.attributes or {})
        return None


def _default_describe(entry: WorldEntry) -> str:
    return f"{entry.key} = {entry.current.value!r}"


_KIND_OF_KEY = {
    "speech_input": "typed",
    "user_event": "user event",
    "personal_space": "user event",
}


class LatencyCollector:
    """Spans (as a span processor's target) and WICA's events, joined per reaction on demand.

    Thread-safe: spans start and end on any thread, WICA's events arrive on the app loop, the page
    reads ``snapshot()`` from its own. A failure while collecting is logged and that item dropped:
    collecting never breaks the app."""

    def __init__(
        self, *, max_records: int = MAX_RECORDS, max_spans: int = MAX_SPANS
    ) -> None:
        self._lock = threading.RLock()
        self._max_records = max_records
        self._max_spans = max_spans
        self._spans: OrderedDict[int, _Node] = OrderedDict()
        self._reaction_span: OrderedDict[int, int] = OrderedDict()
        self._command_span: OrderedDict[str, int] = OrderedDict()
        # The Commands started under a span (a reaction's), by that span's id.
        self._commands_under: dict[int, list[str]] = {}
        self._reactions: OrderedDict[int, ReactionTrace] = OrderedDict()
        self._commands: OrderedDict[str, CommandTrace] = OrderedDict()
        self._labels: OrderedDict[tuple[str, int], str] = OrderedDict()
        self._speech: OrderedDict[datetime, datetime | None] = OrderedDict()
        # The first partial transcript of each stretch of speech, by its start (as the recognizer's
        # span carries it: integer nanoseconds), for the "transcribing as you speak" bar.
        self._partials: OrderedDict[int, datetime] = OrderedDict()
        self._dropped: list[datetime] = []
        self._final: dict[int, ReactionRecord] = {}
        self._cold_id: int | None = None
        self._stats = Statistics()
        self._effects: dict[str, EffectStart] = {}
        self._output_command: str | None = None
        self.describe: Callable[[WorldEntry], str] = _default_describe

    # --- configuration ---------------------------------------------------------------------

    def set_effects(
        self, effects: Mapping[str, EffectStart], *, output_command: str | None
    ) -> None:
        """The embodiment's declared effect starts, by Command name, and the output Command's name
        (its tail is drawn on the Voice lane)."""
        with self._lock:
            self._effects = dict(effects)
            self._output_command = output_command
            self._final.clear()

    def attach(self, wica: Wica) -> None:
        """Subscribe to the agent's events (before it starts)."""
        wica.on_agent_reaction_ended.subscribe(self._on_reaction_ended)
        wica.on_agent_command_ended.subscribe(self._on_command_ended)
        wica.on_agent_trigger.subscribe(self._on_trigger)
        wica.on_agent_trigger_dropped.subscribe(self._on_trigger_dropped)

    # --- inputs --------------------------------------------------------------------------------

    def on_start(self, span: ReadableSpan) -> None:
        try:
            context = span.context
            if context is None:
                return
            parent = span.parent
            node = _Node(span, parent.span_id if parent is not None else None)
            with self._lock:
                self._spans[context.span_id] = node
                if node.parent_id is not None and node.parent_id in self._spans:
                    self._spans[node.parent_id].children.append(context.span_id)
                if span.name == _WICA_REACTION:
                    rid = node.attr(_WICA_REACTION_ID)
                    if isinstance(rid, int):
                        self._reaction_span[rid] = context.span_id
                elif span.name == _WICA_COMMAND:
                    call_id = node.attr(_WICA_CALL_ID)
                    if isinstance(call_id, str):
                        self._command_span[call_id] = context.span_id
                        if node.parent_id is not None:
                            self._commands_under.setdefault(node.parent_id, []).append(
                                call_id
                            )
                while len(self._spans) > self._max_spans:
                    old, _ = self._spans.popitem(last=False)
                    self._commands_under.pop(old, None)
                _trim(self._reaction_span, 4 * self._max_records)
                _trim(self._command_span, 16 * self._max_records)
        except Exception:
            logger.exception("latency: a span start could not be collected")

    def on_end(self, span: ReadableSpan) -> None:
        try:
            context = span.context
            if context is None:
                return
            with self._lock:
                node = self._spans.get(context.span_id)
                if node is not None:
                    node.span = span
                else:  # started before collection began (or evicted): kept as it ended
                    self.on_start(span)
        except Exception:
            logger.exception("latency: a span end could not be collected")

    def speech(
        self,
        started_at: datetime,
        ended_at: datetime | None,
        partial_at: datetime | None = None,
    ) -> None:
        """The person speaking, for the timeline's You row: an interim opens it (*partial_at*,
        when its partial transcript arrived), a final ends it."""
        with self._lock:
            self._speech[started_at] = ended_at or self._speech.get(started_at)
            if partial_at is not None:
                self._partials.setdefault(asr_names.to_ns(started_at), partial_at)
            _trim(self._speech, _MAX_SPEECH)
            _trim(self._partials, _MAX_SPEECH)

    def _on_reaction_ended(self, reaction: ReactionTrace) -> None:
        with self._lock:
            self._reactions[reaction.reaction_id] = reaction
            if self._cold_id is None and reaction.model_started_at is not None:
                self._cold_id = reaction.reaction_id
            while len(self._reactions) > self._max_records:
                old, _ = self._reactions.popitem(last=False)
                self._final.pop(old, None)

    def _on_command_ended(self, command: CommandTrace) -> None:
        with self._lock:
            self._commands[command.call_id] = command
            _trim(self._commands, 16 * self._max_records)

    def _on_trigger(self, entry: WorldEntry) -> None:
        try:
            label = self.describe(entry)
        except Exception:  # noqa: BLE001 - a label never breaks collection
            label = entry.key
        with self._lock:
            self._labels[(entry.key, entry.current.id)] = label
            while len(self._labels) > _MAX_LABELS:
                self._labels.popitem(last=False)

    def _on_trigger_dropped(self, _entry: WorldEntry) -> None:
        with self._lock:
            self._dropped.append(datetime.now(UTC))
            del self._dropped[:-_MAX_DROPPED]

    def reset_statistics(self) -> None:
        with self._lock:
            self._stats.reset()

    # --- reading ---------------------------------------------------------------------------

    def snapshot(self, now: datetime | None = None) -> LatencySnapshot:
        now = now or datetime.now(UTC)
        with self._lock:
            records: list[ReactionRecord] = []
            for rid in reversed(self._reactions):
                record = self._final.get(rid)
                if record is None:
                    try:
                        record = self._record(self._reactions[rid])
                    except Exception:
                        logger.exception("latency: reaction %d could not be read", rid)
                        continue
                    if record.complete and self._settled(record):
                        self._final[rid] = record
                if record.counted:
                    self._stats.add(record)
                records.append(record)
            running = self._running()
            return LatencySnapshot(
                now=now,
                records=tuple(records),
                running=running,
                speech=tuple(self._speech.items()),
                dropped=tuple(self._dropped),
                statistics=self._stats.summary(),
            )

    def _settled(self, record: ReactionRecord) -> bool:
        """A complete record is final once no span below its reaction is still running."""
        span_id = self._reaction_span.get(record.reaction_id)
        return span_id is None or all(
            n.end_ns is not None for n in self._descendants(span_id)
        )

    # --- building a record -------------------------------------------------------------------

    def _node(self, span_id: int | None) -> _Node | None:
        return self._spans.get(span_id) if span_id is not None else None

    def _descendants(self, span_id: int) -> list[_Node]:
        out: list[_Node] = []
        stack = list(self._spans[span_id].children) if span_id in self._spans else []
        while stack:
            node = self._spans.get(stack.pop())
            if node is None:
                continue
            out.append(node)
            stack.extend(node.children)
        return out

    def _head_span(self, reaction_span: int | None) -> _Node | None:
        """The nearest ancestor that is a recognizer segment or a perception of this app — and
        none past an earlier reaction or Command: a Command's completion is written from inside
        that Command's span, so the reaction it wakes descends from the one before, whose input
        is not its own."""
        node = self._node(reaction_span)
        for _ in range(8):
            if node is None:
                return None
            node = self._node(node.parent_id)
            if node is None or node.name in (_WICA_REACTION, _WICA_COMMAND):
                return None
            if node.name in (asr_names.SPAN_NAME, SPAN_PERCEPTION):
                return node
        return None

    def _record(self, reaction: ReactionTrace) -> ReactionRecord:
        trigger = parent_trigger(reaction.triggers)
        span_id = self._reaction_span.get(reaction.reaction_id)
        head = self._head_span(span_id)
        kind, origin, measured, head_steps, parent_rid = self._head(
            reaction, trigger, head
        )
        steps = list(head_steps) + self._middle(reaction, trigger)
        effect_at: datetime | None = None
        effect_command: str | None = None
        effect_note = ""
        effect_started: datetime | None = None
        complete = True
        call_ids = list(reaction.command_call_ids)
        for call_id in (
            self._commands_under.get(span_id, []) if span_id is not None else []
        ):
            if call_id not in call_ids:
                call_ids.append(call_id)
        for call_id in call_ids:
            tail, effect, note, started, ended = self._tail(call_id)
            steps.extend(tail)
            if not ended:
                complete = False
            if effect is not None and (effect_at is None or effect < effect_at):
                effect_at, effect_note, effect_started = effect, note, started
                effect_command = tail[0].command if tail else None
        record = ReactionRecord(
            reaction_id=reaction.reaction_id,
            trigger=kind,
            label=self._labels.get((trigger.key, trigger.version_id), trigger.key),
            parent_reaction_id=parent_rid,
            origin_at=origin,
            origin_measured=measured,
            written_at=trigger.written_at,
            steps=tuple(steps),
            effect_at=effect_at,
            effect_command=effect_command,
            effect_note=effect_note,
            effect_command_started_at=effect_started,
            flags=self._flags(reaction, measured, call_ids),
            outcome=reaction.outcome,
            complete=complete,
        )
        cold = reaction.reaction_id == self._cold_id
        return replace(
            record,
            counted=Statistics.counts(record, cold=cold),
            origin_label=self._origin_label(kind, measured, trigger),
            effect_label=self._effect_label(effect_command),
        )

    def _origin_label(self, kind: str, measured: bool, trigger: TriggerTrace) -> str:
        """Where a record's measure starts, in a few words."""
        if kind == "follow-up":
            if trigger.key == _NEXT_NOTE_KEY:
                return "the agent's note to go on"
            command = self._commands.get(trigger.key[len(COMMAND_KEY_PREFIX) :])
            return (
                f"the end of {command.name}"
                if command is not None
                else "a command's end"
            )
        if not measured:
            return (
                "the final transcript"
                if kind == "speech"
                else "its delivery to the agent"
            )
        return {
            "speech": "your last word",
            "typed": "the message sent",
            "hand sign": "the first frame showing the sign",
            "user event": "the first frame that saw the person",
        }.get(kind, "its delivery to the agent")

    def _effect_label(self, command: str | None) -> str:
        """Where a record's measure ends, in a few words: the body's label for that Command's
        effect, or the Command starting."""
        if command is None:
            return ""
        declared = self._effects.get(command)
        if declared is not None and declared.label:
            return declared.label
        return f"{command} starting"

    def _head(
        self, reaction: ReactionTrace, trigger: TriggerTrace, head: _Node | None
    ) -> tuple[str, datetime, bool, list[Step], int | None]:
        written = trigger.written_at
        if trigger.is_command_completion or trigger.key == _NEXT_NOTE_KEY:
            parent: int | None = reaction.reaction_id - 1
            if trigger.is_command_completion:
                call_id = trigger.key[len(COMMAND_KEY_PREFIX) :]
                command = self._commands.get(call_id)
                parent = command.reaction_id if command is not None else None
            return "follow-up", written, True, [], parent
        if head is not None and head.name == asr_names.SPAN_NAME:
            return ("speech", *self._speech_head(head, written), None)
        if head is not None:
            kind = str(head.attr(ATTR_TRIGGER) or "perception")
            origin = _dt(head.start_ns)
            steps = [
                Step(
                    name=str(child.attr(ATTR_STEP_NAME) or "step"),
                    lane="Seeing",
                    started_at=_dt(child.start_ns),
                    ended_at=_dt(child.end_ns or child.start_ns),
                    kind=_kind(child.attr(ATTR_STEP_KIND)),
                )
                for child in sorted(
                    (self._spans[c] for c in head.children if c in self._spans),
                    key=lambda n: n.start_ns,
                )
                if child.name == SPAN_STEP
            ]
            last = steps[-1].ended_at if steps and steps[-1].ended_at else origin
            steps.append(
                Step("handed to the agent", "Seeing", last, max(last, written), "work")
            )
            return kind, origin, True, steps, None
        kind = _KIND_OF_KEY.get(trigger.key)
        if kind is None:
            kind = (
                "hand sign" if trigger.key.startswith("hand_gesture_") else trigger.key
            )
        # No span of its own: typed text is measured from its write; anything else lacks its origin.
        return kind, written, kind == "typed", [], None

    def _speech_head(
        self, head: _Node, written: datetime
    ) -> tuple[datetime, bool, list[Step]]:
        """The steps of a spoken input, from the recognizer's segment span: the person speaking,
        the recognizer transcribing as they speak (context), the wait for the end of their turn,
        the final transcript, its hand-over to the agent."""
        start = _dt(head.start_ns)
        measured = head.attr(asr_names.ATTR_SPEECH_END_SOURCE) == "vad"
        finalized_event = head.event_ns(asr_names.EVENT_FINALIZED)
        finalized = _dt(finalized_event[0]) if finalized_event else start
        decided_event = head.event_ns(asr_names.EVENT_END_DECIDED)
        decided = _dt(decided_event[0]) if decided_event else None
        decided_by = head.attr(asr_names.ATTR_END_DECIDED_BY)
        steps: list[Step] = []
        spoke = head.attr(asr_names.ATTR_SPEECH_STARTED_UNIX_NS)
        partial = self._partials.get(spoke) if isinstance(spoke, int) else None
        if measured and isinstance(spoke, int):
            steps.append(
                Step(
                    "you speaking",
                    "You",
                    _dt(spoke),
                    start,
                    "work",
                    detail="the voice detector heard speech",
                )
            )
        if partial is not None and partial < finalized:
            steps.append(
                Step(
                    "transcribing as you speak",
                    "Hearing",
                    partial,
                    finalized,
                    "work",
                    detail="from the recognizer's first partial transcript; context, not part of the total",
                    context=True,
                )
            )
        if not measured:
            steps.append(
                Step(
                    "before the final transcript",
                    "Hearing",
                    start,
                    start,
                    "not_measured",
                    detail="no voice detector stamped the end of speech",
                )
            )
        final_from = start
        if measured and decided is not None:
            if decided_by == "vad_gate":
                silence = head.attr(asr_names.ATTR_VAD_MIN_SILENCE_DURATION_MS)
                who = (
                    f"silence {silence} ms"
                    if isinstance(silence, int | float)
                    else "silence"
                )
            else:
                who = "the recognizer's end of turn"
            steps.append(
                Step(
                    "waiting for end of turn",
                    "Hearing",
                    start,
                    decided,
                    "wait",
                    detail=who,
                )
            )
            final_from = decided
        if decided_by == "module":
            final_detail = "arrives with the recognizer's end of turn"
        elif partial is None:
            final_detail = "the whole transcription: no partial came before"
        else:
            final_detail = "what was left to transcribe after the end of turn"
        timer = head.attr(asr_names.ATTR_END_OF_SPEECH_TIMEOUT_S)
        if isinstance(timer, int | float) and timer > 0:
            timer_from = max(final_from, finalized - timedelta(seconds=timer))
            if timer_from > final_from:
                steps.append(
                    Step(
                        "final transcript",
                        "Hearing",
                        final_from,
                        timer_from,
                        "work",
                        detail=final_detail,
                    )
                )
            steps.append(
                Step(
                    "dictation silence timer",
                    "Hearing",
                    timer_from,
                    finalized,
                    "wait",
                    detail=f"{timer:g} s of silence closes the passage",
                )
            )
        else:
            steps.append(
                Step(
                    "final transcript",
                    "Hearing",
                    final_from,
                    finalized,
                    "work",
                    detail=final_detail,
                )
            )
        steps.append(
            Step(
                "handed to the agent",
                "Hearing",
                finalized,
                max(finalized, written),
                "work",
            )
        )
        return start, measured, steps

    @staticmethod
    def _middle(reaction: ReactionTrace, trigger: TriggerTrace) -> list[Step]:
        steps = [
            Step(
                "reaching the agent",
                "Agent",
                trigger.written_at,
                trigger.arrived_at,
                "work",
            ),
            Step(
                "gathering inputs",
                "Agent",
                reaction.window_opened_at,
                reaction.window_closed_at,
                "wait",
            ),
        ]
        if reaction.prompt_ready_at is not None:
            steps.append(
                Step(
                    "building the prompt",
                    "Agent",
                    reaction.window_closed_at,
                    reaction.prompt_ready_at,
                    "work",
                )
            )
        started = reaction.model_started_at
        if started is not None:
            first = reaction.first_content_at
            if first is not None:
                steps.append(
                    Step("model: first token", "Agent", started, first, "work")
                )
                if reaction.first_output_at is not None:
                    steps.append(
                        Step(
                            "model: first words to say",
                            "Agent",
                            first,
                            reaction.first_output_at,
                            "work",
                        )
                    )
                if reaction.model_ended_at is not None:
                    steps.append(
                        Step(
                            "model: whole response",
                            "Agent",
                            first,
                            reaction.model_ended_at,
                            "work",
                        )
                    )
            elif reaction.model_ended_at is not None:
                steps.append(
                    Step("model", "Agent", started, reaction.model_ended_at, "work")
                )
        return steps

    def _tail(
        self, call_id: str
    ) -> tuple[list[Step], datetime | None, str, datetime | None, bool]:
        """A Command's steps, its effect start (or None), the effect's note, its start, whether it
        ended."""
        trace_ = self._commands.get(call_id)
        node = self._node(self._command_span.get(call_id))
        if trace_ is not None:
            name, started, ended = trace_.name, trace_.started_at, trace_.ended_at
        elif node is not None:
            name = str(node.attr(_WICA_COMMAND_NAME) or "command")
            started, ended = _dt(node.start_ns), None
        else:
            return [], None, "", None, True
        lane: Lane = (
            "Voice"
            if name == self._output_command or (trace_ is not None and trace_.is_output)
            else "Actions"
        )
        below = (
            self._descendants(self._command_span[call_id]) if node is not None else []
        )
        declared = self._effects.get(name)
        effect: datetime | None = started
        note = ""
        if declared is not None:
            effect, note = None, declared.note
            for child in below:
                if child.name != declared.span:
                    continue
                found = child.event_ns(declared.event)
                if found is None:
                    continue
                at_ns, attributes = found
                extra = declared.latency_s or 0.0
                if declared.latency_attribute is not None:
                    value = attributes.get(declared.latency_attribute)
                    if isinstance(value, int | float):
                        extra += float(value)
                effect = _dt(at_ns) + timedelta(seconds=extra)
                break
        steps: list[Step] = []
        moments: dict[str, int] = {}
        for child in sorted(below, key=lambda n: n.start_ns):
            for event in child.span.events:
                moments.setdefault(event.name, event.timestamp)
        previous = started
        for event_name, at_ns in sorted(moments.items(), key=lambda kv: kv[1]):
            at = _dt(at_ns)
            if effect is not None and at > effect:
                break
            steps.append(
                Step(event_name, lane, previous, max(previous, at), "work", name)
            )
            previous = max(previous, at)
        if effect is not None:
            if effect > previous and declared is not None:
                steps.append(
                    Step("device latency", lane, previous, effect, "work", name)
                )
            perceived = (
                "heard" if lane == "Voice" else ("moving" if declared else "running")
            )
            steps.append(
                Step(perceived, lane, effect, ended, "effect", name, detail=note)
            )
        elif ended is None:
            # Running, its effect not stamped yet (the bridge places an emotion's motion on its
            # span only when the call ends): the Command is still under way, drawn up to now.
            steps.append(
                Step("in progress", lane, previous, None, "work", name, detail=note)
            )
        else:
            steps.append(
                Step(
                    "never perceived",
                    lane,
                    started,
                    ended,
                    "not_measured",
                    name,
                    detail="the declared effect never came",
                )
            )
        if not steps:
            steps.append(Step(name, lane, started, ended, "work", name))
        return steps, effect, note, started, ended is not None

    def _flags(
        self, reaction: ReactionTrace, measured: bool, call_ids: Sequence[str]
    ) -> tuple[str, ...]:
        flags: list[str] = []
        if reaction.attempts > 1:
            flags.append(f"{reaction.attempts} attempts")
        if reaction.garbled_attempts:
            flags.append("garbled")
        if (
            reaction.guard_duration is not None
            and reaction.guard_duration >= GUARDS_FLAG_S
        ):
            flags.append(f"guards {reaction.guard_duration * 1000:.0f} ms")
        if reaction.outcome == "model_error":
            flags.append("model error")
        elif reaction.outcome == "model_timeout":
            flags.append("model timeout")
        elif reaction.outcome in ("rejected", "cancelled"):
            flags.append(reaction.outcome)
        if reaction.usage is not None and reaction.usage.cache_read_tokens == 0:
            flags.append("uncached")
        if reaction.reaction_id == self._cold_id:
            flags.append("first (cold)")
        if any(
            reaction.window_closed_at <= t <= reaction.ended_at for t in self._dropped
        ):
            flags.append("input dropped")
        for call_id in call_ids:
            span_id = self._command_span.get(call_id)
            if span_id is None:
                continue
            for node in self._descendants(span_id):
                if node.name != TTS_UTTERANCE_SPAN:
                    continue
                admitted = node.event_ns(TTS_ADMITTED_EVENT)
                if admitted and (admitted[0] - node.start_ns) / 1e9 >= QUEUED_FLAG_S:
                    flags.append("queued")
                    break
        if not measured:
            flags.append("origin unknown")
        if reaction.noop:
            flags.append("noop")
        return tuple(dict.fromkeys(flags))

    def _running(self) -> tuple[Step, ...]:
        """The steps of the reactions that have not ended: their head, the agent so far, and the
        Commands they started."""
        steps: list[Step] = []
        for rid, span_id in list(self._reaction_span.items())[-8:]:
            if rid in self._reactions:
                continue
            node = self._node(span_id)
            if node is None or node.end_ns is not None:
                continue
            head = self._head_span(span_id)
            if head is not None and head.name == asr_names.SPAN_NAME:
                steps.extend(self._speech_head(head, _dt(node.start_ns))[2])
            steps.append(
                Step("the agent so far", "Agent", _dt(node.start_ns), None, "work")
            )
            for call_id in self._commands_under.get(span_id, []):
                steps.extend(self._tail(call_id)[0])
        return tuple(steps)


def _trim(items: OrderedDict[Any, Any], size: int) -> None:
    while len(items) > size:
        items.popitem(last=False)


def _kind(value: Any) -> StepKind:
    return value if value in ("work", "wait", "not_measured", "effect") else "work"


# --- Installing the SDK -------------------------------------------------------------------------


class _Forwarder(SpanProcessor):
    """The one processor this app adds to the process's provider: every span start and end goes to
    the newest system's collector."""

    def __init__(self) -> None:
        self.target: LatencyCollector | None = None

    def on_start(
        self, span: Any, parent_context: otel_context.Context | None = None
    ) -> None:
        target = self.target
        if target is not None:
            target.on_start(span)

    def on_end(self, span: ReadableSpan) -> None:
        target = self.target
        if target is not None:
            target.on_end(span)

    def shutdown(self) -> None:
        self.target = None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_install_lock = threading.Lock()
_forwarder: _Forwarder | None = None
_exporter: str | None = None


def install_tracing(
    collector: LatencyCollector, *, environ: Mapping[str, str] = os.environ
) -> list[str]:
    """Install the OpenTelemetry SDK once per process and point collection at *collector*; add
    the exporter ``WICA_INTERACTION_TRACES`` names. Returns the banner lines (empty when all went
    well). The provider is never replaced: a later system takes the forwarding over."""
    global _forwarder, _exporter
    lines: list[str] = []
    with _install_lock:
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            trace.set_tracer_provider(TracerProvider())
            provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            return [
                "Latency: another OpenTelemetry provider is installed; the latency view stays empty"
            ]
        if _forwarder is None:
            _forwarder = _Forwarder()
            provider.add_span_processor(_forwarder)
        _forwarder.target = collector
        choice = environ.get(TRACES_ENV, "").strip().lower()
        if choice and choice != _exporter:
            if choice == "console":
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
                _exporter = choice
            elif choice == "otlp":
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
                        OTLPSpanExporter,
                    )
                except ImportError:
                    lines.append(
                        f"Latency: {TRACES_ENV}=otlp needs the `otlp` extra; no traces are exported"
                    )
                else:
                    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                    _exporter = choice
            else:
                lines.append(
                    f"Latency: {TRACES_ENV}={choice!r} is not `console` or `otlp`; no traces are exported"
                )
    return lines
