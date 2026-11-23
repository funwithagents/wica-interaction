"""What the page shows of the latency records (specs/latency-view.md, "What the page shows").

Pure functions from a ``LatencySnapshot`` (latency.py) to markup and table rows: the glance line,
the live timeline, the session summary, the table of reactions and one reaction's waterfall. No
Gradio, no clock: ``now`` is the snapshot's. Bars are positioned ``div``s with inline styles, so the
markup is testable without a browser and needs no plotting library. Everything that came from a
person or the model (a label, a flag, a note) is HTML-escaped.

The three kinds of time are told apart by pattern as well as colour: work is solid, a configured
wait is light, what is not measured is hatched; what was heard or moving is the darkest.
"""

from __future__ import annotations

import html
import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from wica_interaction.latency import (
    LANES,
    KindStatistics,
    LatencySnapshot,
    ReactionRecord,
    Step,
    StepKind,
)

TIMELINE_S = 30.0

# The three shares of a total, in the order the glance bar draws them.
_SHARE_COLOURS = {"input": "#3b82f6", "agent": "#8b5cf6", "output": "#10b981"}
_LANE_COLOURS = {
    "You": "#64748b",
    "Hearing": "#3b82f6",
    "Seeing": "#0ea5e9",
    "Agent": "#8b5cf6",
    "Voice": "#10b981",
    "Actions": "#f59e0b",
}
# The libraries' event names, said as what the step ending there did. A step is named after the
# event it ends at, so `first_audio` is the time spent producing it: synthesis. An event not listed
# shows under its own name; the raw name is always in the hover text.
EVENT_NAMES = {
    # tts-engine's utterance
    "admitted": "waiting for the voice to be free",
    "first_text": "waiting for the first words",
    "first_segment": "waiting for the first sentence",
    "first_audio": "synthesis",
    "last_audio": "synthesis, to the end",
    "playback_started": "opening the audio device",
    # reachy-mini-bridge's verbs
    "first_push": "sending to the robot",
    "queued": "preparing the move",
    "motion_started": "waiting for the motors",
    "trajectory_started": "blending into the move",
    "sound_started": "starting the sound",
}

_HATCH = "repeating-linear-gradient(45deg, {c} 0 3px, transparent 3px 6px)"

# The waterfall's label column: a step's lane, plain name and detail.
_WATERFALL_LABEL = "26em"

TABLE_HEADERS = [
    "#",
    "trigger",
    "heard / seen",
    "input",
    "wait",
    "agent",
    "output",
    "total",
    "flags",
]


def _fill(kind: StepKind, colour: str) -> str:
    if kind == "wait":
        return f"background:{colour};opacity:0.35;"
    if kind == "not_measured":
        return f"background:{_HATCH.format(c=colour)};"
    if kind == "effect":
        return f"background:{colour};filter:brightness(0.6);"
    return f"background:{colour};"


def legend() -> str:
    swatches: tuple[tuple[StepKind, str], ...] = (
        ("work", "work"),
        ("wait", "configured wait"),
        ("not_measured", "not measured"),
        ("effect", "heard or moving"),
    )
    items = "".join(
        f'<span style="display:inline-block;margin-right:12px">'
        f'<span style="display:inline-block;width:18px;height:9px;vertical-align:middle;'
        f'{_fill(kind, "#8b5cf6")}"></span> {label}</span>'
        for kind, label in swatches
    )
    return f'<div style="font-size:0.8em;opacity:0.8;margin-top:4px">{items}</div>'


def step_name(step: Step) -> str:
    """How the page names *step*: a library's event in plain words, the app's own steps as they
    are, with the step's detail after it."""
    name = EVENT_NAMES.get(step.name, step.name)
    return f"{name} · {step.detail}" if step.detail and step.kind != "effect" else name


