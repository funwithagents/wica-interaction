"""The Gradio UI: the page, the session's surfaces, and the app's own ASR live view.

This is the *presentation* half of the app, split from the wiring in [system.py](system.py). The three
framework-observability panels — Conversation, World state, Reaction (prompt + instrumentation) —
are **not** implemented here: they are WICA's reusable ``wica.contrib.gradio`` components (the
``wica[gradio]`` extra), each a presenter built over the ``Wica`` plus a panel function that creates
the components and owns the timer keeping them live. What this module holds:

- ``display_entry`` — the one hook through which the app injects persona knowledge into those generic
  panels: how a World entry reads to a *person* (`speech_input` → `🗣️ "…"`, a `say` execution →
  the spoken text). The person-facing counterpart of the ``speech_input`` ``serialize_fn`` in
  [system.py](system.py).
- ``entry_display(system)`` — that hook with the embodiment's own entries in front of it: each
  ``InputEntry`` an embodiment declares carries its ``display_fn``, so the body's wording reaches the
  transcript without this module knowing the body.
- ``build_page(model, session)`` — the page, built once per process: the Interaction tab (the
  session bar — Start, Stop, the status and why a Start cannot run — then the running session's
  surfaces: the Conversation beside the body's camera and the typed-text hatch, the app's ASR
  beside its Vision, the debug panels, a banner for anything missing), the Latency tab, and
  app-setup's Setup and Secrets panels. The session's surfaces are drawn per session, over its own
  presenters (built by ``system.open_session``); the page wires nothing into the agent.
- ``tick_asr`` — the live view the ASR panel's timer polls; ``tick_view`` — what the camera's
  timer shows of the body (``embodiment.view()``). The app owns these two timers.

The only dependencies on ``system`` and ``session`` are their *types* (for annotations), imported
under ``TYPE_CHECKING`` so there is no import cycle: ``system`` imports ``build_page`` and the
presenters' hook from here at runtime, and this module never imports ``system`` at runtime. See
specs/app.md and specs/session.md.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gradio as gr
from app_setup.ui import secrets_panel, setup_panel, unsaved_notice
from wica import CommandExecution, WorldEntry
from wica.contrib.gradio import (
    EntryDisplay,
    conversation_panel,
    reaction_panel,
    world_state_panel,
)

from wica_interaction import latency_view

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt
    from app_setup.app import AppModel

    from wica_interaction.latency import LatencyCollector, ReactionRecord
    from wica_interaction.session import Running, Session
    from wica_interaction.system import AppSystem

logger = logging.getLogger(__name__)

# The output Command's name (the embodiment's `say.__name__`): the voice, whose execution item the
# hook titles by channel with the spoken text as body.
_SAY_COMMAND_NAME = "say"

_NO_LIVE_SPEECH = "(no speech yet)"

# The Conversation's height (px): tall enough to follow a few reactions without scrolling.
CONVERSATION_HEIGHT = 640

# The World keys of the per-hand sign entries (`hand_gesture_1`, …) — see specs/gesture-input.md.
_HAND_GESTURE_KEY = re.compile(r"^hand_gesture_(\d+)$")


def _hand_number(key: str) -> int | None:
    """The hand number of a `hand_gesture_<n>` entry key, or `None` for any other key."""
    match = _HAND_GESTURE_KEY.match(key)
    return int(match.group(1)) if match is not None else None


# Default folder for the Vision snapshot buttons: a `snapshots/` folder under the working directory
# (the hand demo defaults to its own script's folder; an app run from its checkout gets the same).
_SNAPSHOT_DIR = Path.cwd() / "snapshots"


def display_entry(entry: WorldEntry) -> EntryDisplay | None:
    """How the app's entries show in the transcript and the reaction labels — the person-facing
    counterpart of the `speech_input` serialize_fn. Anything it doesn't recognise returns None and
    gets the contrib's generic default (`⚡ key = value`, `🦾 name(args)` for a Command)."""
    value = entry.current.value
    if entry.key == "speech_input":
        return EntryDisplay(f'🗣️ "{value}"')
    hand = _hand_number(entry.key)
    if hand is not None:
        # The hand-sign inputs: only a sign being shown ever triggers (and so shows in the
        # transcript); a hand going sign-less can still label a reaction whose prompt carried it.
        sign = value if isinstance(value, str) else "no sign"
        return EntryDisplay(f"✋ hand {hand}: {sign}")
    if isinstance(value, CommandExecution) and value.name == _SAY_COMMAND_NAME:
        # The voice: title it by channel and show what the agent set out to say as the body.
        return EntryDisplay("🗣️ say", str(value.args.get("text", "")))
    return None


