"""The runnable reachy-mini interaction application: wiring + lifecycle.

Stands up the robot and the ASR engine behind WICA on one app-owned event loop — [AsrBridge](asr_bridge.py)
in (fed the robot's echo-cancelled mic via ``RobotMicSource``), the WICA Agent in the middle,
[VoiceOutput](tts_output.py) out (spoken through the robot speaker). Speech-out and the mic stream both
ride a single ``reachy-mini-bridge`` ``ReachyMiniApi``, so there is one robot connection and one media
session. The Gradio presentation layer (the page, the presenters it owns, the ASR live view) lives in
[app_ui.py](app_ui.py). See specs/app.md.

The composition follows WICA's conversation demo — **build → UI → wire → start → launch → close** —
which is the order the framework's output-wiring setters exist for (``Wica.init`` takes no
application callables; the sink and the output Command are set afterwards):

- ``build_system(config)`` — pure build, no Gradio, no wiring: owns the loop, builds the one
  ``ReachyMiniApi`` (which builds its own voice from ``config.reachy.tts``) and enters it on the loop,
  ``Wica.init``s the agent and registers the ``speech_input`` entry, builds the ASR bridge. Its
  ``api=`` / ``asr_engine=`` kwargs are test seams for injecting fakes.
- ``build_ui(system)`` — the Gradio page (in [app_ui.py](app_ui.py)); it owns the contrib presenters
  (``TranscriptLog`` / ``PromptLog``, subscribed to the agent's Events in their constructors) and hands
  the transcript back on an ``AppUi`` handle.
- ``wire(system, transcript)`` — Gradio-free: sets the transcript's ``output_sink`` and the voice's
  ``say`` as the output Command on the agent, registers the dictation + robot Commands. Tests call it
  with a ``TranscriptLog`` they build themselves, playing the UI's role.
- ``main()`` — parse ``--config``, build, build the UI, wire, then ``_serve``: start + launch inside a
  ``try`` whose ``finally`` runs ``_shutdown`` (isolated, time-bounded steps; the bridge is stopped
  before the api exits).

``AppSystem`` is a frozen snapshot of the resolved components (loop, api, wica, bridge, voice) and the
typed-text ``submit_speech`` hatch — fully formed at construction, nothing back-patched. The build
order — robot → voice → **wica → bridge** — is deliberate: the bridge's ``on_final_segment`` needs
WICA, so building WICA *before* the bridge lets every reference resolve forward with no placeholder.

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

import gradio as gr
from asr_engine.engine import ASREngine
from reachy_mini_bridge.api import ReachyMiniApi
from wica import Content, TextPart, Wica
from wica.config import ConfigError, MissingEnvError
from wica.contrib.gradio import TranscriptLog

from reachy_mini_interaction_wica.app_ui import build_ui
from reachy_mini_interaction_wica.asr_bridge import AsrBridge, RobotMicSource
from reachy_mini_interaction_wica.config import AppConfig, load_config
from reachy_mini_interaction_wica.robot_control import RobotControl
from reachy_mini_interaction_wica.tts_output import VoiceOutput

logger = logging.getLogger(__name__)


def _speech_input(value: Any, previous: Any) -> Content:
    """Render the triggering `speech_input` entry into the prompt (voice-assistant phrasing).

    An empty string — an ASR final that closed on silence, delivered verbatim — is a real value,
    rendered as ``said to you: ""``; only ``None`` means the person hasn't spoken yet. Its
    person-facing counterpart (how the same entry reads in the transcript) is ``display_entry`` in
    [app_ui.py](app_ui.py)."""
    if value is None:
        return [TextPart("The person hasn't said anything to you yet.")]
    return [TextPart(f'The person said to you: "{value}"')]


@dataclass(frozen=True)
class AppSystem:
    """A frozen snapshot of the built system: the resolved components on one app-owned loop and the
    typed-text ``submit_speech`` hatch.

    Every field is final at construction. ``api`` / ``wica`` / ``bridge`` / ``voice`` are ``None`` when
    that component failed to build (its reason is captured in ``missing``); the UI still opens and the
    surviving paths still work — notably typed text, which reaches the agent whenever ``wica`` is
    running, needing neither ASR nor the robot. ``build_system`` enters the robot's media session (so
    ``say``/mic are live), but neither wires the output channel (``wire`` does, once the UI's
    transcript exists) nor *starts* the agent or ASR capture (``main`` does), so a test can inspect a
    built-but-not-listening system. ``main`` is what tears down via ``_shutdown`` (agent, then ASR
    bridge, then the api's ``__aexit__``, then the loop).
    """

    loop: asyncio.AbstractEventLoop | None
    api: ReachyMiniApi | None
    wica: Wica | None
    bridge: AsrBridge | None
    voice: VoiceOutput | None
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
    """Stand up the whole system from *config* — the build step of `main`, minus the UI, the output
    wiring, and starting/launching.

    Owns a fresh event loop (run in a daemon thread) and injects it into ``Wica.init`` so the ASR
    engine and the robot api share the loop the Agent runs on. Builds in dependency order — robot api →
    voice → wica → bridge — so every component is fully formed when the frozen ``AppSystem`` is
    assembled at the end. The one ``ReachyMiniApi`` is built from ``config.reachy`` (which builds its
    own voice from the config's ``tts`` block, degrading to no voice rather than raising on a build
    failure) and **entered on the loop** here (bringing up the daemon when configured, the robot
    connection, and the media session), because both ``say`` and the mic stream ride it. Construction
    never connects — only ``__aenter__`` does — so a failure there (an upstream ``reachy_mini``
    exception, or the bridge's own ``DaemonError``/``BridgeError``) is caught into a ``Robot:`` entry in
    ``missing``. After a successful construct + enter, ``api.synthesizer_error`` (or the absence of any
    ``reachy.tts`` block) is read into a ``TTS:`` entry — the voice degrades independently, the rest of
    the robot stays up. Skipped when a test injects ``api=`` (the fake carries its own ``say``). Each
    remaining build that can raise on an unset key (``Wica.init`` → ``MissingEnvError``, any other
    ``ConfigError`` such as an unreadable ``system_prompt_file``, or ``ImportError`` for a missing
    provider extra; ``ASREngine`` → ``ValueError``) is likewise caught into ``missing`` rather than
    crashing, so the returned system opens even with credentials or a robot absent. ``api`` /
    ``asr_engine`` inject fakes for tests, bypassing the real build.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(
        target=loop.run_forever, name="reachy-mini-interaction-loop", daemon=True
    ).start()

    missing: list[str] = []

    # --- The robot: one ReachyMiniApi (which builds its own voice from config.reachy.tts) behind
    # say + the mic stream, entered on the app loop. Construction never connects; __aenter__ brings up
    # the daemon (when configured), the robot connection, and the media session — any failure there is
    # an upstream reachy_mini exception or a bridge type, so wrap it in one broad except and degrade
    # (api stays None). -------------------------------------------------------------------------
    robot: ReachyMiniApi | None = None
    try:
        robot = api if api is not None else ReachyMiniApi(config.reachy)
        asyncio.run_coroutine_threadsafe(robot.__aenter__(), loop).result()
    except Exception as exc:  # noqa: BLE001 - any construct/connect failure degrades, it never crashes
        missing.append(f"Robot: {exc}")
        robot = None
    else:
        # The voice degrades independently of the rest of the robot: a build failure (typically the
        # TTS key unset) leaves `robot.synthesizer_error` set instead of raising. Skipped when a test
        # injects `api=` (the fake carries its own `say`, with nothing to report here).
        if api is None:
            if config.reachy.tts is None:
                missing.append(
                    "TTS: no reachy.tts block configured (say is unavailable)"
                )
            elif robot.synthesizer_error is not None:
                missing.append(f"TTS: {robot.synthesizer_error}")

    # --- Speech out: the robot-backed voice, or None when the robot is unavailable --------------
    voice = VoiceOutput(robot) if robot is not None else None

    # --- The agent, on the app-owned loop. No callables here: the sink and the output Command are
    # set in `wire`, once the UI's transcript exists (build, then wire, then start). ---------------
    wica: Wica | None = None
    try:
        wica = Wica.init(config.wica, loop=loop)
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

    if wica is not None:
        # A World-schema operation, so it belongs to the build (like the demo's register_world).
        wica.world.register(
            "speech_input", str, serialize_fn=_speech_input, triggers_llm_call=True
        )

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

    return AppSystem(
        loop=loop,
        api=robot,
        wica=wica,
        bridge=bridge,
        voice=voice,
        missing=missing,
        submit_speech=submit_speech,
    )


def wire(system: AppSystem, transcript: TranscriptLog) -> None:
    """Wire the output channel and the Commands onto the agent — after the UI's presenters exist and
    before ``start()``. Gradio-free: it takes the transcript presenter, not the page, so the tests
    call it with a ``TranscriptLog`` they build themselves.

    The transcript's ``output_sink`` receives the model's free text (private reasoning, shown as the
    💭 item); the voice's ``say`` becomes the output Command (setting it recomposes the system prompt
    to name it, which is why it precedes ``start()``); the dictation and robot-action Commands are
    registered. Each is wired only when its component was built. A no-op without an agent — there is
    nothing to wire into, and the banner already says why."""
    wica = system.wica
    if wica is None:
        return
    wica.set_output_sink(transcript.output_sink)
    if system.voice is not None:
        wica.set_output_command(system.voice.say)
    if system.bridge is not None:
        wica.register_command(system.bridge.start_dictation)
        wica.register_command(system.bridge.stop_dictation)
    # Robot action commands, over the same api as the voice: emotions, head tracking, motors. Only
    # when the robot is up (no api ⇒ nothing to act through); the agent still speaks/reasons without.
    if system.api is not None:
        controls = RobotControl(system.api)
        for command in (
            controls.list_emotions,
            controls.play_emotion,
            controls.start_head_tracking,
            controls.stop_head_tracking,
            controls.get_motors_state,
            controls.set_motors_state,
        ):
            wica.register_command(command)


def _start(system: AppSystem) -> None:
    """Start the agent (if built), then the ASR bridge (only if the agent is up to consume speech)."""
    if system.wica is not None:
        system.wica.start()
        if system.bridge is not None and system.loop is not None:
            asyncio.run_coroutine_threadsafe(
                system.bridge.start(), system.loop
            ).result()


def _degraded_system(message: str) -> AppSystem:
    """A do-nothing system that only carries a banner message (e.g. a config that failed to load)."""
    return AppSystem(
        loop=None,
        api=None,
        wica=None,
        bridge=None,
        voice=None,
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
    except (
        Exception
    ):  # one teardown step must not abort the rest (a timeout lands here too)
        future.cancel()
        logger.exception("teardown step %r failed", step)


def _shutdown(system: AppSystem) -> None:
    """Tear the system down in dependency order: the agent, then ASR capture, then the robot's
    media session, then the loop. The bridge is stopped *before* the api exits so the mic pump is
    cancelled while its stream is still valid, rather than iterating a closed media session.

    Runs on the Gradio thread once ``launch()`` returns (never from a sink, Command, or Event
    subscriber — those run on the loop). Each step is isolated (and each loop hop time-bounded): a
    step that raises or hangs is logged and the rest still run, so one failure cannot strand the
    robot session or the loop thread."""
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


def _serve(system: AppSystem, blocks: gr.Blocks) -> None:
    """Start the system and run the (already built and wired) page, tearing down whatever happens.
    ``_start`` sits *inside* the ``try``: ``build_system`` has already entered the robot's media
    session, so a raise in ``wica.start()`` / ``asr_bridge.start()`` must still exit it and stop the
    loop."""
    try:
        _start(system)
        blocks.launch()
    finally:
        _shutdown(system)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("wica").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description="Talk to a WICA-driven Reachy Mini.")
    # Required, with no default: config.example.json is a template to copy and adapt, not a config
    # anyone should run unmodified — argparse exits with usage guidance instead of silently loading it.
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
    # build → UI → wire → start (+ launch, then close): the UI owns the presenters, so it comes before
    # the wiring that needs the transcript's sink; everything is wired before start.
    ui = build_ui(system)
    wire(system, ui.transcript)
    _serve(system, ui.blocks)


if __name__ == "__main__":
    main()
