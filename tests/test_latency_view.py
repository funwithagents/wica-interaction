"""The latency view's markup (`latency_view.py`): hand-built records in, what a reader sees out
(specs/latency-view.md, "What the page shows")."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from wica_interaction.latency import (
    KindStatistics,
    LatencySnapshot,
    ReactionRecord,
    Shares,
    Step,
)
from wica_interaction.latency_view import (
    TABLE_HEADERS,
    glance,
    summary,
    table_rows,
    timeline,
    waterfall,
)

T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def record(
    rid: int = 1,
    *,
    label: str = '🗣️ "what\'s the weather"',
    trigger: str = "speech",
    parent: int | None = None,
    steps: tuple[Step, ...] | None = None,
    effect: float | None = 2.0,
    flags: tuple[str, ...] = (),
) -> ReactionRecord:
    """Speech ending at 0 s, written at 0.5 s, the voice starting at 1.5 s, heard at 2 s."""
    return ReactionRecord(
        reaction_id=rid,
        trigger=trigger,
        label=label,
        parent_reaction_id=parent,
        origin_at=at(0),
        origin_measured=True,
        written_at=at(0.5),
        steps=steps
        if steps is not None
        else (
            Step("endpointing", "Hearing", at(0), at(0.3), "wait"),
            Step("finalising", "Hearing", at(0.3), at(0.5), "work"),
            Step("model", "Agent", at(0.7), at(1.5), "work"),
            Step("first_audio", "Voice", at(1.5), at(2.0), "work", "say"),
            Step("effect", "Voice", at(2.0), at(3.0), "effect", "say"),
        ),
        effect_at=at(effect) if effect is not None else None,
        effect_command="say",
        effect_note="the audio device started, plus the latency it reports",
        origin_label="your last word",
        effect_label="the first sound",
        effect_command_started_at=at(1.5),
        flags=flags,
        outcome="ok",
        complete=True,
    )


def snap(
    *records: ReactionRecord, now: float = 4.0, **fields: object
) -> LatencySnapshot:
    values: dict[str, object] = {
        "now": at(now),
        "records": records,
        "running": (),
        "speech": (),
        "dropped": (),
        "statistics": (),
    }
    values.update(fields)
    return LatencySnapshot(**values)  # type: ignore[arg-type]


def bars(markup: str) -> list[tuple[float, float]]:
    """(left %, width %) of every positioned bar, in order."""
    return [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(r"left:([\d.]+)%;width:([\d.]+)%", markup)
    ]


def test_the_waterfall_puts_each_step_where_it_happened() -> None:
    markup = waterfall(record(), snap())
    # Axis 0 → 3 s: endpointing 0 → 0.3 s is the first tenth of it.
    assert (0.0, 10.0) in bars(markup)
    # The model 0.7 → 1.5 s.
    assert (23.3, 26.7) in bars(markup)
    # The title says where the measure starts and ends; the effect's note is a line of its own.
    assert (
        "<b>2.00 s</b> from <i>your last word</i> to <i>the first sound</i> (say)"
        in markup
    )
    assert ">the audio device started, plus the latency it reports</div>" in markup


def test_the_kinds_are_told_apart_by_pattern() -> None:
    steps = (
        Step("hold", "Seeing", at(0), at(0.3), "wait"),
        Step("before the final", "Hearing", at(0.3), at(0.3), "not_measured"),
        Step("effect", "Voice", at(1), at(2), "effect", "say"),
    )
    markup = waterfall(record(steps=steps), snap())
    assert "opacity:0.35" in markup  # the wait, light
    assert "repeating-linear-gradient" in markup  # not measured, hatched
    assert "brightness(0.6)" in markup  # heard, darkest
    # Not measured has no length: drawn with a minimum width, never as nothing.
    assert "min-width:6px" in markup


def test_what_a_person_or_the_model_wrote_is_escaped() -> None:
    hostile = record(label="<script>alert(1)</script>", flags=("<b>x</b>",))
    markup = waterfall(hostile, snap(hostile))
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert "<b>x</b>" not in markup


def test_an_idle_timeline_renders_the_same_markup_on_every_tick() -> None:
    assert timeline(snap(now=100.0)) == timeline(snap(now=100.2))


def test_the_timeline_draws_what_still_runs_up_to_now() -> None:
    running = (Step("agent", "Agent", at(25.0), None, "work"),)
    markup = timeline(snap(now=30.0, running=running))
    # The last 30 s: from 25 s to now is the last sixth of the row.
    assert (83.3, 16.7) in bars(markup)


def test_the_timeline_marks_a_dropped_input() -> None:
    markup = timeline(snap(now=30.0, dropped=(at(15.0),)))
    assert 'title="input dropped"' in markup and "left:50.0%" in markup


def test_the_table_indents_a_follow_up_under_its_parent() -> None:
    parent = record(1)
    follow = record(2, trigger="follow-up", parent=1, effect=None)
    other = record(3, trigger="typed", label="hello")
    rows = table_rows(
        snap(other, follow, parent)
    )  # newest first, as the collector gives them
    # Top to bottom as they happened, the follow-up right under the reaction it follows.
    assert [r[0] for r in rows] == ["1", "2", "3"]
    assert rows[1][1] == "  ↳ follow-up"
    assert len(rows[0]) == len(TABLE_HEADERS)
    # input: wait, input: work, agent: wait, agent: work, output, total
    assert rows[0][3:9] == ["0.30", "0.20", "0.00", "1.00", "0.50", "2.00"]
    assert rows[1][8] == "—"  # no effect, no total


def test_the_glance_line_shows_the_last_total_and_its_kinds_statistics() -> None:
    stats = KindStatistics("speech", 4, 1.42, 2.31, Shares(0.3, 0.2, 0.2, 0.5, 0.2))
    markup = glance(snap(record(), statistics=(stats,)))
    assert "last: 2.00 s" in markup
    assert "speech median 1.42 s · p90 2.31 s" in markup
    # Input 0 → 0.5 s, its first 0.3 s a wait: two pieces, the wait lighter.
    assert (0.0, 15.0) in bars(markup) and (15.0, 10.0) in bars(markup)


def test_the_glance_line_waits_for_a_measured_reaction() -> None:
    assert "No reaction measured yet" in glance(snap(record(effect=None)))


def test_the_summary_lists_each_trigger_kind() -> None:
    stats = KindStatistics("hand sign", 3, 1.05, 1.4, Shares(0.3, 0.1, 0.2, 0.3, 0.15))
    assert (
        "| hand sign | 3 | 1.05 s | 1.40 s | 0.30 | 0.10 | 0.20 | 0.30 | 0.15 |"
        in summary(snap(statistics=(stats,)))
    )
    assert "No reaction counted yet" in summary(snap())


def test_a_librarys_event_is_said_in_plain_words_with_its_raw_name_on_hover() -> None:
    markup = waterfall(record(), snap())
    # `first_audio` ends the step that produced it: synthesis.
    assert "Voice · synthesis" in markup
    assert "synthesis [first_audio]: 500 ms" in markup
    # A step's detail follows its name.
    steps = (
        Step(
            "waiting for end of turn",
            "Hearing",
            at(0),
            at(0.3),
            "wait",
            detail="silence 300 ms",
        ),
    )
    assert "Hearing · waiting for end of turn · silence 300 ms" in waterfall(
        record(steps=steps), snap()
    )


def test_the_signal_rows_carry_every_input_and_output_on_the_axis() -> None:
    mine = record(1)
    # Another reaction on the same span of time: its sign was seen, its emotion moved the robot.
    other = record(
        2,
        trigger="hand sign",
        steps=(
            Step("holding the sign", "Seeing", at(0.5), at(0.8), "wait"),
            Step("moving", "Actions", at(1.2), at(2.5), "effect", "play_emotion"),
        ),
    )
    markup = waterfall(mine, snap(mine, other, speech=((at(-1.0), at(0.0)),)))
    signals = markup[markup.index("signal · inputs") :]
    assert 'title="you speaking"' in signals and 'title="holding the sign"' in signals
    assert (
        'title="say: effect"' in signals and 'title="play_emotion: moving"' in signals
    )