def entry_label(
    display: Callable[[WorldEntry], EntryDisplay | None], entry: WorldEntry
) -> str:
    """An entry's one-line label, as the transcript shows it (the generic default otherwise)."""
    shown = display(entry)
    if shown is not None:
        return shown.label
    value = entry.current.value
    if isinstance(value, CommandExecution):
        # A Command's completion waking a follow-up: which Command, and how it ended.
        return f"🦾 {value.name}: {value.state}"
    return f"⚡ {entry.key} = {value}"


def entry_display(system: AppSystem) -> Callable[[WorldEntry], EntryDisplay | None]:
    """The hook both presenters get: an embodiment's own entry labelled by its ``display_fn``
    (``system.input_displays``), any other by ``display_entry``. A ``None`` value — an entry cleared
    by its TTL, still labelling a reaction whose prompt carried it — falls through to the default."""
    displays = system.input_displays

    def display(entry: WorldEntry) -> EntryDisplay | None:
        label = displays.get(entry.key)
        value = entry.current.value
        if label is not None and value is not None:
            return EntryDisplay(label(value))
        return display_entry(entry)

    return display


# --- The ASR live view its timer polls --------------------------------------------------------


# The voice-activity line, per `AsrBridge.status()["vad"]`.
_VAD_LINES = {
    "speech": "**Voice activity:** 🟢 speech detected",
    "silence": "**Voice activity:** ⚪ no speech",
    "failed_open": "**Voice activity:** ⚠️ VAD failed: all audio goes to the recognizer",
    None: "**Voice activity:** — _no VAD (the recognizer ends utterances itself)_",
}


def vad_line(vad: str | None) -> str:
    """The ASR section's voice-activity line: whether the engine's VAD hears speech right now."""
    return _VAD_LINES.get(vad, _VAD_LINES[None])


def tick_asr(system: AppSystem) -> tuple[str, str, Any, str]:
    """Refresh the ASR panel: live interim text, a status line, the Stop-dictation button's
    enabled state (interactive only while dictating), and the voice-activity line."""
    bridge = system.bridge
    if bridge is None:
        return (
            _NO_LIVE_SPEECH,
            "_ASR unavailable._",
            gr.update(interactive=False),
            "",
        )
    display = bridge.display_text or _NO_LIVE_SPEECH
    status = bridge.status()
    mode = "dictation" if status["dictating"] else "conversation"
    status_md = (
        f"**ASR:** {'running' if status['running'] else 'stopped'} · "
        f"**mode:** {mode} (`{status['segmentation_mode']}`)"
    )
    if status["capture_error"]:
        # The engine can still say "running" while its mic stream is dead — say so plainly.
        status_md += f" · ⚠️ **capture:** {status['capture_error']}"
    return (
        display,
        status_md,
        gr.update(interactive=bool(status["dictating"])),
        vad_line(status["vad"]),
    )


def _fps(value: float | None) -> str:
    """An achieved rate, or an em dash before the first full sampling window."""
    return f"{value:.1f} fps" if value is not None else "— fps"


def _hand(label: str | None, confidence: float) -> str:
    """One hand on the Vision line: its validated label and score, the raw score of a hand whose top
    class missed the threshold (unvalidated), or "no crop" for a hand at the frame edge."""
    if label is None and confidence == 0.0:
        return "no crop"
    return f"{label if label is not None else 'below threshold'} ({confidence:.2f})"


def _stage_times(status: dict) -> str:
    """The newest classifier result's line (specs/latency-view.md, "The Vision panel's readout"):
    how old its frame is, and each stage's time on it; nothing before the first result."""
    age = status.get("frame_age_s")
    if age is None:
        return ""
    stages = " · ".join(
        f"{name} {ms:.0f} ms" for name, ms in status.get("stage_ms", [])
    )
    return f"\n- **newest result:** frame {age * 1000:.0f} ms old" + (
        f" · {stages}" if stages else ""
    )


