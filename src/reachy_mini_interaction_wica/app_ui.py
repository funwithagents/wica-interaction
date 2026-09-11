"""The Gradio UI: panel view-state, instrumentation sink, and the browser layout.

This is the *presentation* half of the app, split from the wiring in [app.py](app.py). It holds
everything the browser needs and nothing that stands up the robot/engines:

- ``PanelState`` — the instrumentation sink + panel view-state handed to ``Wica.init`` as the
  ``output_sink`` and subscribed to the agent's Events. It depends on nothing (no WICA), so ``app`` can
  build it before the agent.
- ``build_ui(system)`` — the Gradio layout: Conversation + ASR as the main surface, World + Prompt
  behind a debug toggle, a banner for any missing credentials, and the typed-text hatch.
- the live-view renderers the UI timers poll — ``tick_world`` / ``tick_asr`` — plus the message/value
  rendering helpers. These read a built ``AppSystem`` but belong to the view, so they live here as free
  functions rather than methods on the frozen wiring snapshot.

The only dependency on ``app`` is the ``AppSystem`` *type* (for annotations), imported under
``TYPE_CHECKING`` so there is no import cycle: ``app`` imports ``PanelState`` / ``build_ui`` from here at
runtime, and this module never imports ``app`` at runtime. See specs/app.md.
"""

from __future__ import annotations

import asyncio
import html
import queue
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

import gradio as gr
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from wica import CommandIssued, WorldEntry
from wica.agent import CommandExecution

if TYPE_CHECKING:
    from reachy_mini_interaction_wica.app import AppSystem

# The output Command's name (VoiceOutput.say.__name__): what the model calls to speak, and what
# on_command reads to render the 🗣️ say bubble. WICA auto-registers a `noop` Command the model calls
# to declare "no reaction"; it fires on_command like any issued command so the panel can show it.
_OUTPUT_COMMAND_NAME = "say"
_NOOP_COMMAND_NAME = "noop"

_NO_PROMPT_YET = "(no prompt sent to the model yet)"
_NO_LIVE_SPEECH = "(no speech yet)"

_WORLD_TABLE_HEADERS = ["Key", "Value", "Updated"]
# Fixed column widths (percent, summing to 100) so the table never reflows when a long value lands.
_WORLD_COL_WIDTHS = [34, 44, 22]


def _describe_trigger(entry: WorldEntry) -> str:
    key = entry.key
    value = entry.current.value
    if key == "speech_input":
        return f'🗣️ "{value}"'
    if key.startswith("agent:command:"):
        name = value.name if isinstance(value, CommandExecution) else key
        return f"⚡ command finished: {name}"
    return f"⚡ {key} = {value!r}"


