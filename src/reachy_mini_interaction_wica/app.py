"""The runnable reachy-mini interaction application: wiring + lifecycle.

Stands up the robot and the ASR engine behind WICA on one app-owned event loop — [AsrBridge](asr_bridge.py)
in (fed the robot's echo-cancelled mic via ``RobotMicSource``), the WICA Agent in the middle,
[VoiceOutput](tts_output.py) out (spoken through the robot speaker). Speech-out and the mic stream both
ride a single ``reachy-mini-bridge`` ``ReachyMiniApi``, so there is one robot connection and one media
session. The Gradio presentation layer (panels, layout, live views) lives in [app_ui.py](app_ui.py).
See specs/app.md.

Three seams keep the wiring testable without a browser:

- ``build_system(config)`` — pure wiring, no Gradio: owns the loop, builds the one ``ReachyMiniApi``
  (and enters it on the loop), builds/adopts the engines behind WICA, registers commands +
  ``speech_input``, wires the bridge callback, collects panel state. Its ``api=`` / ``asr_engine=``
  kwargs are test seams for injecting fakes.
- ``build_ui(system)`` — the Gradio layout only (in [app_ui.py](app_ui.py)).
- ``main()`` — parse ``--config``, build, start, launch, and ``__aexit__`` the api at teardown.

Two objects hold the running system, split by dependency so both can be *fully formed at
construction* (no post-hoc mutation):

- ``PanelState`` ([app_ui.py](app_ui.py)) — the instrumentation sink + the panel view-state. It depends
  on nothing (no WICA), so it is built first and handed to ``Wica.init`` as the ``output_sink`` and
  subscribed to the agent's Events.
- ``AppSystem`` — a frozen snapshot of the resolved components (loop, wica, bridge, voice, panels) and
  the shared ``submit_speech`` funnel. Built last, once everything it holds exists.

The build order — panels → voice → **wica → bridge** — is deliberate: WICA needs only the panels and
the voice, and the bridge's ``on_final_segment`` needs WICA, so building WICA *before* the bridge lets
every reference resolve forward with no placeholder/back-patching.

WICA exposes no accessor for the loop it runs on, so the app *owns* the loop and injects it into
``Wica.init(loop=…)`` — the only way to schedule the ASR engine and the robot api onto the same loop
the Agent runs on. Each build that can raise on an unset key is caught so the app opens even with
credentials missing and says what to set.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asr_engine.engine import ASREngine
from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.audio import TTSEngineSynthesizer
from wica import Content, TextPart, Wica
from wica.config import MissingEnvError

from reachy_mini_interaction_wica.app_ui import PanelState, build_ui
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource
from reachy_mini_interaction_wica.config import AppConfig, load_config
from reachy_mini_interaction_wica.robot_control import RobotControl
from reachy_mini_interaction_wica.tts_output import VoiceOutput

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = "config.example.json"


def _speech_input(value: Any, previous: Any) -> Content:
    """Render the triggering `speech_input` entry into the prompt (voice-assistant phrasing)."""
    if value is None:
        return [TextPart("The person hasn't said anything to you yet.")]
    return [TextPart(f'The person said to you: "{value}"')]


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
    ``__aexit__``s the api at teardown. The live views the UI polls (``tick_world`` / ``tick_asr``) are
    free functions in [app_ui.py](app_ui.py) that read this snapshot.
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


def build_system(
    config: AppConfig,
    *,
    api: ReachyMiniApi | None = None,
    asr_engine: ASREngine | None = None,
) -> AppSystem:
    """Stand up the whole system from *config* — the wiring `main` does, minus starting/launching.

    Owns a fresh event loop (run in a daemon thread) and injects it into ``Wica.init`` so the ASR
    engine and the robot api share the loop the Agent runs on. Builds in dependency order — robot api →
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
        # Robot action commands, over the same api as the voice: emotions, head tracking, motors. Only
        # when the robot is up (no api ⇒ nothing to act through); the agent still speaks/reasons without.
        if robot is not None:
            controls = RobotControl(robot)
            wica.register_command(controls.list_emotions)
            wica.register_command(controls.play_emotion)
            wica.register_command(controls.start_head_tracking)
            wica.register_command(controls.stop_head_tracking)
            wica.register_command(controls.get_motors_state)
            wica.register_command(controls.set_motors_state)
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