def tick_vision(system: AppSystem) -> str:
    """Refresh the pipeline's part of the Vision section, as a few readable lines: first the camera
    feed's achieved rate (its own — the robot's daemon-paced feed, or the paced webcam) and each
    stage's next to the configured target (the hand demo's readout); then, under a "Hand signs"
    subheading, whether the gesture input runs and one table row per hand slot under the configured cap — the hand's raw top label and score (or "no
    hand") and its last delivered sign; then any camera (a failure, a stall) or stage
    error, each on its own line."""
    gesture = system.gesture
    if gesture is None:
        return "_Vision off (`vision.enabled` is false, or no camera)._"
    status = gesture.status()
    hands = status["hands"]
    signs = status["signs"]
    rows = ["| hand | seen | sign |", "|---|---|---|"]
    for i in range(status["max_hands"]):
        seen = (
            _hand(hands[i]["label"], hands[i]["confidence"])
            if i < len(hands)
            else "no hand"
        )
        sign = signs[i] if i < len(signs) and signs[i] is not None else "—"
        rows.append(f"| #{i + 1} | {seen} | {sign} |")
    target = status["target_fps"]
    lines = [
        (
            f"- **camera:** {_fps(status['stream_fps'])}\n"
            f"- **hand stage:** {_fps(status['hand_fps'])} (target {target:g})\n"
            f"- **classifier:** {_fps(status['classifier_fps'])} (target {target:g})"
            + _stage_times(status)
        ),
        "#### Hand signs",
        (
            f"**Status:** {'running' if status['running'] else 'stopped'} · "
            f"up to {status['max_hands']} hands"
        ),
        "\n".join(rows),
    ]
    if status["capture_error"]:
        lines.append(f"⚠️ **camera:** {status['capture_error']}")
    if status["error"]:
        lines.append(f"⚠️ **error:** {status['error']}")
    return "\n\n".join(lines)


# --- What the page shows of the body: the camera and its status --------------------------------------------------


def tick_view(system: AppSystem) -> tuple[npt.NDArray[np.uint8] | None, str]:
    """Refresh the camera and the body's status from ``embodiment.view()``: the frame (``None`` clears the image)
    and the Markdown under it — the body's status, after a one-line note when there is no frame. A
    body whose view fails shows the failure; the page never breaks on it."""
    body = system.embodiment
    if body is None:
        return None, "_No body._"
    try:
        view = body.view()
    except Exception as exc:
        logger.exception("the embodiment's view failed")
        return None, f"⚠️ _View unavailable: {html.escape(str(exc))}_"
    if view.frame is None:
        note = "_No camera frame yet._"
        return None, f"{note}\n\n{view.status}" if view.status else note
    return view.frame, view.status


# --- The latency view (specs/latency-view.md, "What the page shows") ---------------------------

# The table of reactions is the app's own markup (`latency_view.table`) in a `gr.HTML`, not a
# Dataframe: redrawn whole on each change, so a new row shows at once, and with browser code of its
# own for what a Dataframe does not do — follow the newest row, keep the place while an older one
# is read, select a row by a click (specs/latency-view.md, "What the page shows").
LATENCY_TABLE_CSS = """
.lv-toolbar { display: flex; justify-content: flex-end; margin-bottom: 4px; }
.lv-latest { padding: 2px 10px; border: 1px solid var(--border-color-primary); border-radius: 4px;
    background: var(--button-secondary-background-fill); cursor: pointer; font-size: 0.85em; }
.lv-scroll { max-height: 360px; overflow: auto; border: 1px solid var(--border-color-primary);
    border-radius: 4px; }
.lv-scroll table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.85em; }
.lv-scroll thead th { position: sticky; top: 0; z-index: 1; text-align: left; padding: 4px 8px;
    background: var(--background-fill-secondary); white-space: nowrap; }
.lv-scroll td { padding: 3px 8px; border-top: 1px solid var(--border-color-primary);
    white-space: pre-wrap; vertical-align: top; }
.lv-scroll tr[data-id] { cursor: pointer; }
.lv-scroll tr.lv-selected td { background: var(--color-accent-soft); }
"""