def _hover(step: Step, end: datetime) -> str:
    duration = (end - step.started_at).total_seconds() * 1000
    raw = f" [{step.name}]" if step.name in EVENT_NAMES else ""
    detail = f" — {step.detail}" if step.detail else ""
    return f"{EVENT_NAMES.get(step.name, step.name)}{raw}: {duration:.0f} ms, {step.kind.replace('_', ' ')}{detail}"


def seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


@dataclass(frozen=True)
class _Axis:
    start: datetime
    end: datetime

    @property
    def span_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 1e-6)

    def x(self, at: datetime) -> float:
        """*at* as a percentage of the axis, clamped to it, rounded to a tenth."""
        fraction = (at - self.start).total_seconds() / self.span_s
        return round(min(max(fraction, 0.0), 1.0) * 100, 1)


def _bar(
    axis: _Axis,
    start: datetime,
    end: datetime,
    style: str,
    *,
    title: str = "",
    min_px: int = 0,
) -> str:
    left = axis.x(start)
    width = round(max(axis.x(end) - left, 0.0), 1)
    minimum = f"min-width:{min_px}px;" if min_px else ""
    tip = f' title="{html.escape(title)}"' if title else ""
    return (
        f'<div{tip} style="position:absolute;top:2px;bottom:2px;left:{left}%;'
        f'width:{width}%;{minimum}{style}border-radius:2px"></div>'
    )


def _track(
    label: str, bars: Iterable[str], *, height: int = 14, width: str = "11em"
) -> str:
    return (
        '<div style="display:flex;align-items:center;margin:1px 0">'
        f'<div title="{html.escape(label)}" style="width:{width};flex:none;font-size:0.8em;white-space:nowrap;overflow:hidden;'
        f'text-overflow:ellipsis">{html.escape(label)}</div>'
        f'<div style="position:relative;flex:1;height:{height}px;'
        f'background:rgba(127,127,127,0.08)">{"".join(bars)}</div></div>'
    )


def _context(step: Step) -> str:
    """A context bar (the recognizer transcribing as the person speaks) is drawn thinner and
    lighter, under the steps it overlaps."""
    return "top:5px;bottom:5px;opacity:0.5;" if step.context else ""


def _end(step: Step, now: datetime) -> datetime:
    return step.ended_at if step.ended_at is not None else now


def _kind_stats(snapshot: LatencySnapshot, trigger: str) -> KindStatistics | None:
    return next((k for k in snapshot.statistics if k.trigger == trigger), None)


# --- The glance line -------------------------------------------------------------------------


def glance(snapshot: LatencySnapshot) -> str:
    """The newest reaction with a total: the total, one bar split into input, agent and output
    with the configured waits lighter, and its trigger kind's median and 90th percentile."""
    record = next((r for r in snapshot.records if r.total is not None), None)
    if record is None or record.effect_at is None:
        return '<div style="opacity:0.7">No reaction measured yet.</div>'
    shares = record.shares
    assert shares is not None and record.total is not None
    boundary = record.written_at + timedelta(seconds=shares.agent)
    segments = [
        ("input", record.origin_at, record.written_at),
        ("agent", record.written_at, boundary),
        ("output", boundary, record.effect_at),
    ]
    waits = [
        (s.started_at, _end(s, snapshot.now)) for s in record.steps if s.kind == "wait"
    ]
    axis = _Axis(record.origin_at, record.effect_at)
    bars: list[str] = []
    for share, start, end in segments:
        if end <= start:
            continue
        cuts = sorted(
            {start, end} | {t for a, b in waits for t in (a, b) if start < t < end}
        )
        for a, b in itertools.pairwise(cuts):
            waiting = any(wa <= a and b <= wb for wa, wb in waits)
            colour = _SHARE_COLOURS[share]
            style = f"background:{colour};" + ("opacity:0.35;" if waiting else "")
            title = f"{share} {(b - a).total_seconds():.2f} s" + (
                " (wait)" if waiting else ""
            )
            bars.append(_bar(axis, a, b, style, title=title))
    stats = _kind_stats(snapshot, record.trigger)
    tail = (
        f" · {html.escape(record.trigger)} median {stats.median:.2f} s · p90 {stats.p90:.2f} s"
        if stats is not None
        else ""
    )
    key = " ".join(
        f'<span style="color:{c}">■</span> {name} {getattr(shares, name):.2f}'
        for name, c in _SHARE_COLOURS.items()
    )
    return (
        '<div style="display:flex;align-items:center;gap:12px">'
        f"<b>last: {record.total:.2f} s</b>"
        f'<div style="position:relative;flex:1;height:14px">{"".join(bars)}</div>'
        f'<span style="font-size:0.85em">{key}{tail}</span></div>'
    )


