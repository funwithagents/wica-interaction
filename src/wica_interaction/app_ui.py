"""The Gradio UI: the page layout, the presenters it owns, and the app's own ASR live view.

This is the *presentation* half of the app, split from the wiring in [system.py](system.py). The three
framework-observability panels — Conversation, World state, Reaction (prompt + instrumentation) —
are **not** implemented here: they are WICA's reusable ``wica.contrib.gradio`` components (the
``wica[gradio]`` extra), each a presenter built over the ``Wica`` plus a panel function that creates
the components and owns the timer keeping them live. What this module holds:

- ``display_entry`` — the one hook through which the app injects persona knowledge into those generic
  panels: how a World entry reads to a *person* (`speech_input` → `🗣️ "…"`, a `say` execution →
  the spoken text). The person-facing counterpart of the ``speech_input`` ``serialize_fn`` in
  [system.py](system.py).
- ``build_ui(system)`` — builds the presenters (``TranscriptLog`` / ``ReactionLog``, subscribed to
  the agent's Events in their constructors, both given ``display_entry``), lays out the page (the
  three contrib panels, the app's ASR panel, the typed-text hatch, a banner for anything missing,
  the debug toggle), and returns the page plus the transcript on an ``AppUi`` handle so the app can
  wire the transcript's sink (``wire`` in [system.py](system.py)). The UI wires nothing itself.
- ``tick_asr`` — the live view the ASR panel's timer polls (the only timer the app owns).

The only dependency on ``system`` is the ``AppSystem`` *type* (for annotations), imported under
``TYPE_CHECKING`` so there is no import cycle: ``system`` imports ``build_ui`` from here at runtime,
and this module never imports ``system`` at runtime. See specs/app.md.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gradio as gr
from wica import CommandExecution, WorldEntry
from wica.contrib.gradio import (
    EntryDisplay,
    ReactionLog,
    TranscriptLog,
    conversation_panel,
    reaction_panel,
    world_state_panel,
)

if TYPE_CHECKING:
    from wica_interaction.system import AppSystem

# The output Command's name (the embodiment's `say.__name__`): the voice, whose execution item the
# hook titles by channel with the spoken text as body.
_SAY_COMMAND_NAME = "say"

_NO_LIVE_SPEECH = "(no speech yet)"

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


@dataclass(frozen=True)
class AppUi:
    """What build_ui() returns: the page, plus the transcript presenter the app wires into the agent
    (`transcript.output_sink` → `wica.set_output_sink`)."""

    blocks: gr.Blocks
    transcript: TranscriptLog


# --- The ASR live view its timer polls --------------------------------------------------------


def tick_asr(system: AppSystem) -> tuple[str, str, Any]:
    """Refresh the ASR panel: live interim text, a status line, and the Stop-dictation button's
    enabled state (interactive only while dictating)."""
    bridge = system.bridge
    if bridge is None:
        return _NO_LIVE_SPEECH, "_ASR unavailable._", gr.update(interactive=False)
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
    return display, status_md, gr.update(interactive=bool(status["dictating"]))


def _fps(value: float | None) -> str:
    """An achieved rate, or an em dash before the first full sampling window."""
    return f"{value:.1f} fps" if value is not None else "— fps"


def _hand(label: str | None, confidence: float) -> str:
    """One hand on the Vision line: its validated label and score, the raw score of a hand whose top
    class missed the threshold (unvalidated), or "no crop" for a hand at the frame edge."""
    if label is None and confidence == 0.0:
        return "no crop"
    return f"{label if label is not None else 'below threshold'} ({confidence:.2f})"


def tick_vision(system: AppSystem) -> str:
    """Refresh the Vision line: whether the gesture input runs, every hand's raw top label and score
    in slot order (or "no hand") under the configured cap, every hand's last delivered sign, each node's achieved rate next to the
    configured one — the camera stream's poll rate, the two stages' target (the hand demo's
    readout) — and any camera / stage error."""
    gesture = system.gesture
    if gesture is None:
        return "_Vision off (`vision.enabled` is false, or no camera)._"
    status = gesture.status()
    seen = ", ".join(
        f"#{i} {_hand(hand['label'], hand['confidence'])}"
        for i, hand in enumerate(status["hands"], start=1)
    )
    signs = ", ".join(
        f"#{hand} {sign if sign is not None else '—'}"
        for hand, sign in enumerate(status["signs"], start=1)
    )
    target = status["target_fps"]
    status_md = (
        f"**Vision:** {'running' if status['running'] else 'stopped'} · "
        f"**hands (max {status['max_hands']}):** {seen or 'no hand'} · **signs:** {signs} · "
        f"**stream:** {_fps(status['stream_fps'])} (poll {target:g}) · "
        f"**hand stage:** {_fps(status['hand_fps'])} (target {target:g}) · "
        f"**classifier:** {_fps(status['classifier_fps'])} (target {target:g})"
    )
    if status["capture_error"]:
        status_md += f" · ⚠️ **camera:** {status['capture_error']}"
    if status["error"]:
        status_md += f" · ⚠️ **error:** {status['error']}"
    return status_md


# --- Layout ------------------------------------------------------------------------------------


def build_ui(system: AppSystem) -> AppUi:
    """Assemble the page over an already-built (not yet wired, not yet started) system: Conversation
    + ASR as the main surface, World + Reaction behind a debug toggle, a banner for any missing
    credentials, and the typed-text hatch into `speech_input`. Input widgets only touch
    `system.submit_speech` / the bridge; the panels refresh on their own timers. With no agent
    (`system.wica is None`) the presenters are built over `None` — the contrib's explore-only mode:
    they subscribe to nothing and render empty."""
    loop = system.loop
    wica = system.wica
    # The presenters subscribe to the agent's Events in their constructors, so the Wica must already
    # exist (it does: build_system ran first). One hook for both, so transcript items and reaction
    # labels read alike.
    transcript = TranscriptLog(wica, display_entry)
    reactions = ReactionLog(wica, display_entry)

    def on_send(user_text: str) -> str:
        system.submit_speech(user_text)  # empty/whitespace is ignored inside
        return ""  # clear the box; the transcript updates via its own timer

    def on_stop_dictation() -> None:
        bridge = system.bridge
        if bridge is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(bridge.stop_dictation(), loop)

    # The snapshot buttons run on the Gradio thread — the caller's thread, as the library's
    # snapshot functions require — and return a status line rather than raising.
    def on_save_detector(folder: str) -> str:
        gesture = system.gesture
        return gesture.save_detector_snapshot(folder) if gesture is not None else ""

    def on_save_classifier(folder: str) -> str:
        gesture = system.gesture
        return gesture.save_classifier_snapshot(folder) if gesture is not None else ""

    # The page is named after the body the app was launched with (specs/embodiment.md).
    name = system.embodiment.display_name if system.embodiment is not None else "WICA"
    with gr.Blocks(title=f"WICA Interaction — {name}") as demo:
        gr.Markdown(f"# Talk to a WICA agent embodied in a {name}")
        if system.missing:
            items = "\n".join(f"> - {html.escape(m)}" for m in system.missing)
            gr.Markdown(
                "> ⚠️ **Some parts could not start — set the missing variables (or connect the body) "
                "and restart.**\n"
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set. With no embodiment, the `say` voice and its action commands are not "
                "wired (the agent reasons but cannot speak or act); with no voice, `say` reports a "
                "TTS error."
            )
        gr.Markdown(
            "Speak (or type) a turn; the agent reasons over the World and replies with its **🗣️ "
            "say** voice. It handles **one reasoning call at a time** (v1): input arriving mid-step is "
            "dropped, not queued. On the left, each step is one collapsible **💬 reaction**; inside it, "
            "items are labelled by channel — **🗣️ say** (what you heard), **💭 output sink** (private "
            "free text), **🦾** commands, **🚫 noop**. Every command item shows its **execution state "
            "live**: a spinner while it runs, then ✅ complete / ❌ failed / ⏹ cancelled with its result "
            "and duration."
        )

        with gr.Row():
            with gr.Column(scale=3):
                # The contrib transcript: its own timer, pushed only when the transcript changed.
                conversation_panel(transcript)
                with gr.Row():
                    # gradio's dynamic event methods (.click/.submit/.change/.tick) aren't in the
                    # stubs, hence the narrow ignores below.
                    msg = gr.Textbox(
                        placeholder="Type a turn (works with no mic / no ASR key)…",
                        show_label=False,
                        scale=5,
                    )
                    send = gr.Button("Send", variant="primary", scale=1)

                gr.Markdown("### ASR")
                asr_status = gr.Markdown("_ASR status…_")
                asr_live = gr.Textbox(
                    label="Live speech (interim)",
                    value=_NO_LIVE_SPEECH,
                    interactive=False,
                    lines=2,
                )
                stop_dictation = gr.Button("Stop dictation", interactive=False)

                gr.Markdown("### Vision")
                vision_status = gr.Markdown("_Vision status…_")
                if system.gesture is not None:
                    # As in the vision-modules hand demo: save what each stage last consumed.
                    with gr.Row():
                        snapshot_folder = gr.Textbox(
                            value=str(_SNAPSHOT_DIR), label="Snapshot folder", scale=3
                        )
                        save_detector = gr.Button("Save detector snapshot", scale=1)
                        save_classifier = gr.Button("Save classifier snapshot", scale=1)
                    snapshot_status = gr.Markdown("")
                    save_detector.click(  # pyright: ignore[reportAttributeAccessIssue]
                        on_save_detector,
                        inputs=snapshot_folder,
                        outputs=snapshot_status,
                    )
                    save_classifier.click(  # pyright: ignore[reportAttributeAccessIssue]
                        on_save_classifier,
                        inputs=snapshot_folder,
                        outputs=snapshot_status,
                    )

            with gr.Column(scale=2):
                show_debug = gr.Checkbox(label="Show debug panels", value=False)
                # The toggle is visibility only: the contrib panels own their timers and keep
                # ticking while hidden (see specs/app.md, "Refresh cost").
                with gr.Column(visible=False) as debug_panels:
                    gr.Markdown("### World state (live)")
                    if wica is not None:
                        world_state_panel(wica.world)
                    else:
                        gr.Markdown("_No agent — there is no World to show._")
                    gr.Markdown("### Reaction (prompt + instrumentation)")
                    reaction_panel(reactions)

        send.click(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        msg.submit(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        stop_dictation.click(on_stop_dictation)  # pyright: ignore[reportAttributeAccessIssue]
        show_debug.change(  # pyright: ignore[reportAttributeAccessIssue]
            lambda show: gr.update(visible=show),
            inputs=show_debug,
            outputs=debug_panels,
        )

        # The one timer the app owns: the ASR panel reads the bridge and the Vision line reads the
        # gesture input (the contrib panels above each own theirs, so the surfaces never share one
        # round-trip).
        asr_timer = gr.Timer(0.2)
        asr_timer.tick(
            lambda: (*tick_asr(system), tick_vision(system)),
            outputs=[asr_live, asr_status, stop_dictation, vision_status],
        )  # pyright: ignore[reportAttributeAccessIssue]

    return AppUi(blocks=demo, transcript=transcript)