# Follows the newest row while it is the one selected and the table is scrolled to the bottom;
# otherwise keeps the reader's place across redraws. A click selects a row (the newest one means
# "follow the newest"); "Latest" scrolls down and selects the newest.
LATENCY_TABLE_JS = """
let following = true;
let place = 0;
const scroller = () => element.querySelector('.lv-scroll');
element.addEventListener('scroll', (event) => {
    const box = scroller();
    if (!box || event.target !== box) return;
    place = box.scrollTop;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 4;
    const newest = box.querySelector('tr[data-last]');
    following = atBottom && newest !== null && newest.classList.contains('lv-selected');
}, true);
const settle = () => {
    const box = scroller();
    if (!box) return;
    box.scrollTop = following ? box.scrollHeight : place;
};
const choose = (row) => {
    element.querySelectorAll('tr.lv-selected').forEach((r) => r.classList.remove('lv-selected'));
    if (row) row.classList.add('lv-selected');
};
element.addEventListener('click', (event) => {
    if (event.target.closest('.lv-latest')) {
        following = true;
        choose(element.querySelector('tr[data-last]'));
        settle();
        server.select_reaction(-1);
        return;
    }
    const row = event.target.closest('tr[data-id]');
    if (!row) return;
    const newest = row.hasAttribute('data-last');
    choose(row);
    following = newest && (() => {
        const box = scroller();
        return box !== null && box.scrollHeight - box.scrollTop - box.clientHeight < 4;
    })();
    server.select_reaction(newest ? -1 : Number(row.dataset.id));
});
watch('value', settle);
settle();
"""

_NO_LATENCY = (
    '<div style="opacity:0.7">No agent: there is no reaction to measure.</div>'
)


class LatencyPanel:
    """What the page's latency parts show, read from the collector on the page's timer.

    ``tick`` returns the glance line, the timeline, the summary, the table rows and the waterfall,
    each as ``gr.skip()`` when it is what was last sent — an idle page sends nothing — and the
    Latency tab's three parts skipped while that tab is closed. ``select_reaction`` picks the
    waterfall's reaction; with none picked, the newest is shown and followed."""

    def __init__(self, collector: LatencyCollector | None) -> None:
        self._collector = collector
        self._sent: dict[str, Any] = {}
        self.selected: int | None = None

    def _changed(self, name: str, value: Any) -> Any:
        if self._sent.get(name) == value:
            return gr.skip()
        self._sent[name] = value
        return value

    def _waterfall(self, snapshot: Any) -> str:
        records: tuple[ReactionRecord, ...] = snapshot.records
        chosen = next((r for r in records if r.reaction_id == self.selected), None)
        if chosen is None and records:
            chosen = records[0]
        return latency_view.waterfall(chosen, snapshot)

    def tick(self, tab_open: bool = True) -> tuple[Any, Any, Any, Any, Any]:
        collector = self._collector
        if collector is None:
            values = (_NO_LATENCY, "", "", "", "")
            return tuple(
                self._changed(n, v) for n, v in zip(_PARTS, values, strict=True)
            )  # type: ignore[return-value]
        snapshot = collector.snapshot()
        main = (
            self._changed("glance", latency_view.glance(snapshot)),
            self._changed("timeline", latency_view.timeline(snapshot)),
        )
        if not tab_open:
            return (*main, gr.skip(), gr.skip(), gr.skip())
        return (
            *main,
            self._changed("summary", latency_view.summary(snapshot)),
            self._changed("table", latency_view.table(snapshot, self.selected)),
            self._changed("waterfall", self._waterfall(snapshot)),
        )

    def select_reaction(self, reaction_id: int | None) -> str:
        """Pick the waterfall's reaction (the table's browser code calls this on a click): its
        number, or ``None`` or a negative one to follow the newest. The page shows it on the next
        tick."""
        self.selected = (
            int(reaction_id)
            if reaction_id is not None and int(reaction_id) >= 0
            else None
        )
        return "ok"

    def reset(self) -> None:
        if self._collector is not None:
            self._collector.reset_statistics()
        self._sent.pop("summary", None)