# --- The live timeline -----------------------------------------------------------------------


def timeline(snapshot: LatencySnapshot, *, window_s: float = TIMELINE_S) -> str:
    """The last *window_s* seconds, one row per lane; what still runs is drawn up to now."""
    now = snapshot.now
    axis = _Axis(now - timedelta(seconds=window_s), now)
    rows: dict[str, list[str]] = {lane: [] for lane in LANES}
    for started, ended in snapshot.speech:
        end = ended or now
        if end >= axis.start:
            rows["You"].append(
                _bar(axis, started, end, _fill("work", _LANE_COLOURS["You"]))
            )
    steps = [s for r in snapshot.records for s in r.steps] + list(snapshot.running)
    for step in steps:
        end = _end(step, now)
        if end < axis.start or step.lane == "You":
            continue
        rows[step.lane].append(
            _bar(
                axis,
                step.started_at,
                end,
                _context(step) + _fill(step.kind, _LANE_COLOURS[step.lane]),
                title=_hover(step, end),
                min_px=2 if step.kind == "not_measured" else 0,
            )
        )
    for dropped in snapshot.dropped:
        if dropped >= axis.start:
            x = axis.x(dropped)
            rows["Agent"].append(
                f'<div title="input dropped" style="position:absolute;top:0;bottom:0;left:{x}%;'
                'width:2px;background:#ef4444"></div>'
            )
    scale = (
        '<div style="display:flex;font-size:0.75em;opacity:0.7"><div style="width:11em"></div>'
        f'<div style="flex:1;display:flex;justify-content:space-between">'
        f"<span>-{window_s:g} s</span><span>-{window_s / 2:g} s</span><span>now</span></div></div>"
    )
    return scale + "".join(_track(lane, rows[lane]) for lane in LANES) + legend()


# --- The summary and the table ----------------------------------------------------------------


