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
- ``main()`` — parse ``--config``, build, then ``_serve``: start + launch inside a ``try`` whose
  ``finally`` runs ``_shutdown`` (isolated, time-bounded steps; the bridge is stopped before the api
  exits).

Two objects hold the running system, split by dependency so both can be *fully formed at
construction* (no post-hoc mutation):

- ``PanelState`` ([app_ui.py](app_ui.py)) — the instrumentation sink + the panel view-state. It depends
  on nothing (no WICA), so it is built first and handed to ``Wica.init`` as the ``output_sink`` and
  subscribed to the agent's Events.
- ``AppSystem`` — a frozen snapshot of the resolved components (loop, wica, bridge, voice, panels) and
  the typed-text ``submit_speech`` hatch. Built last, once everything it holds exists.

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
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from asr_engine.engine import ASREngine
from reachy_mini_bridge.api import ReachyMiniApi
from reachy_mini_bridge.audio import TTSEngineSynthesizer
from wica import Content, TextPart, Wica
from wica.config import ConfigError, MissingEnvError

from reachy_mini_interaction_wica.app_ui import PanelState, build_ui
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource
from reachy_mini_interaction_wica.config import AppConfig, load_config
from reachy_mini_interaction_wica.robot_control import RobotControl
from reachy_mini_interaction_wica.tts_output import VoiceOutput

logger = logging.getLogger(__name__)


def _speech_input(value: Any, previous: Any) -> Content:
    """Render the triggering `speech_input` entry into the prompt (voice-assistant phrasing).

    An empty string — an ASR final that closed on silence, delivered verbatim — is a real value,
    rendered as ``said to you: ""``; only ``None`` means the person hasn't spoken yet."""
    if value is None:
        return [TextPart("The person hasn't said anything to you yet.")]
    return [TextPart(f'The person said to you: "{value}"')]


