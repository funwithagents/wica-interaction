"""The runnable reachy-mini interaction application: Gradio UI + wiring.

Stands up the robot and both engines behind WICA on one app-owned event loop — [AsrBridge](asr_bridge.py)
in (fed the robot's echo-cancelled mic via ``RobotMicSource``), the WICA Agent in the middle,
[VoiceOutput](tts_output.py) out (spoken through the robot speaker) — and presents the demo's
observability panels (Conversation, plus World/Prompt behind a debug toggle) alongside a live ASR
panel with a typed-text hatch. Speech-out and the mic stream both ride a single
``reachy-mini-bridge`` ``ReachyMiniApi``, so there is one robot connection and one media session. See
specs/app.md.

Three seams keep the wiring testable without a browser:

- ``build_system(config)`` — pure wiring, no Gradio: owns the loop, builds the one ``ReachyMiniApi``
  (and enters it on the loop), builds/adopts the engines behind WICA, registers commands +
  ``speech_input``, wires the bridge callback, collects panel state. Its ``api=`` / ``asr_engine=``
  kwargs are test seams for injecting fakes.
- ``build_ui(system)`` — the Gradio layout only.
- ``main()`` — parse ``--config``, build, start, launch, and ``__aexit__`` the api at teardown.

Two objects hold the running system, split by dependency so both can be *fully formed at
construction* (no post-hoc mutation):

- ``PanelState`` — the instrumentation sink + the panel view-state. It depends on nothing (no WICA),
  so it is built first and handed to ``Wica.init`` as the ``output_sink`` and subscribed to the
  agent's Events.
- ``AppSystem`` — a frozen snapshot of the resolved components (loop, wica, bridge, voice, panels) and
  the shared ``submit_speech`` funnel. Built last, once everything it holds exists.

The build order — panels → voice → **wica → bridge** — is deliberate: WICA needs only the panels and
the voice, and the bridge's ``on_final_segment`` needs WICA, so building WICA *before* the bridge lets
every reference resolve forward with no placeholder/back-patching.

WICA exposes no accessor for the loop it runs on, so the app *owns* the loop and injects it into
``Wica.init(loop=…)`` — the only way to schedule the ASR/TTS coroutines onto the same loop the Agent
runs on. Each engine build that can raise on an unset key is caught so the app opens even with
credentials missing and says what to set.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import gradio as gr
from asr_engine.engine import ASREngine
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.audio import TTSEngineSynthesizer
from wica import CommandIssued, Content, TextPart, Wica, WorldEntry
from wica.agent import CommandExecution
from wica.config import MissingEnvError

from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource
from reachy_mini_interaction_wica.config import AppConfig, load_config
from reachy_mini_interaction_wica.tts_output import VoiceOutput

logger = logging.getLogger(__name__)

# The output Command's name (VoiceOutput.say.__name__): what the model calls to speak, and what
# on_command reads to render the 🗣️ say bubble. WICA auto-registers a `noop` Command the model calls
# to declare "no reaction"; it fires on_command like any issued command so the panel can show it.
_OUTPUT_COMMAND_NAME = "say"
_NOOP_COMMAND_NAME = "noop"

_NO_PROMPT_YET = "(no prompt sent to the model yet)"
_NO_LIVE_SPEECH = "(no speech yet)"

_DEFAULT_CONFIG = "config.example.json"

_WORLD_TABLE_HEADERS = ["Key", "Value", "Updated"]
# Fixed column widths (percent, summing to 100) so the table never reflows when a long value lands.
_WORLD_COL_WIDTHS = [34, 44, 22]


def _speech_input(value: Any, previous: Any) -> Content:
    """Render the triggering `speech_input` entry into the prompt (voice-assistant phrasing)."""
    if value is None:
        return [TextPart("The person hasn't said anything to you yet.")]
    return [TextPart(f'The person said to you: "{value}"')]


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


@dataclass(frozen=True)
class AppSystem:
    """A frozen snapshot of the built system: the resolved components on one app-owned loop, the panel
    state, and the shared ``submit_speech`` funnel.

    Every field is final at construction — the two-phase ``system.wica = …`` back-patch is gone because
    ``build_system`` builds WICA before the bridge (see the module docstring). ``api`` / ``wica`` /
    ``bridge`` / ``voice`` are ``None`` when that component failed to build (its reason is captured in
    ``missing``); the UI still opens and the surviving paths still work — notably typed text, which
    reaches the agent whenever ``wica`` is running, needing neither ASR nor the robot. ``build_system``
    enters the robot's media session (so ``say``/mic are live), but does not *start* the agent or ASR
    capture (``main`` does), so a test can inspect a built-but-not-listening system. ``main`` is what
    ``__aexit__``s the api at teardown.
    """

    loop: asyncio.AbstractEventLoop | None
    api: ReachyMiniApi | None
    wica: Wica | None
    bridge: AsrBridge | None
    voice: VoiceOutput | None
    panels: PanelState
    missing: list[str]
    # The one input funnel: a closed ASR segment (bridge callback) and the typed-text box both call
    # it, so both flow through the identical reason → say → sink path. Built in build_system.
    submit_speech: Callable[[str], None]

    @property
    def can_reason(self) -> bool:
        """Whether the Agent was built (its key was present) — the gate on starting WICA."""
        return self.wica is not None

    @property
    def can_listen(self) -> bool:
        """Whether the ASR bridge was built — the gate on live microphone capture."""
        return self.bridge is not None

    def tick_world(self) -> str:
        """Refresh only the World table, on its own timer, reading live state directly (so a fleeting
        command entry — e.g. a `say` that is `running` only while it speaks — isn't lost)."""
        return self._world_html()

    def tick_asr(self) -> tuple[str, str, Any]:
        """Refresh the ASR panel: live interim text, a status line, and the Stop-dictation button's
        enabled state (interactive only while dictating)."""
        bridge = self.bridge
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

    # --- World table rendering (HTML, not gr.Dataframe: renders fleeting command rows reliably) --

    def _world_rows(self) -> list[list[str]]:
        wica = self.wica
        if wica is None:
            return []
        rows: list[list[str]] = []
        for entry in wica.world.get_prompt_entries():
            version = entry.current
            stamp = version.timestamp.astimezone().strftime("%H:%M:%S")
            rows.append([entry.key, _format_value(version.value), stamp])
        return rows

    def _world_html(self) -> str:
        cols = "".join(f'<col style="width:{w}%">' for w in _WORLD_COL_WIDTHS)
        header_cells = "".join(f"<th>{html.escape(h)}</th>" for h in _WORLD_TABLE_HEADERS)
        body_rows: list[str] = []
        for row in self._world_rows():
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


def build_system(
    config: AppConfig,
    *,
    api: ReachyMiniApi | None = None,
    asr_engine: ASREngine | None = None,
) -> AppSystem:
    """Stand up the whole system from *config* — the wiring `main` does, minus starting/launching.

    Owns a fresh event loop (run in a daemon thread) and injects it into ``Wica.init`` so the ASR
    engine and the robot share the loop the Agent runs on. Builds in dependency order — robot api →
    voice → wica → bridge — so every component is fully formed when the frozen ``AppSystem`` is
    assembled at the end. The one ``ReachyMiniApi`` is built and **entered on the loop** here (its
    media session opens), because both ``say`` and the mic stream ride it; a ``real``/``sim`` backend
    can raise at **construction** (``build_robot`` connects) and/or at ``__aenter__``, and the failure
    is an upstream ``reachy_mini`` exception (not a bridge type), so both are wrapped in one broad
    ``except`` that appends to ``missing``. Each remaining build that can raise on an unset key
    (``Wica.init`` → ``MissingEnvError`` / ``ImportError`` for a missing provider extra, ``ASREngine``
    → ``ValueError``) is likewise caught into ``missing`` rather than crashing, so the returned system
    opens even with credentials or a robot absent. ``api`` / ``asr_engine`` inject fakes for tests,
    bypassing the real build.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="reachy-mini-interaction-loop", daemon=True).start()

    panels = PanelState()
    missing: list[str] = []

    # --- The robot: one ReachyMiniApi behind say + the mic stream, entered on the app loop ------
    # Construction connects a real/sim robot and __aenter__ opens the media session; both can raise an
    # upstream reachy_mini exception, so wrap both in one broad except and degrade (api stays None).
    robot: ReachyMiniApi | None = None
    try:
        robot = (
            api
            if api is not None
            else ReachyMiniApi(
                config.reachy.backend, synthesizer=TTSEngineSynthesizer(config.tts_engine)
            )
        )
        asyncio.run_coroutine_threadsafe(robot.__aenter__(), loop).result()
    except Exception as exc:  # noqa: BLE001 - any construct/connect failure degrades, it never crashes
        missing.append(f"Robot: {exc}")
        robot = None

    # --- Speech out: the robot-backed voice, or None when the robot is unavailable --------------
    voice = VoiceOutput(robot) if robot is not None else None

    # --- The agent, on the app-owned loop (needs only the panels sink + the voice) --------------
    wica: Wica | None = None
    try:
        wica = Wica.init(
            config.wica,
            output_sink=panels.output_sink,
            output_command=voice.say if voice is not None else None,
            loop=loop,
        )
    except MissingEnvError as exc:
        missing.append(f"Agent: environment variable {exc.env_var!r} is not set")
    except ImportError as exc:
        # The configured provider's extra isn't installed (a setup gap, not a missing key). Surface
        # it in the banner rather than crashing, so the app still opens and says what to install.
        missing.append(f"Agent: {exc}")

    # The one input funnel — a closed ASR segment (bridge callback) and the typed-text box both call
    # it. `wica` is already final here (built above), so a plain closure captures it: empty/whitespace
    # input is ignored (nothing to reply to), and world.update is guarded on a running agent (it raises
    # while stopped, and a segment arriving before the agent is up — or after teardown — is dropped).
    def submit_speech(text: str) -> None:
        text = text.strip()
        if not text:
            return
        if wica is not None and wica.is_running:
            wica.world.update("speech_input", text)

    # --- Speech in: the ASR bridge, or None with a captured reason. Built after the agent, so its
    # on_final_segment callback (submit_speech) resolves forward with no placeholder. The engine is
    # fed the robot's echo-cancelled mic (RobotMicSource), so speech-in also needs the robot: with no
    # api there is no mic, and the bridge stays None (typed text still drives the agent). -----------
    bridge: AsrBridge | None = None
    if asr_engine is not None:
        bridge = AsrBridge(asr_engine, on_final_segment=submit_speech)
    elif robot is not None:
        try:
            asr = ASREngine(config.asr_engine, audio_source=RobotMicSource(robot))
            bridge = AsrBridge(asr, on_final_segment=submit_speech)
        except ValueError as exc:
            missing.append(f"ASR: {exc}")

    # --- Wire the agent to the World, the commands, and the panels ------------------------------
    if wica is not None:
        wica.world.register("speech_input", str, serialize_fn=_speech_input, triggers_llm_call=True)
        if bridge is not None:
            wica.register_command(bridge.start_dictation)
            wica.register_command(bridge.stop_dictation)
        wica.on_agent_prompt.subscribe(panels.on_prompt)
        wica.on_agent_trigger.subscribe(panels.on_trigger)
        wica.on_agent_command.subscribe(panels.on_command)

    return AppSystem(
        loop=loop,
        api=robot,
        wica=wica,
        bridge=bridge,
        voice=voice,
        panels=panels,
        missing=missing,
        submit_speech=submit_speech,
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
                "own key is set, even with no robot (speech-out and robot actions will report the robot "
                "as missing). For offline development, set `reachy.backend` to `\"fake\"` or `\"sim\"`."
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
                    world_view = gr.HTML(value=system._world_html())
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
        world_timer.tick(system.tick_world, outputs=world_view)  # pyright: ignore[reportAttributeAccessIssue]
        asr_timer = gr.Timer(0.2)
        asr_timer.tick(system.tick_asr, outputs=[asr_live, asr_status, stop_dictation])  # pyright: ignore[reportAttributeAccessIssue]

    return demo


def _start(system: AppSystem) -> None:
    """Start the agent (if built), then the ASR bridge (only if the agent is up to consume speech)."""
    if system.wica is not None:
        system.wica.start()
        if system.bridge is not None and system.loop is not None:
            asyncio.run_coroutine_threadsafe(system.bridge.start(), system.loop).result()


def _degraded_system(message: str) -> AppSystem:
    """A do-nothing system that only carries a banner message (e.g. a config that failed to load)."""
    return AppSystem(
        loop=None,
        api=None,
        wica=None,
        bridge=None,
        voice=None,
        panels=PanelState(),
        missing=[message],
        submit_speech=lambda _text: None,
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("wica").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description="Talk to a WICA-driven Reachy Mini.")
    parser.add_argument(
        "--config", default=_DEFAULT_CONFIG, help="Path to the composed app config JSON."
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ValueError as exc:
        # A malformed/missing config still opens the UI with an explanation rather than a traceback.
        system = _degraded_system(str(exc))
    else:
        system = build_system(config)
        _start(system)

    try:
        build_ui(system).launch()
    finally:
        if system.wica is not None:
            system.wica.close()
        if system.api is not None and system.loop is not None:
            # Close the media session + robot connection on the loop before the loop stops.
            asyncio.run_coroutine_threadsafe(system.api.__aexit__(None, None, None), system.loop).result()
        if system.loop is not None:
            system.loop.call_soon_threadsafe(system.loop.stop)


if __name__ == "__main__":
    main()