_PARTS = ("glance", "timeline", "summary", "table", "waterfall")


# --- The session bar ----------------------------------------------------------------------------


def session_status(session: Session, blockers: Sequence[str] = ()) -> str:
    """The line over Start and Stop: running, starting, stopping, or stopped — with why the last
    Start did not start (``Session.reasons``) and why the saved config cannot start now
    (*blockers*), or ready to start when there are none."""
    status = session.status
    if status == "running":
        return "🟢 **Running.** Stop it to change the config in the Setup tab."
    if status == "starting":
        return "⏳ **Starting…**"
    if status == "stopping":
        return "⏳ **Stopping…**"
    reasons = list(dict.fromkeys([*session.reasons, *blockers]))
    if reasons:
        listed = "\n".join(f"- {html.escape(reason)}" for reason in reasons)
        return (
            "⚠️ **Not started.** Fix this in the Setup and Secrets tabs, then Start:\n\n"
            + listed
        )
    return (
        "⚪ **Stopped — ready to start.** Start runs the saved config (the Setup tab)."
    )


def session_buttons(session: Session) -> tuple[bool, bool]:
    """Whether Start and Stop can be pressed: Start only while stopped, Stop only while running
    (both greyed while starting or stopping)."""
    status = session.status
    return status == "stopped", status == "running"


# What the bar's blockers were computed against: the keys' revision, the session's version and
# status. A change of any computes them again; a tick with none costs no check.
_Watched = tuple[int, int, str]


def _blockers(model: AppModel, session: Session) -> list[str]:
    """Why the saved config cannot start now; none unless stopped."""
    if session.status != "stopped":
        return []
    return model.blockers(model.saved_config())


@dataclass(frozen=True)
class _SessionBar:
    """The Interaction tab's bar, as the page wires it to the Setup panel."""

    notice: gr.Markdown
    version: gr.State
    outputs: list[Any]
    recheck: Callable[[], tuple[Any, ...]]


def _session_bar(model: AppModel, session: Session) -> _SessionBar:
    """Start, Stop, the status line and the unsaved-changes notice."""
    can_start, can_stop = session_buttons(session)
    status = gr.Markdown(session_status(session))
    with gr.Row():
        start = gr.Button("Start", variant="primary", scale=0, interactive=can_start)
        stop = gr.Button("Stop", scale=0, interactive=can_stop)
        # "The Setup tab has unsaved changes: Start uses the saved config.", or nothing.
        notice = gr.Markdown()
    # The session's version: a start or a stop, from any browser tab or at launch, draws the
    # session's surfaces again.
    version = gr.State(session.version)
    blockers = gr.State([])
    seen = gr.State(None)

    def view(found: list[str], watched: _Watched) -> tuple[Any, ...]:
        can_start, can_stop = session_buttons(session)
        return (
            session_status(session, found),
            gr.update(interactive=can_start),
            gr.update(interactive=can_stop),
            session.version,
            found,
            watched,
        )

    def follow(
        found: list[str], watched: _Watched | None, force: bool
    ) -> tuple[Any, ...]:
        now: _Watched = (model.secrets.revision, session.version, session.status)
        if force or now != watched:
            found = _blockers(model, session)
        return view(found, now)

    def refresh(found: list[str], watched: _Watched | None) -> tuple[Any, ...]:
        return follow(found, watched, False)

    def recheck() -> tuple[Any, ...]:
        return follow([], None, True)

    def on_start() -> tuple[Any, ...]:
        session.start(model)
        return recheck()

    def on_stop() -> tuple[Any, ...]:
        session.stop()
        return recheck()

    outputs = [status, start, stop, version, blockers, seen]
    # Greyed at once on a click, before the (possibly slow) start or stop runs.
    greyed = (gr.update(interactive=False), gr.update(interactive=False))
    start.click(lambda: greyed, outputs=[start, stop]).then(  # pyright: ignore[reportAttributeAccessIssue]
        on_start, outputs=outputs
    )
    stop.click(lambda: greyed, outputs=[start, stop]).then(  # pyright: ignore[reportAttributeAccessIssue]
        on_stop, outputs=outputs
    )
    # On the page's load, then polled: a key saved, or a start or stop from another browser tab or
    # at launch, computes the blockers again; a tick with no change only redraws.
    gr.on(None, recheck, outputs=outputs, show_progress="hidden")
    gr.Timer(1.0).tick(  # pyright: ignore[reportAttributeAccessIssue]
        refresh, inputs=[blockers, seen], outputs=outputs, show_progress="hidden"
    )
    return _SessionBar(notice=notice, version=version, outputs=outputs, recheck=recheck)