class PanelState:
    """The instrumentation sink and the panel view-state — everything the UI shows, and nothing that
    depends on WICA.

    Built before the agent so its ``output_sink`` / ``on_trigger`` / ``on_command`` / ``on_prompt``
    can be handed to ``Wica.init`` and subscribed to the agent's Events. Instrumentation Events fire
    on the agent loop and push chat events onto one thread-safe queue that a ``gr.Timer`` drains, so
    the Gradio side stays synchronous; a single ordered queue keeps triggers, spoken replies, and
    command calls interleaved in the order they happened.
    """

    def __init__(self) -> None:
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._state_lock = threading.Lock()
        self._conversation: list[dict[str, Any]] = []
        # Every step's prompt, append-only, each {"label", "text"}; never mutated in place.
        self._prompts: list[dict[str, str]] = []
        self._last_trigger_label = "start"
        self._last_shown_count = 0
        self._last_conv_sig: tuple[Any, ...] | None = None
        # Reaction grouping: each step is one collapsible group; on_prompt opens it and the step's
        # outputs nest under it via metadata.parent_id. All on the agent loop, so no lock needed.
        self._reaction_count = 0
        self._current_reaction_id: str | None = None

    # --- Instrumentation handlers (subscribed to WICA's Events) --------------------------------

    def _reaction_child(self, title: str, content: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"title": title}
        if self._current_reaction_id is not None:
            metadata["parent_id"] = self._current_reaction_id
        return {"role": "assistant", "content": content, "metadata": metadata}

    async def output_sink(self, text: str) -> None:
        """The agent's free text for a step — private reasoning, since `say` is the voice. Labelled
        so the panel makes plain which channel produced it, set apart from the spoken reply."""
        if text.strip():
            self._events.put(self._reaction_child("💭 output sink", text))

    def on_trigger(self, entry: WorldEntry) -> None:
        """A World entry triggered a step — show it on the input (right) side, and always record the
        label so on_prompt can tag this step's prompt. The agent's own command-completion re-triggers
        are labelled but not echoed on the transcript (they already show as command calls)."""
        label = _describe_trigger(entry)
        self._last_trigger_label = label
        if entry.key.startswith("agent:command:"):
            return
        self._events.put({"role": "user", "content": label})

    def on_command(self, command: CommandIssued) -> None:
        """A command the model issued — shown on the assistant (left) side. `say` renders as the
        spoken reply (🗣️, its `text` arg is what the user heard); `noop` is the agent choosing not to
        react; every other command (start_dictation, …) is a 🦾 action."""
        if command.name == _OUTPUT_COMMAND_NAME:
            spoken = command.args.get("text", "")
            self._events.put(self._reaction_child("🗣️ say", str(spoken)))
            return
        if command.name == _NOOP_COMMAND_NAME:
            self._events.put(self._reaction_child("🚫 noop", "chose not to react"))
            return
        rendered = ", ".join(f"{k}={v!r}" for k, v in command.args.items())
        self._events.put(self._reaction_child(f"🦾 {command.name}", f"{command.name}({rendered})"))

    def on_prompt(self, messages: list[BaseMessage]) -> None:
        """Capture the exact messages sent to the model (labelled by time + trigger) and open this
        step's reaction group so its outputs nest under one header. Once per step, on the agent loop,
        before the model call — so it precedes the step's say/output-sink/command events."""
        rendered = "\n\n".join(_render_message(m) for m in messages)
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        trigger_label = self._last_trigger_label
        with self._state_lock:
            self._prompts.append({"label": f"{stamp} — {trigger_label}", "text": rendered})

        self._reaction_count += 1
        self._current_reaction_id = f"reaction-{self._reaction_count}"
        self._events.put(
            {
                "role": "assistant",
                "content": "",
                "metadata": {
                    "id": self._current_reaction_id,
                    "title": f"💬 reaction {self._reaction_count} · {trigger_label}",
                },
            }
        )

    # --- Timer handlers the panels poll (panel state only; no WICA) -----------------------------

    def tick_conversation(self) -> tuple[Any, Any, Any]:
        """Drain queued chat events into the transcript and snap the prompt view to the newest step.
        Push a new chatbot value only when the transcript actually changed, so gr.Chatbot's autoscroll
        isn't re-triggered every fire (which would fight a user scrolling up)."""
        with self._state_lock:
            while True:
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    break
                self._conversation.append(event)
            conversation = list(self._conversation)
            conv_sig = tuple(str(m.get("content", "")) for m in conversation)
            count = len(self._prompts)
            choices = [(p["label"], i) for i, p in enumerate(self._prompts)]
            newest_text = self._prompts[-1]["text"] if self._prompts else None

        if conv_sig != self._last_conv_sig:
            self._last_conv_sig = conv_sig
            chatbot_update: Any = conversation
        else:
            chatbot_update = gr.update()

        if count > self._last_shown_count:
            self._last_shown_count = count
            selector_update = gr.update(choices=choices, value=count - 1)
            prompt_update = gr.update(value=newest_text)
        else:
            selector_update = gr.update()
            prompt_update = gr.update()
        return chatbot_update, selector_update, prompt_update

    def on_select_prompt(self, index: int | None) -> Any:
        """User picked a prompt from the dropdown — show its exact text."""
        if index is None:
            return gr.update()
        with self._state_lock:
            if 0 <= index < len(self._prompts):
                return self._prompts[index]["text"]
        return gr.update()


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, CommandExecution):
        args = ", ".join(f"{k}={v!r}" for k, v in value.args.items())
        return f"{value.name}({args}) [{value.state}]"
    return str(value)


