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
- ``entry_display(system)`` — that hook with the embodiment's own entries in front of it: each
  ``InputEntry`` an embodiment declares carries its ``display_fn``, so the body's wording reaches the
  transcript without this module knowing the body.
- ``build_ui(system)`` — builds the presenters (``TranscriptLog`` / ``ReactionLog``, subscribed to
  the agent's Events in their constructors, both given ``entry_display(system)``), lays out the page (the
  Conversation beside the body's camera and the typed-text hatch, then the app's ASR beside its
  Vision, then the debug toggle and the two debug panels side by side, a banner for anything
  missing), and returns the page. The UI wires nothing into the agent: its presenters
  subscribe to the agent's Events themselves.
- ``tick_asr`` — the live view the ASR panel's timer polls; ``tick_view`` — what the camera's
  timer shows of the body (``embodiment.view()``). The app owns these two timers.

The only dependency on ``system`` is the ``AppSystem`` *type* (for annotations), imported under
``TYPE_CHECKING`` so there is no import cycle: ``system`` imports ``build_ui`` from here at runtime,
and this module never imports ``system`` at runtime. See specs/app.md.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Callable
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
    import numpy as np
    import numpy.typing as npt

    from wica_interaction.system import AppSystem

logger = logging.getLogger(__name__)

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


# --- Layout ------------------------------------------------------------------------------------


def build_ui(system: AppSystem) -> gr.Blocks:
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
    display = entry_display(system)
    transcript = TranscriptLog(wica, display)
    reactions = ReactionLog(wica, display)

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

    # The page is named after the body the app was launched with (specs/embodiment.md) — also when
    # that body failed to enter (the banner says why). Only a config that never loaded has no name.
    name = system.display_name
    title = f"WICA Interaction — {name}" if name else "WICA Interaction"
    heading = (
        f"# Talk to a WICA agent embodied in a {name}"
        if name
        else "# Talk to a WICA agent"
    )
    with gr.Blocks(title=title) as demo:
        gr.Markdown(heading)
        if system.missing:
            items = "\n".join(f"> - {html.escape(m)}" for m in system.missing)
            gr.Markdown(
                "> ⚠️ **Some parts could not start — set the missing variables (or connect the body) "
                "and restart.**\n"
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set. With no embodiment, the `say` voice and its action commands are not "
                "wired (the agent reasons but cannot speak or act); with a body but no voice, `say` "
                "stays wired and reports a TTS error."
            )
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

        # The main surface, two rows of two equal columns (specs/app.md, "The layout"): the
        # Conversation beside the body's camera and the typed-text box; then ASR beside Vision.
        with gr.Row(equal_height=False):
            with gr.Column(scale=1):
                # The contrib transcript: its own timer, pushed only when the transcript changed.
                conversation_panel(transcript)
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
                # What the body knows of what it sees: on the robot, whom the head follows and
                # the users present.
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
                        on_save_detector,
                        inputs=snapshot_folder,
                        outputs=snapshot_status,
                    )
                    save_classifier.click(  # pyright: ignore[reportAttributeAccessIssue]
                        on_save_classifier,
                        inputs=snapshot_folder,
                        outputs=snapshot_status,
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
                reaction_panel(reactions)

        send.click(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        msg.submit(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        stop_dictation.click(on_stop_dictation)  # pyright: ignore[reportAttributeAccessIssue]
        show_debug.change(  # pyright: ignore[reportAttributeAccessIssue]
            lambda show: gr.update(visible=show),
            inputs=show_debug,
            outputs=debug_panels,
        )

        # The app's two timers: one reads the ASR bridge and the gesture input (the status lines
        # and the live text), the other the body's view, on its own so a frame's encoding never
        # delays the live text (the contrib panels above each own theirs).
        asr_timer = gr.Timer(0.2)
        asr_timer.tick(
            lambda: (*tick_asr(system), tick_vision(system)),
            outputs=[asr_live, asr_status, stop_dictation, asr_vad, vision_status],
        )  # pyright: ignore[reportAttributeAccessIssue]
        view_timer = gr.Timer(0.2)
        view_timer.tick(  # pyright: ignore[reportAttributeAccessIssue]
            lambda: tick_view(system), outputs=[camera, camera_status]
        )

    return demo
