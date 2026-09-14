"""The Gradio UI: the page layout, the presenters it owns, and the app's own ASR live view.

This is the *presentation* half of the app, split from the wiring in [app.py](app.py). The three
framework-observability panels — Conversation, World state, Prompt — are **not** implemented here:
they are WICA's reusable ``wica.contrib.gradio`` components (the ``wica[gradio]`` extra), each a
presenter built over the ``Wica`` plus a panel function that creates the components and owns the
timer keeping them live. What this module holds:

- ``display_entry`` — the one hook through which the app injects persona knowledge into those generic
  panels: how a World entry reads to a *person* (`speech_input` → `🗣️ "…"`, a `say` execution →
  the spoken text). The person-facing counterpart of the ``speech_input`` ``serialize_fn`` in
  [app.py](app.py).
- ``build_ui(system)`` — builds the presenters (``TranscriptLog`` / ``PromptLog``, subscribed to the
  agent's Events in their constructors, both given ``display_entry``), lays out the page (the three
  contrib panels, the app's ASR panel, the typed-text hatch, a banner for anything missing, the
  debug toggle), and returns the page plus the transcript on an ``AppUi`` handle so the app can wire
  the transcript's sink (``wire`` in [app.py](app.py)). The UI wires nothing itself.
- ``tick_asr`` — the live view the ASR panel's timer polls (the only timer the app owns).

The only dependency on ``app`` is the ``AppSystem`` *type* (for annotations), imported under
``TYPE_CHECKING`` so there is no import cycle: ``app`` imports ``build_ui`` from here at runtime, and
this module never imports ``app`` at runtime. See specs/app.md.
"""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gradio as gr
from wica import CommandExecution, WorldEntry
from wica.contrib.gradio import (
    EntryDisplay,
    PromptLog,
    TranscriptLog,
    conversation_panel,
    prompt_panel,
    world_state_panel,
)

if TYPE_CHECKING:
    from reachy_mini_interaction_wica.app import AppSystem

# The output Command's name (VoiceOutput.say.__name__): the voice, whose execution item the hook
# titles by channel with the spoken text as body.
_SAY_COMMAND_NAME = "say"

_NO_LIVE_SPEECH = "(no speech yet)"


def display_entry(entry: WorldEntry) -> EntryDisplay | None:
    """How the app's entries show in the transcript and the prompt labels — the person-facing
    counterpart of the `speech_input` serialize_fn. Anything it doesn't recognise returns None and
    gets the contrib's generic default (`⚡ key = value`, `🦾 name(args)` for a Command)."""
    value = entry.current.value
    if entry.key == "speech_input":
        return EntryDisplay(f'🗣️ "{value}"')
    if isinstance(value, CommandExecution) and value.name == _SAY_COMMAND_NAME:
        # The voice: title it by channel and show what the robot set out to say as the body.
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


# --- Layout ------------------------------------------------------------------------------------


def build_ui(system: AppSystem) -> AppUi:
    """Assemble the page over an already-built (not yet wired, not yet started) system: Conversation
    + ASR as the main surface, World + Prompt behind a debug toggle, a banner for any missing
    credentials, and the typed-text hatch into `speech_input`. Input widgets only touch
    `system.submit_speech` / the bridge; the panels refresh on their own timers. With no agent
    (`system.wica is None`) the presenters are built over `None` — the contrib's explore-only mode:
    they subscribe to nothing and render empty."""
    loop = system.loop
    wica = system.wica
    # The presenters subscribe to the agent's Events in their constructors, so the Wica must already
    # exist (it does: build_system ran first). One hook for both, so transcript items and prompt
    # labels read alike.
    transcript = TranscriptLog(wica, display_entry)
    prompts = PromptLog(wica, display_entry)

    def on_send(user_text: str) -> str:
        system.submit_speech(user_text)  # empty/whitespace is ignored inside
        return ""  # clear the box; the transcript updates via its own timer

    def on_stop_dictation() -> None:
        bridge = system.bridge
        if bridge is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(bridge.stop_dictation(), loop)

    with gr.Blocks(title="Reachy Mini Interaction WICA") as demo:
        gr.Markdown("# Talk to a WICA-driven Reachy Mini")
        if system.missing:
            items = "\n".join(f"> - {html.escape(m)}" for m in system.missing)
            gr.Markdown(
                "> ⚠️ **Some parts could not start — set the missing variables (or connect the robot) "
                "and restart.**\n"
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set. With no robot, the `say` voice and the robot actions are not wired "
                "(the agent reasons but cannot speak or move); with no TTS key, `say` exists but reports "
                'a TTS error. For offline development, set `reachy.backend` to `"fake"` or `"sim"`.'
            )
        gr.Markdown(
            "Speak (or type) a turn; the assistant reasons over the World and replies with its **🗣️ "
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
                    prompt_panel(prompts)

        send.click(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        msg.submit(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        stop_dictation.click(on_stop_dictation)  # pyright: ignore[reportAttributeAccessIssue]
        show_debug.change(  # pyright: ignore[reportAttributeAccessIssue]
            lambda show: gr.update(visible=show),
            inputs=show_debug,
            outputs=debug_panels,
        )

        # The one timer the app owns: the ASR panel reads the bridge (the contrib panels above each
        # own theirs, so the surfaces never share one round-trip).
        asr_timer = gr.Timer(0.2)
        asr_timer.tick(
            lambda: tick_asr(system), outputs=[asr_live, asr_status, stop_dictation]
        )  # pyright: ignore[reportAttributeAccessIssue]

    return AppUi(blocks=demo, transcript=transcript)