def summary(snapshot: LatencySnapshot) -> str:
    """The statistics per trigger kind, as Markdown."""
    if not snapshot.statistics:
        return (
            "_No reaction counted yet: the first one is cold, follow-ups are left out._"
        )
    lines = [
        "| trigger | count | median | p90 | input | wait | agent | output |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k in snapshot.statistics:
        s = k.shares
        lines.append(
            f"| {k.trigger} | {k.count} | {k.median:.2f} s | {k.p90:.2f} s | {s.input:.2f} | "
            f"{s.wait:.2f} | {s.agent:.2f} | {s.output:.2f} |"
        )
    return "\n".join(lines)


def ordered(records: Sequence[ReactionRecord]) -> list[tuple[ReactionRecord, int]]:
    """The records newest first, each follow-up right under its parent, with its depth."""
    ids = {r.reaction_id for r in records}
    children: dict[int, list[ReactionRecord]] = {}
    for record in records:
        if record.parent_reaction_id in ids:
            children.setdefault(record.parent_reaction_id or 0, []).append(record)
    out: list[tuple[ReactionRecord, int]] = []

    def visit(record: ReactionRecord, depth: int) -> None:
        out.append((record, depth))
        for child in sorted(
            children.get(record.reaction_id, []), key=lambda r: r.reaction_id
        ):
            visit(child, depth + 1)

    for record in records:
        if record.parent_reaction_id not in ids:
            visit(record, 0)
    return out


def table_rows(snapshot: LatencySnapshot) -> list[list[str]]:
    """One row per record (``TABLE_HEADERS``), newest first, follow-ups indented under their
    parent. Plain text: the table is a Dataframe."""
    rows: list[list[str]] = []
    for record, depth in ordered(snapshot.records):
        shares = record.shares
        indent = "  " * depth + ("↳ " if depth else "")
        rows.append(
            [
                str(record.reaction_id),
                indent + record.trigger,
                record.label,
                seconds(shares.input if shares else None),
                seconds(shares.wait if shares else None),
                seconds(shares.agent if shares else None),
                seconds(shares.output if shares else None),
                seconds(record.total),
                ", ".join(record.flags),
            ]
        )
    return rows


# --- The waterfall ----------------------------------------------------------------------------


def waterfall(record: ReactionRecord | None, snapshot: LatencySnapshot) -> str:
    """Every step of *record* on one time axis, one row each, the origin and the effect marked,
    then two signal rows on the same axis: the person speaking, and the voice heard."""
    if record is None:
        return '<div style="opacity:0.7">Select a reaction in the table.</div>'
    now = snapshot.now
    starts = [s.started_at for s in record.steps] + [record.origin_at]
    ends = [_end(s, now) for s in record.steps] + [record.effect_at or record.origin_at]
    axis = _Axis(min(starts), max(ends))
    title = (
        f"reaction {record.reaction_id} · {html.escape(record.trigger)} · "
        + (
            f"{record.total:.2f} s to {html.escape(record.effect_note or 'the effect')}"
            if record.total is not None
            else "no effect"
        )
        + (" · origin not measured" if not record.origin_measured else "")
    )
    origin_x = axis.x(record.origin_at)
    marks = [
        (
            f'<div title="origin" style="position:absolute;top:0;bottom:0;left:{origin_x}%;'
            'border-left:1px dashed currentColor;opacity:0.6"></div>'
        )
    ]
    if record.effect_at is not None:
        marks.append(
            f'<div title="{html.escape(record.effect_note or "effect")}" style="position:absolute;'
            f'top:0;bottom:0;left:{axis.x(record.effect_at)}%;border-left:2px solid #ef4444"></div>'
        )
    rows = []
    for step in sorted(record.steps, key=lambda s: (LANES.index(s.lane), s.started_at)):
        end = _end(step, now)
        label = f"{step.lane} · {step_name(step)}"
        bar = _bar(
            axis,
            step.started_at,
            end,
            _context(step) + _fill(step.kind, _LANE_COLOURS[step.lane]),
            title=_hover(step, end),
            min_px=6 if step.kind == "not_measured" else 1,
        )
        rows.append(_track(label, [bar, *marks], width=_WATERFALL_LABEL))
    speaking = [
        _bar(axis, a, b or now, _fill("work", _LANE_COLOURS["You"]))
        for a, b in snapshot.speech
        if (b or now) >= axis.start and a <= axis.end
    ]
    heard = [
        _bar(axis, s.started_at, _end(s, now), _fill("effect", _LANE_COLOURS["Voice"]))
        for s in record.steps
        if s.kind == "effect" and s.lane == "Voice"
    ]
    signals = _track(
        "signal · you speaking", speaking, height=6, width=_WATERFALL_LABEL
    ) + _track("signal · voice heard", heard, height=6, width=_WATERFALL_LABEL)
    scale = (
        f'<div style="display:flex;font-size:0.75em;opacity:0.7"><div style="width:{_WATERFALL_LABEL}"></div>'
        f'<div style="flex:1;display:flex;justify-content:space-between"><span>0</span>'
        f"<span>{axis.span_s:.2f} s</span></div></div>"
    )
    flags = (
        f'<div style="font-size:0.85em">flags: {html.escape(", ".join(record.flags))}</div>'
        if record.flags
        else ""
    )
    return (
        f"<div><b>{title}</b></div>{flags}<div>“{html.escape(record.label)}”</div>"
        + scale
        + "".join(rows)
        + signals
        + legend()
    )