# --- The session's surfaces, drawn once per session --------------------------------------------

_NO_SESSION = "_No session is running. Start one above._"


def _guarded(
    session: Session, running: Running, tick: Callable[[], Any], outputs: int
) -> Callable[[], Any]:
    """*tick*, or nothing to send once *running* is no longer the session's: a timer of a draw
    the page has replaced never reads a stopped session's objects."""

    def guarded() -> Any:
        if session.current is not running:
            skipped = tuple(gr.skip() for _ in range(outputs))
            return skipped if outputs > 1 else skipped[0]
        return tick()

    return guarded


def _banner(system: AppSystem) -> None:
    if not system.missing:
        return
    items = "\n".join(f"> - {html.escape(m)}" for m in system.missing)
    gr.Markdown(
        "> ⚠️ **Some parts could not start — see the Setup tab, then Stop and Start.**\n"
        f"{items}\n>\n> The session still runs; you can type below to drive the agent wherever "
        "it was built. With no embodiment, the `say` voice and its action commands are not "
        "wired (the agent reasons but cannot speak or act); with a body but no voice, `say` "
        "stays wired and reports a TTS error."
    )


def _interaction_surfaces(session: Session, running: Running) -> None:
    """Everything below the session bar on the Interaction tab, over one running session: the
    banner, the latency glance and timeline, the Conversation beside the body's camera and the
    typed-text hatch, the ASR beside the Vision, the debug panels. Input widgets only touch
    `system.submit_speech` / the bridge; the panels refresh on their own timers."""
    system = running.system
    loop = system.loop
    wica = system.wica
    latency = LatencyPanel(system.latency)

    def on_send(user_text: str) -> str:
        system.submit_speech(user_text)  # empty/whitespace is ignored inside
        return ""  # clear the box; the transcript updates via its own timer

    def on_stop_dictation() -> None:
        bridge = system.bridge
        if bridge is not None and loop is not None and session.current is running:
            asyncio.run_coroutine_threadsafe(bridge.stop_dictation(), loop)

    # The snapshot buttons run on the Gradio thread — the caller's thread, as the library's
    # snapshot functions require — and return a status line rather than raising.
    def on_save_detector(folder: str) -> str:
        gesture = system.gesture
        return gesture.save_detector_snapshot(folder) if gesture is not None else ""

    def on_save_classifier(folder: str) -> str:
        gesture = system.gesture
        return gesture.save_classifier_snapshot(folder) if gesture is not None else ""

    name = system.display_name
    if name:
        gr.Markdown(f"### Talk to a WICA agent embodied in a {name}")
    _banner(system)
    gr.Markdown(
        "Speak (or type) a turn; the agent reasons over the World and replies with its **🗣️ "
        "say** voice. It handles **one reasoning call at a time** (v1): input arriving mid-step is "
        "dropped, not queued. On the left, each step is one collapsible **💬 reaction**; inside it, "
        "items are labelled by channel — **🗣️ say** (what you heard), **💭 private text** (what "
        "the model wrote outside its `<say>` tag, never heard), **🦾** commands, **🚫 noop** (it "
        "chose not to react). Every command item shows its **execution state "
        "live**: a spinner while it runs, then ✅ complete / ❌ failed / ⏹ cancelled with its result "
        "and duration."
    )

    # The latency view's main surface, first: the last reaction at a glance, and the last 30 s
    # live (specs/latency-view.md, "What the page shows").
    with gr.Row(), gr.Column():
        gr.Markdown("### Latency")
        latency_glance = gr.HTML(latency_view.legend())
        latency_timeline = gr.HTML("")

    # The main surface, two rows of two equal columns (specs/app.md, "The layout"): the
    # Conversation beside the body's camera and the typed-text box; then ASR beside Vision.
    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            # The contrib transcript: its own timer, pushed only when the transcript changed.
            conversation_panel(running.transcript, height=CONVERSATION_HEIGHT)
        with gr.Column(scale=1):
            # The body's camera, its faces drawn (embodiment.view(); its status is Vision's).
            camera = gr.Image(
                label="Camera (mirrored)",
                type="numpy",
                interactive=False,
                show_label=False,
            )
            with gr.Row():
                # gradio's dynamic event methods (.click/.submit/.change/.tick) aren't in the
                # stubs, hence the narrow ignores below.
                msg = gr.Textbox(
                    placeholder="Type a turn (works with no mic / no ASR key)…",
                    show_label=False,
                    scale=5,
                )
                send = gr.Button("Send", variant="primary", scale=1)

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            gr.Markdown("### ASR")
            asr_status = gr.Markdown("_ASR status…_")
            asr_vad = gr.Markdown("_Voice activity…_")
            asr_live = gr.Textbox(
                label="Live speech (interim)",
                value=_NO_LIVE_SPEECH,
                interactive=False,
                lines=2,
            )
            stop_dictation = gr.Button("Stop dictation", interactive=False)
        with gr.Column(scale=1):
            gr.Markdown("### Vision")
            vision_status = gr.Markdown("_Vision status…_")
            # What the body knows of what it sees: on the robot, whom the head follows and the
            # users present.
            camera_status = gr.Markdown("_No camera frame yet._")
            if system.gesture is not None:
                # As in the vision-modules hand demo: save what each stage last consumed.
                snapshot_folder = gr.Textbox(
                    value=str(_SNAPSHOT_DIR), label="Snapshot folder"
                )
                with gr.Row():
                    save_detector = gr.Button("Save detector snapshot")
                    save_classifier = gr.Button("Save classifier snapshot")
                snapshot_status = gr.Markdown("")
                save_detector.click(  # pyright: ignore[reportAttributeAccessIssue]
                    on_save_detector, inputs=snapshot_folder, outputs=snapshot_status
                )
                save_classifier.click(  # pyright: ignore[reportAttributeAccessIssue]
                    on_save_classifier, inputs=snapshot_folder, outputs=snapshot_status
                )

    # Debug surface last, on one line: the toggle, then World state and Reaction side by side.
    # The toggle is visibility only: the contrib panels own their timers and keep ticking while
    # hidden (see specs/app.md, "Refresh cost").
    show_debug = gr.Checkbox(label="Show debug panels", value=False)
    with gr.Row(visible=False, equal_height=False) as debug_panels:
        with gr.Column():
            gr.Markdown("### World state (live)")
            if wica is not None:
                world_state_panel(wica.world)
            else:
                gr.Markdown("_No agent — there is no World to show._")
        with gr.Column():
            gr.Markdown("### Reaction (prompt + instrumentation)")
            reaction_panel(running.reactions)

    send.click(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
    msg.submit(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
    stop_dictation.click(on_stop_dictation)  # pyright: ignore[reportAttributeAccessIssue]
    show_debug.change(  # pyright: ignore[reportAttributeAccessIssue]
        lambda show: gr.update(visible=show), inputs=show_debug, outputs=debug_panels
    )

    # The glance line and the timeline on their own timer; an unchanged part is not sent.
    gr.Timer(0.2).tick(  # pyright: ignore[reportAttributeAccessIssue]
        _guarded(session, running, lambda: latency.tick(False)[:2], 2),
        outputs=[latency_glance, latency_timeline],
    )
    # The app's two timers: one reads the ASR bridge and the gesture input (the status lines and
    # the live text), the other the body's view, on its own so a frame's encoding never delays
    # the live text (the contrib panels above each own theirs).
    gr.Timer(0.2).tick(  # pyright: ignore[reportAttributeAccessIssue]
        _guarded(session, running, lambda: (*tick_asr(system), tick_vision(system)), 5),
        outputs=[asr_live, asr_status, stop_dictation, asr_vad, vision_status],
    )
    gr.Timer(0.2).tick(  # pyright: ignore[reportAttributeAccessIssue]
        _guarded(session, running, lambda: tick_view(system), 2),
        outputs=[camera, camera_status],
    )


def _latency_surfaces(
    session: Session, running: Running, latency_open: gr.State
) -> None:
    """The Latency tab over one running session: the statistics, one row per reaction, the
    selected reaction's waterfall, read only while the tab is open."""
    latency = LatencyPanel(running.system.latency)
    gr.Markdown("### Latency, reaction by reaction")
    with gr.Row():
        latency_summary = gr.Markdown("")
        latency_reset = gr.Button("Reset statistics", scale=0)

    # A plain function: Gradio marks a server function with an attribute, which a bound method
    # cannot carry. Its name is the one the table's browser code calls.
    def select_reaction(reaction_id: int | None) -> str:
        return latency.select_reaction(reaction_id)

    latency_table = gr.HTML(
        "",
        css_template=LATENCY_TABLE_CSS,
        js_on_load=LATENCY_TABLE_JS,
        server_functions=[select_reaction],
        elem_id="latency-table",
    )
    latency_waterfall = gr.HTML("")

    def detail(tab_open: bool) -> tuple[Any, Any, Any]:
        if not tab_open or session.current is not running:
            return (gr.skip(), gr.skip(), gr.skip())
        _, _, summary, table, waterfall = latency.tick(True)
        return (summary, table, waterfall)

    latency_reset.click(latency.reset)  # pyright: ignore[reportAttributeAccessIssue]
    gr.Timer(0.2).tick(  # pyright: ignore[reportAttributeAccessIssue]
        detail,
        inputs=latency_open,
        outputs=[latency_summary, latency_table, latency_waterfall],
    )


# --- The page ------------------------------------------------------------------------------------


def build_page(
    model: AppModel, session: Session, *, open_on: str = "setup"
) -> gr.Blocks:
    """The app's page, built once per process around the session (specs/app.md, "What the user
    sees"): **Interaction** (the session bar, then the running session's surfaces), **Latency**,
    then app-setup's **Setup** and **Secrets** panels. The session's surfaces are drawn in
    ``gr.render``s keyed on ``Session.version``, so a start or a stop draws them again over that
    session's own objects. It opens on *open_on* (``"interaction"`` when the session started at
    launch, else ``"setup"``)."""
    with gr.Blocks(title=model.title) as page:
        gr.Markdown(f"# {model.title}")
        # Which tab is open: the latency detail is read only while its tab is.
        latency_open = gr.State(open_on == "latency")
        with gr.Tabs(selected=open_on):
            with gr.Tab("Interaction", id="interaction") as interaction_tab:
                bar = _session_bar(model, session)

                @gr.render(inputs=bar.version)
                def _interaction(_version: int) -> None:
                    running = session.current
                    if running is None:
                        gr.Markdown(_NO_SESSION)
                        return
                    _interaction_surfaces(session, running)

            with gr.Tab("Latency", id="latency") as latency_tab:

                @gr.render(inputs=bar.version)
                def _latency(_version: int) -> None:
                    running = session.current
                    if running is None:
                        gr.Markdown(_NO_SESSION)
                        return
                    _latency_surfaces(session, running, latency_open)

            with gr.Tab("Setup", id="setup"):
                setup = setup_panel(model)
            with gr.Tab("Secrets", id="secrets"):
                secrets_panel(model, setup.draft)
        interaction_tab.select(  # pyright: ignore[reportAttributeAccessIssue]
            lambda: False, outputs=latency_open
        )
        latency_tab.select(lambda: True, outputs=latency_open)  # pyright: ignore[reportAttributeAccessIssue]
        # The bar's blockers are the saved config's now: checked again when the tab is selected
        # and when the Setup panel saves or resets (a key change is caught by the tick).
        interaction_tab.select(  # pyright: ignore[reportAttributeAccessIssue]
            bar.recheck, outputs=bar.outputs, show_progress="hidden"
        )
        setup.saved.change(bar.recheck, outputs=bar.outputs, show_progress="hidden")
        # Start uses the saved config: said next to it while the Setup tab has unsaved edits.
        for changed in (setup.draft.change, setup.saved.change):
            changed(
                lambda draft: unsaved_notice(model, draft),
                inputs=[setup.draft],
                outputs=[bar.notice],
                show_progress="hidden",
            )
    return page