def _flatten_content(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image":
                parts.append(f"[image {block.get('mime_type', '')}]")
            else:
                parts.append(str(block))
        else:
            parts.append(str(block))
    return "".join(parts)


def _render_message(m: BaseMessage) -> str:
    """Render one LangChain message to text for the prompt panel, making explicit the tool_calls an
    assistant message carries in its structured field (its .content is otherwise blank)."""
    lines = [f"### {m.type.upper()}"]
    if isinstance(m, ToolMessage):
        lines.append(f"🔧 result for [id={m.tool_call_id}] => {_flatten_content(m.content)}")
        return "\n".join(lines)
    body = _flatten_content(m.content)
    if body:
        lines.append(body)
    if isinstance(m, AIMessage):
        for call in m.tool_calls:
            args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
            lines.append(f"🔧 tool_call {call['name']}({args})  [id={call['id']}]")
    return "\n".join(lines)


# --- Live views the UI timers poll (read a built AppSystem; belong to the view) -----------------


def tick_world(system: AppSystem) -> str:
    """Refresh only the World table, on its own timer, reading live state directly (so a fleeting
    command entry — e.g. a `say` that is `running` only while it speaks — isn't lost)."""
    return _world_html(system)


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
    return display, status_md, gr.update(interactive=bool(status["dictating"]))


# --- World table rendering (HTML, not gr.Dataframe: renders fleeting command rows reliably) -----


def _world_rows(system: AppSystem) -> list[list[str]]:
    wica = system.wica
    if wica is None:
        return []
    rows: list[list[str]] = []
    for entry in wica.world.get_prompt_entries():
        version = entry.current
        stamp = version.timestamp.astimezone().strftime("%H:%M:%S")
        rows.append([entry.key, _format_value(version.value), stamp])
    return rows


def _world_html(system: AppSystem) -> str:
    cols = "".join(f'<col style="width:{w}%">' for w in _WORLD_COL_WIDTHS)
    header_cells = "".join(f"<th>{html.escape(h)}</th>" for h in _WORLD_TABLE_HEADERS)
    body_rows: list[str] = []
    for row in _world_rows(system):
        is_command = row[0].startswith("agent:command:")
        cells = "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
        cls = ' class="cmd"' if is_command else ""
        body_rows.append(f"<tr{cls}>{cells}</tr>")
    return (
        "<div class='world-table'><style>"
        ".world-table table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:13px}"
        ".world-table th,.world-table td{border:1px solid var(--border-color-primary,#ccc);"
        "padding:4px 8px;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere;word-break:break-word}"
        ".world-table th{font-weight:600}"
        ".world-table tr.cmd td{background:var(--color-accent-soft,#fff4e5)}"
        f"</style><table><colgroup>{cols}</colgroup><thead><tr>"
        f"{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"
    )


def build_ui(system: AppSystem) -> gr.Blocks:
    """The Gradio layout: Conversation + ASR as the main surface, World + Prompt behind a debug
    toggle, a banner for any missing credentials, and the typed-text hatch into `speech_input`."""
    loop = system.loop
    panels = system.panels

    def on_send(user_text: str) -> str:
        system.submit_speech(user_text)  # empty/whitespace is ignored inside
        return ""  # clear the box; the transcript updates via the timer

    def on_stop_dictation() -> None:
        bridge = system.bridge
        if bridge is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(bridge.stop_dictation(), loop)

    def on_toggle_debug(show: bool) -> Any:
        return gr.update(visible=show)

    with gr.Blocks(title="Reachy Mini Interaction WICA") as demo:
        gr.Markdown("# Talk to a WICA-driven Reachy Mini")
        if system.missing:
            items = "\n".join(f"> - {html.escape(m)}" for m in system.missing)
            gr.Markdown(
                "> ⚠️ **Some parts could not start — set the missing variables (or connect the robot) "
                "and restart.**\n"
                f"{items}\n>\n> The app still opens; you can type below to drive the agent wherever its "
                "own key is set. With no robot, the `say` voice and the robot actions are not registered "
                "(the agent reasons but cannot speak or move); with no TTS key, `say` exists but reports "
                "a TTS error. For offline development, set `reachy.backend` to `\"fake\"` or `\"sim\"`."
            )
        gr.Markdown(
            "Speak (or type) a turn; the assistant reasons over the World and replies with its **🗣️ "
            "say** voice. It handles **one reasoning call at a time** (v1): input arriving mid-step is "
            "dropped, not queued. On the left, each step is one collapsible **💬 reaction**; inside it, "
            "items are labelled by channel — **🗣️ say** (what you heard), **💭 output sink** (private "
            "free text), **🦾** commands, **🚫 noop**."
        )

        with gr.Row():
            with gr.Column(scale=3):
                # gradio 6 renders the messages format (role/content/metadata dicts) natively — the
                # only format now — so no `type=` is needed. Its dynamic event methods (.click/.tick/…)
                # aren't in the stubs, hence the narrow ignores below.
                chatbot = gr.Chatbot(label="Conversation", height=420)
                with gr.Row():
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
                with gr.Column(visible=False) as debug_panels:
                    gr.Markdown("### World state (live)")
                    world_view = gr.HTML(value=_world_html(system))
                    prompt_selector = gr.Dropdown(
                        label="Prompt sent to the model (newest shown automatically)",
                        choices=[],
                        interactive=True,
                    )
                    prompt_view = gr.Textbox(
                        show_label=False, lines=16, interactive=False, value=_NO_PROMPT_YET
                    )

        send.click(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        msg.submit(on_send, inputs=msg, outputs=msg)  # pyright: ignore[reportAttributeAccessIssue]
        stop_dictation.click(on_stop_dictation)  # pyright: ignore[reportAttributeAccessIssue]
        show_debug.change(on_toggle_debug, inputs=show_debug, outputs=debug_panels)  # pyright: ignore[reportAttributeAccessIssue]
        prompt_selector.change(panels.on_select_prompt, inputs=prompt_selector, outputs=prompt_view)  # pyright: ignore[reportAttributeAccessIssue]

        # Independent timers so the panels don't share one round-trip (a growing transcript payload
        # must not bottleneck the live World/ASR views).
        conv_timer = gr.Timer(0.2)
        conv_timer.tick(  # pyright: ignore[reportAttributeAccessIssue]
            panels.tick_conversation, outputs=[chatbot, prompt_selector, prompt_view]
        )
        world_timer = gr.Timer(0.2)
        world_timer.tick(lambda: tick_world(system), outputs=world_view)  # pyright: ignore[reportAttributeAccessIssue]
        asr_timer = gr.Timer(0.2)
        asr_timer.tick(lambda: tick_asr(system), outputs=[asr_live, asr_status, stop_dictation])  # pyright: ignore[reportAttributeAccessIssue]

    return demo