@dataclass(frozen=True)
class AppSystem:
    """A frozen snapshot of the built system: the resolved components on one app-owned loop, the panel
    state, and the typed-text ``submit_speech`` hatch.

    Every field is final at construction — the two-phase ``system.wica = …`` back-patch is gone because
    ``build_system`` builds WICA before the bridge (see the module docstring). ``api`` / ``wica`` /
    ``bridge`` / ``voice`` are ``None`` when that component failed to build (its reason is captured in
    ``missing``); the UI still opens and the surviving paths still work — notably typed text, which
    reaches the agent whenever ``wica`` is running, needing neither ASR nor the robot. ``build_system``
    enters the robot's media session (so ``say``/mic are live), but does not *start* the agent or ASR
    capture (``main`` does), so a test can inspect a built-but-not-listening system. ``main`` is what
    tears down via ``_shutdown`` (agent, then ASR bridge, then the api's ``__aexit__``, then the loop).
    The live views the UI polls (``tick_world`` / ``tick_asr``) are free functions in
    [app_ui.py](app_ui.py) that read this snapshot.
    """

    loop: asyncio.AbstractEventLoop | None
    api: ReachyMiniApi | None
    wica: Wica | None
    bridge: AsrBridge | None
    voice: VoiceOutput | None
    panels: PanelState
    missing: list[str]
    # The typed-text hatch: writes the same `speech_input` World update a closed ASR segment makes (so
    # both flow through the identical reason → say → sink path), but ignores empty/whitespace input —
    # a guard ASR finals deliberately bypass. Built in build_system.
    submit_speech: Callable[[str], None]


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
    media session opens), because both ``say`` and the mic stream ride it. The ``TTSEngineSynthesizer``
    is built first, in its own guard (an unset TTS key appends a ``TTS:`` entry and the robot is built
    with no synthesizer, so only ``say`` degrades); a ``real``/``sim`` backend can raise at
    **construction** (``build_robot`` connects) and/or at ``__aenter__``, and the failure is an upstream
    ``reachy_mini`` exception (not a bridge type), so both are wrapped in one broad ``except`` that
    appends to ``missing``. Each remaining build that can raise on an unset key
    (``Wica.init`` → ``MissingEnvError``, any other ``ConfigError`` such as an unreadable
    ``system_prompt_file``, or ``ImportError`` for a missing provider extra; ``ASREngine`` →
    ``ValueError``) is likewise caught into ``missing`` rather than crashing, so the returned system
    opens even with credentials or a robot absent. ``api`` / ``asr_engine`` inject fakes for tests,
    bypassing the real build.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="reachy-mini-interaction-loop", daemon=True).start()

    panels = PanelState()
    missing: list[str] = []

    # --- The voice's synthesizer: built on its own so an unset TTS key degrades only `say` ------
    # TTSEngineSynthesizer resolves the TTS module's key at construction and raises when it is
    # unset. That must not take the robot down with it (the mic, ASR, and actions need no TTS key),
    # so it is guarded separately and the robot is built with `synthesizer=None` on failure — the
    # bridge accepts that and `api.say` then raises a BridgeError, which VoiceOutput maps to a
    # readable "TTS error: …". Skipped when a test injects `api=` (the fake carries its own say).
    synthesizer: TTSEngineSynthesizer | None = None
    if api is None:
        try:
            synthesizer = TTSEngineSynthesizer(config.tts_engine)
        except Exception as exc:  # noqa: BLE001 - an unset/invalid TTS key degrades, it never crashes
            missing.append(f"TTS: {exc}")

    # --- The robot: one ReachyMiniApi behind say + the mic stream, entered on the app loop ------
    # Construction connects a real/sim robot and __aenter__ opens the media session; both can raise an
    # upstream reachy_mini exception, so wrap both in one broad except and degrade (api stays None).
    robot: ReachyMiniApi | None = None
    try:
        robot = (
            api if api is not None else ReachyMiniApi(config.reachy.backend, synthesizer=synthesizer)
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
    except ConfigError as exc:
        # After MissingEnvError (its subclass). Wica.init reads `system_prompt_file` and raises this
        # when the file is missing/unreadable — a config typo degrades to the banner, not a traceback.
        missing.append(f"Agent: {exc}")
    except ImportError as exc:
        # The configured provider's extra isn't installed (a setup gap, not a missing key). Surface
        # it in the banner rather than crashing, so the app still opens and says what to install.
        missing.append(f"Agent: {exc}")

    # The two input paths into `speech_input`. `wica` is already final here (built above), so plain
    # closures capture it. world.update is guarded on a running agent (it raises while stopped, and
    # input arriving before the agent is up — or after teardown — is dropped).
    def _deliver_speech(text: str) -> None:
        # The ASR-final path: every closed segment reaches the agent verbatim, including an
        # empty/whitespace one (a passage that closed on silence), so the agent can react to the user
        # saying nothing — the react-to-silence contract in specs/asr-bridge.md.
        if wica is not None and wica.is_running:
            wica.world.update("speech_input", text)

    def submit_speech(text: str) -> None:
        # The typed-text path: an empty typed line has nothing to reply to, so it is inert. This guard
        # is the typed hatch's alone — it must not gate ASR finals.
        text = text.strip()
        if text:
            _deliver_speech(text)

    # --- Speech in: the ASR bridge, or None with a captured reason. Built after the agent, so its
    # on_final_segment callback (_deliver_speech) resolves forward with no placeholder. The engine is
    # fed the robot's echo-cancelled mic (RobotMicSource), so speech-in also needs the robot: with no
    # api there is no mic, and the bridge stays None (typed text still drives the agent). -----------
    bridge: AsrBridge | None = None
    if asr_engine is not None:
        bridge = AsrBridge(asr_engine, on_final_segment=_deliver_speech)
    elif robot is not None:
        try:
            mic = RobotMicSource(robot)
            asr = ASREngine(config.asr_engine, audio_source=mic)
            bridge = AsrBridge(asr, on_final_segment=_deliver_speech, mic=mic)
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


# Per-step bound on each teardown coroutine (same value as vocal-interaction-wica's _STOP_TIMEOUT_S),
# so a hung provider connection or robot session cannot wedge exit.
_SHUTDOWN_TIMEOUT_S = 5.0


def _run_on_loop(
    loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], *, step: str
) -> None:
    """Run one teardown coroutine on *loop*, bounded by ``_SHUTDOWN_TIMEOUT_S``; log and swallow any
    failure (or timeout — the coroutine is then cancelled) so the next teardown step still runs."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        future.result(_SHUTDOWN_TIMEOUT_S)
    except Exception:  # one teardown step must not abort the rest (a timeout lands here too)
        future.cancel()
        logger.exception("teardown step %r failed", step)


def _shutdown(system: AppSystem) -> None:
    """Tear the system down in dependency order: the agent, then ASR capture, then the robot's
    media session, then the loop. The bridge is stopped *before* the api exits so the mic pump is
    cancelled while its stream is still valid, rather than iterating a closed media session.

    Each step is isolated (and each loop hop time-bounded): a step that raises or hangs is logged and
    the rest still run, so one failure cannot strand the robot session or the loop thread."""
    loop = system.loop
    if system.wica is not None:
        try:
            system.wica.close()
        except Exception:  # one teardown step must not abort the rest
            logger.exception("teardown step 'wica.close' failed")
    # Only a running bridge is stopped — one can be built but never started (no agent ⇒ _start
    # skipped it), and this keeps teardown off ASREngine.stop()'s behavior on a never-started engine.
    bridge = system.bridge
    if bridge is not None and loop is not None and bridge.status()["running"]:
        _run_on_loop(loop, bridge.stop(), step="asr.stop")
    if system.api is not None and loop is not None:
        # Close the media session + robot connection on the loop before the loop stops.
        _run_on_loop(loop, system.api.__aexit__(None, None, None), step="api.exit")
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)


def _serve(system: AppSystem) -> None:
    """Start the system and run the UI, tearing down whatever happens. ``_start`` sits *inside* the
    ``try``: ``build_system`` has already entered the robot's media session, so a raise in
    ``wica.start()`` / ``asr_bridge.start()`` must still exit it and stop the loop."""
    try:
        _start(system)
        build_ui(system).launch()
    finally:
        _shutdown(system)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("wica").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description="Talk to a WICA-driven Reachy Mini.")
    # Required, with no default: the committed config.example.json targets a *real* robot, so a bare
    # run must not silently pick it up — argparse exits with usage guidance instead.
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the composed app config JSON (copy config.example.json to start).",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ValueError as exc:
        # A malformed/missing config still opens the UI with an explanation rather than a traceback.
        system = _degraded_system(str(exc))
    else:
        system = build_system(config)
    _serve(system)


if __name__ == "__main__":
    main()
