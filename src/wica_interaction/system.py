"""The app's wiring + lifecycle, written against an embodiment rather than a body.

Stands up the WICA agent, the ASR engine and the vision pipeline around whatever
[Embodiment](embodiment.py) the entry point hands it — the computer it runs on, a Reachy Mini — on
one app-owned event loop: [AsrBridge](asr_bridge.py) in (over the embodiment's mic),
[GestureInput](gesture_input.py) in (over its camera), the WICA Agent in the middle, the
embodiment's ``voice`` and action Commands out. Nothing here knows what kind of body it has; the
Gradio presentation layer (the page, the presenters it owns, the live views) lives in
[app_ui.py](app_ui.py). See specs/app.md.

The composition follows WICA's conversation demo — **build → UI → wire → start → launch → close** —
which is the order the framework's output-wiring setters exist for (``Wica.init`` takes no
application callables; the sink and the output Command are set afterwards):

- ``build_system(config, embodiment)`` — pure build, no Gradio, no wiring: owns the loop, *enters*
  the embodiment on it, folds the embodiment's ``prompt_addendum`` (and, with vision on, the
  hand-signs section) into the persona (an inline-prompt variant of the WICA config — see
  ``_seeded_wica_config``), ``Wica.init``s the agent and registers the ``speech_input`` entry,
  builds the ASR bridge over ``embodiment.mic`` and the gesture input over ``embodiment.camera``.
  Its ``asr_engine_factory=`` / ``gesture_models=`` kwargs are test seams for injecting fakes.
- ``build_ui(system)`` — the Gradio page (in [app_ui.py](app_ui.py)); it owns the contrib presenters
  (``TranscriptLog`` / ``ReactionLog``, subscribed to the agent's Events in their constructors) and hands
  the transcript back on an ``AppUi`` handle.
- ``wire(system, transcript)`` — Gradio-free: sets the transcript's ``output_sink`` and the
  embodiment's ``voice`` as the output Command on the agent, registers the dictation Commands and
  each of ``embodiment.commands()``. Tests call it with a ``TranscriptLog`` they build themselves,
  playing the UI's role.
- ``run_app(description, load)`` — each embodiment's ``main()`` is this call: parse ``--config``,
  ``load`` it into ``(AppConfig, Embodiment)``, build, then ``_serve`` — UI, wire, start and launch,
  all inside the one ``try`` whose ``finally`` runs ``_shutdown`` (isolated, time-bounded steps; the
  bridge and the vision are stopped before the embodiment exits), since the body is already
  entered by then.

``AppSystem`` is a frozen snapshot of the resolved components (loop, embodiment, wica, bridge,
gesture) and the typed-text ``submit_speech`` hatch — fully formed at construction, nothing
back-patched. The build order — embodiment → **wica → bridge → vision** — is deliberate: the
bridge's ``on_final_segment`` and the gesture callback need WICA, so building WICA *before* them
lets every reference resolve forward with no placeholder.

WICA exposes no accessor for the loop it runs on, so the app *owns* the loop and injects it into
``Wica.init(loop=…)`` — the only way to schedule the ASR engine and the embodiment's async verbs
onto the same loop the Agent runs on. Each build that can raise on an unset key is caught so the app
opens even with credentials missing and says what to set.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from asr_engine.audio import AudioSource
from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from wica import Content, TextPart, Wica
from wica.config import (
    ConfigError,
    MissingEnvError,
    WicaConfig,
    resolve_system_prompt,
)
from wica.contrib.gradio import TranscriptLog

from wica_interaction.app_ui import build_ui
from wica_interaction.asr_bridge import AsrBridge, DictatedPassage
from wica_interaction.config import AppConfig
from wica_interaction.embodiment import Embodiment
from wica_interaction.gesture_input import (
    GestureInput,
    GestureModels,
    hand_signs_prompt_section,
    preload_mediapipe,
    sign_shown,
)

logger = logging.getLogger(__name__)

# Builds the ASR engine over the embodiment's mic (`None` ⇒ the engine opens its own default
# device). A seam so the lifecycle tests can hand in a fake *and* see which source it was given —
# the hand-off that decides whether the robot listens through its own echo-cancelled mic.
AsrEngineFactory = Callable[[ASREngineConfig, AudioSource | None], ASREngine]


def _build_asr_engine(
    config: ASREngineConfig, audio_source: AudioSource | None
) -> ASREngine:
    return ASREngine(config, audio_source=audio_source)


# The inputs are transient (specs/app.md, "Inputs are transient"): each perception is a one-shot
# event the step it triggers observes, then the World clears it (a TTL reset — no trigger, a new
# id) and it is gone from every later prompt. The values are set so the coalescing window (~200 ms)
# always observes the input, and little else does: a step woken by something else — a `say`
# finishing, a command completing, a sign — never re-reads an utterance from earlier as if it were
# current, and a held sign is what the person *did*, not ambient state.
SPEECH_INPUT_TTL = timedelta(seconds=2)
HAND_GESTURE_TTL = timedelta(seconds=1)


def _speech_input(value: Any, previous: Any) -> Content:
    """Render the triggering `speech_input` entry into the prompt.

    It says what was *heard*, not who said it: the mic cannot tell the person in front of the app
    from anyone else in the room. A ``DictatedPassage`` says the dictation is over and the passage
    whole, so the model reads it as the note it asked for, not one more sentence of it. An empty
    string — an ASR final that closed on silence, delivered verbatim — is a real value, rendered as
    ``""``. ``None`` — nothing said yet, or the last utterance expired
    (`SPEECH_INPUT_TTL`) — renders **nothing**: WICA omits an entry whose serializer returns an empty
    `Content`, envelope included, so the prompt carries no `speech_input` block at all rather than
    a stale or empty one. Its person-facing counterpart (how the same entry reads in the transcript)
    is ``display_entry`` in [app_ui.py](app_ui.py)."""
    if value is None:
        return []
    if isinstance(value, DictatedPassage):
        return [
            TextPart(
                f'Dictation is over. You heard this complete dictated passage: "{value}"'
            )
        ]
    return [TextPart(f'You hear someone say: "{value}"')]


def _hand_gesture(hand: int) -> Callable[[Any, Any], Content]:
    """The `serialize_fn` of hand *hand*'s entry — present in a prompt only while the entry holds a
    sign (specs/gesture-input.md, "In the prompt"). Only a sign being *shown* triggers a step
    (`sign_shown`); ``None`` — no sign delivered yet, the hand dropped, or the sign expired
    (`HAND_GESTURE_TTL`) — renders nothing, so the entry is omitted rather than saying "no sign".
    What a sign *means* is deliberately not said here, nor in the persona (except `stop`): the agent
    reads it from the interaction. It says a gesture was *detected*, not that the person made it: the
    camera cannot tell whose hand it is."""

    def serialize(value: Any, previous: Any) -> Content:
        if value is None:
            return []
        return [TextPart(f'Hand gesture detected on hand {hand}: "{value}".')]

    return serialize


@dataclass(frozen=True)
class AppSystem:
    """A frozen snapshot of the built system: the resolved components on one app-owned loop and the
    typed-text ``submit_speech`` hatch.

    Every field is final at construction. ``embodiment`` / ``wica`` / ``bridge`` / ``gesture`` are
    ``None`` when that component failed to build (its reason is captured in ``missing``); the UI
    still opens and the surviving paths still work — notably typed text, which reaches the agent
    whenever ``wica`` is running, needing neither ASR nor a body. ``build_system`` *enters* the
    embodiment (so its voice and mic are live), but neither wires the output channel (``wire``
    does, once the UI's transcript exists) nor *starts* the agent or ASR capture (``_serve`` does),
    so a test can inspect a built-but-not-listening system. ``_shutdown`` is what tears it down
    (agent, then vision, then ASR bridge, then ``embodiment.exit()``, then the loop).
    """

    loop: asyncio.AbstractEventLoop | None
    # The launched embodiment's `display_name` — kept even when its `enter()` raised, so the page
    # still says which body it was launched for; None only when the config never loaded.
    display_name: str | None
    # The body: None when `enter()` raised (its message is in `missing`) — the agent then reasons
    # on typed text with nothing to speak, act or see through. See specs/embodiment.md.
    embodiment: Embodiment | None
    wica: Wica | None
    bridge: AsrBridge | None
    # Vision-in: the hand-sign input over the embodiment's camera; None without a `vision` config
    # section or without an embodiment. See specs/gesture-input.md.
    gesture: GestureInput | None
    missing: list[str]
    # The typed-text hatch: writes the same `speech_input` World update a closed ASR segment makes (so
    # both flow through the identical reason → say → sink path), but ignores empty/whitespace input —
    # a guard ASR finals deliberately bypass. Built in build_system.
    submit_speech: Callable[[str], None]


def build_system(
    config: AppConfig,
    embodiment: Embodiment,
    *,
    asr_engine_factory: AsrEngineFactory = _build_asr_engine,
    gesture_models: GestureModels | None = None,
) -> AppSystem:
    """Stand up the whole system from *config* and *embodiment* — the build step of ``run_app``,
    minus the UI, the output wiring, and starting/launching.

    Owns a fresh event loop (run in a daemon thread) and injects it into ``Wica.init`` so the ASR
    engine and the embodiment's async verbs share the loop the Agent runs on. Builds in dependency
    order — embodiment → persona → wica → bridge → vision — so every component is fully formed when
    the frozen ``AppSystem`` is assembled at the end.

    The embodiment is **entered on the loop** here (the robot's daemon/connection/media session, the
    computer's audio processing and TTS engine). **Any** exception degrades to *no embodiment*: the
    banner gets ``<display_name>: <exc>`` and nothing that needs a body is built. After a successful
    enter, the per-component degrade lines the embodiment gathered (``embodiment.missing`` — a voice
    key unset, an optional dependency absent, an unreadable emotion library) join ``missing`` and the
    corresponding member degrades on its own. The persona is resolved *here*, not inside
    ``Wica.init``, so the embodiment's ``prompt_addendum`` and the hand-signs section can be appended
    to it (see ``_seeded_wica_config``). Each remaining build that can raise on an unset key
    (``Wica.init`` → ``MissingEnvError``, any other ``ConfigError`` such as an unreadable
    ``system_prompt_file``, or ``ImportError`` for a missing provider extra; ``ASREngine`` →
    ``ValueError``) is likewise caught into ``missing`` rather than crashing, so the returned system
    opens even with credentials or a body absent. ``asr_engine_factory`` / ``gesture_models`` inject
    fakes for tests; the factory is called exactly as the real build is — only with a body, over its
    ``mic`` — so a test sees the source the engine was given.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(
        target=loop.run_forever, name="wica-interaction-loop", daemon=True
    ).start()

    missing: list[str] = []

    # MediaPipe before the body: imported once the robot's media runs, it aborts the process on
    # Linux (see `preload_mediapipe`). Only for the shipped detector — an injected one needs none.
    if (
        config.vision is not None
        and config.vision.enabled
        and (gesture_models is None or gesture_models.detector is None)
    ):
        preload_mediapipe()

    # --- The body: entered on the app loop. Any failure is "no embodiment" — the agent still
    # builds and typed text still drives it, but nothing that needs a body is wired. The
    # per-component degrade lines the embodiment gathered inside come back in `missing` too, each
    # degrading only its own member (specs/embodiment.md, "Degradation is per component"). -------
    body: Embodiment | None = embodiment
    try:
        asyncio.run_coroutine_threadsafe(body.enter(), loop).result()
    except Exception as exc:  # noqa: BLE001 - any enter failure degrades, it never crashes
        missing.append(f"{body.display_name}: {exc}")
        body = None
    else:
        missing.extend(body.missing)

    # --- The agent, on the app-owned loop. No callables here: the sink and the output Command are
    # set in `wire`, once the UI's transcript exists (build, then wire, then start). The persona is
    # resolved *here* (not inside Wica.init) so the startup sections can be appended to it: the
    # embodiment's own addendum (the robot's emotion names) and, when vision is on, the hand-signs
    # vocabulary — so a persona never describes hands it cannot see or emotions it cannot play. ---
    addenda = [body.prompt_addendum if body is not None else ""]
    vision = config.vision
    if vision is not None and vision.enabled and body is not None:
        addenda.append(hand_signs_prompt_section())

    wica: Wica | None = None
    try:
        wica = Wica.init(_seeded_wica_config(config.wica, addenda), loop=loop)
    except MissingEnvError as exc:
        missing.append(f"Agent: environment variable {exc.env_var!r} is not set")
    except ConfigError as exc:
        # After MissingEnvError (its subclass). resolve_system_prompt (in _seeded_wica_config) reads
        # `system_prompt_file` and raises this when the file is missing/unreadable — a config typo
        # degrades to the banner, not a traceback.
        missing.append(f"Agent: {exc}")
    except ImportError as exc:
        # The configured provider's extra isn't installed (a setup gap, not a missing key). Surface
        # it in the banner rather than crashing, so the app still opens and says what to install.
        missing.append(f"Agent: {exc}")

    if wica is not None:
        # A World-schema operation, so it belongs to the build (like the demo's register_world).
        # The TTL makes each utterance a one-shot observation (see SPEECH_INPUT_TTL).
        wica.world.register(
            "speech_input",
            str,
            serialize_fn=_speech_input,
            triggers_llm_call=True,
            ttl=SPEECH_INPUT_TTL,
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
    # on_final_segment callback (_deliver_speech) resolves forward with no placeholder. The engine
    # reads the embodiment's mic (None there means it opens its own default device), so speech-in
    # needs a body: with none, the bridge stays None and typed text still drives the agent. -------
    bridge: AsrBridge | None = None
    if body is not None:
        embodied = body  # bound for the closure below: `body` is not reassigned after this point
        try:
            asr = asr_engine_factory(config.asr_engine, body.mic)
            bridge = AsrBridge(
                asr,
                on_final_segment=_deliver_speech,
                mic_error=lambda: embodied.mic_error,
                # The engine keeps its trigger words private; `start_dictation`'s reply names them.
                trigger_words=config.asr_engine.segmentation.trigger_words,
            )
        except (ValueError, ImportError) as exc:
            missing.append(f"ASR: {exc}")

    # --- Vision in: the hand-sign input over the embodiment's camera, only with `vision.enabled`
    # and a body (no embodiment ⇒ no camera) and an agent (there is nothing else to deliver to). The
    # World entries are registered only then, so a vision-less run never tells the model about hands
    # it cannot see. Construction loads no model and opens no camera (both happen on start()), so a
    # failure here is a programming/config error — still a banner line, never a crash. -----------
    gesture: GestureInput | None = None
    if vision is not None and vision.enabled and body is not None and wica is not None:
        world = wica.world

        def _deliver_gesture(hand: int, value: str | None) -> None:
            # Per-hand change-only deliveries from the watcher thread; the entries' trigger predicate
            # keeps only a sign being shown waking the agent. Dropped while the agent is not running.
            if wica is not None and wica.is_running:
                world.update(f"hand_gesture_{hand}", value)

        try:
            # One entry per hand the pipeline can publish, so two hands are two states the agent
            # reads side by side rather than one merged signal. Coalescing is *not* bypassed: two
            # hands changing on the same frame then make one reasoning step, not two. The TTL makes
            # a sign a one-shot observation too (see HAND_GESTURE_TTL); a sign the hand keeps
            # holding is not re-delivered (GestureInput delivers changes only), so it is gone from
            # the prompt until the hand changes.
            for hand in range(1, vision.max_hands + 1):
                world.register(
                    f"hand_gesture_{hand}",
                    str,
                    serialize_fn=_hand_gesture(hand),
                    triggers_llm_call=True,
                    trigger_condition_fn=sign_shown,
                    ttl=HAND_GESTURE_TTL,
                )
            gesture = GestureInput(
                body.camera(fps=vision.fps),
                on_gesture=_deliver_gesture,
                threshold=vision.threshold,
                fps=vision.fps,
                max_hands=vision.max_hands,
                hold_s=vision.hold_s,
                device=vision.device,
                models=gesture_models,
            )
        except Exception as exc:  # noqa: BLE001 - vision degrades to a banner line, never a crash
            missing.append(f"Vision: {exc}")
            gesture = None

    return AppSystem(
        loop=loop,
        display_name=embodiment.display_name,
        embodiment=body,
        wica=wica,
        bridge=bridge,
        gesture=gesture,
        missing=missing,
        submit_speech=submit_speech,
    )


def _seeded_wica_config(config: WicaConfig, addenda: Sequence[str]) -> WicaConfig:
    """The WICA config `Wica.init` gets: *config* with its persona resolved here and the non-empty
    *addenda* appended — as an inline `system_prompt` (the `system_prompt_file` cleared, since the
    two are exclusive). WICA's `resolve_system_prompt` prefers the inline form, so the seeded text is
    what its prompt composition sees, verbatim. The prompt-file read happens here, so an unreadable
    file raises `ConfigError` from this call (the caller degrades it the same way it always did).
    See specs/app.md, build step 3."""
    persona = resolve_system_prompt(config.agent) + "".join(a for a in addenda if a)
    return replace(
        config,
        agent=replace(config.agent, system_prompt=persona, system_prompt_file=None),
    )


def wire(system: AppSystem, transcript: TranscriptLog) -> None:
    """Wire the output channel and the Commands onto the agent — after the UI's presenters exist and
    before ``start()``. Gradio-free: it takes the transcript presenter, not the page, so the tests
    call it with a ``TranscriptLog`` they build themselves.

    The transcript's ``output_sink`` receives the model's free text (private reasoning, shown as the
    💭 item); the embodiment's ``voice`` becomes the output Command (setting it recomposes the system
    prompt to name it, which is why it precedes ``start()``); the dictation Commands and each of the
    embodiment's action Commands are registered. Each is wired only when its component was built. A
    no-op without an agent — there is nothing to wire into, and the banner already says why."""
    wica = system.wica
    if wica is None:
        return
    wica.set_output_sink(transcript.output_sink)
    embodiment = system.embodiment
    if embodiment is not None and embodiment.voice is not None:
        wica.set_output_command(embodiment.voice)
    if system.bridge is not None:
        wica.register_command(system.bridge.start_dictation)
        wica.register_command(system.bridge.stop_dictation)
    # The body's own action Commands (the robot's emotion + motors; none on the computer), over
    # whatever the embodiment acts through. Only with a body — the agent still speaks and reasons
    # without one.
    if embodiment is not None:
        for command in embodiment.commands():
            wica.register_command(command)


def _start(system: AppSystem) -> None:
    """Start the agent (if built), then the ASR bridge (only if the agent is up to consume speech)."""
    if system.wica is not None:
        system.wica.start()
        if system.bridge is not None and system.loop is not None:
            asyncio.run_coroutine_threadsafe(
                system.bridge.start(), system.loop
            ).result()
        if system.gesture is not None:
            # Threads, not a coroutine: the camera source hops to the loop itself per poll.
            system.gesture.start()


def _degraded_system(message: str) -> AppSystem:
    """A do-nothing system that only carries a banner message (e.g. a config that failed to load)."""
    return AppSystem(
        loop=None,
        display_name=None,
        embodiment=None,
        wica=None,
        bridge=None,
        gesture=None,
        missing=[message],
        submit_speech=lambda _text: None,
    )


# Per-step bound on each teardown coroutine, so a hung provider connection or media session cannot
# wedge exit.
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
    """Tear the system down in dependency order: the agent, then vision, then ASR capture, then the
    embodiment, then the loop. Vision and the bridge are stopped *before* the embodiment exits so the
    camera poll is joined and the mic pump cancelled while the body is still live, rather than
    polling a closed session.

    Runs on the Gradio thread once ``launch()`` returns (never from a sink, Command, or Event
    subscriber — those run on the loop). Each step is isolated (and each loop hop time-bounded): a
    step that raises or hangs is logged and the rest still run, so one failure cannot strand the
    body's session or the loop thread."""
    loop = system.loop
    if system.wica is not None:
        try:
            system.wica.close()
        except Exception:  # one teardown step must not abort the rest
            logger.exception("teardown step 'wica.close' failed")
    # Vision before the embodiment exits: its camera source may poll the body on the loop, so it
    # must be joined while that session is still open (the same reason the bridge precedes it).
    # A synchronous join, not a loop hop — the pipeline is threads.
    if system.gesture is not None:
        try:
            system.gesture.stop()
        except Exception:  # one teardown step must not abort the rest
            logger.exception("teardown step 'gesture.stop' failed")
    # Only a running bridge is stopped — one can be built but never started (no agent ⇒ _start
    # skipped it), and this keeps teardown off ASREngine.stop()'s behavior on a never-started engine.
    bridge = system.bridge
    if bridge is not None and loop is not None and bridge.status()["running"]:
        _run_on_loop(loop, bridge.stop(), step="asr.stop")
    if system.embodiment is not None and loop is not None:
        # Release the body (a media session, a robot connection, …) on the loop before it stops.
        _run_on_loop(loop, system.embodiment.exit(), step="embodiment.exit")
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)


def _serve(system: AppSystem) -> None:
    """Everything after the build — UI → wire → start → launch — tearing down whatever happens.

    All four sit *inside* the ``try``: ``build_system`` has already entered the embodiment (a robot
    session, a daemon the bridge spawned), so a raise anywhere from here on — the page failing to
    build, ``wire`` rejecting a Command name, ``wica.start()`` / ``asr_bridge.start()`` — must still
    exit it and stop the loop. The UI owns the presenters, so it comes before the wiring that needs
    the transcript's sink; everything is wired before start."""
    try:
        ui = build_ui(system)
        wire(system, ui.transcript)
        _start(system)
        ui.blocks.launch()
    finally:
        _shutdown(system)


def run_app(
    *, description: str, load: Callable[[str], tuple[AppConfig, Embodiment]]
) -> None:
    """The whole app, from the command line: each embodiment's ``main()`` is one call to this.

    *description* is the argparse description (what this command talks to); *load* reads a config
    path into the shared ``AppConfig`` plus the embodiment built from that embodiment's own sections
    (no I/O in its constructor — ``build_system`` enters it). A ``ValueError`` from *load* — a
    missing, malformed, or wrongly-sectioned file — opens the UI with the message in the banner
    rather than a traceback.
    """
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("wica").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description=description)
    # Required, with no default: the committed example configs are templates to copy and adapt, not
    # configs anyone should run unmodified — argparse exits with usage guidance instead of silently
    # loading one.
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the composed app config JSON (copy the example config to start).",
    )
    args = parser.parse_args()

    try:
        app_config, embodiment = load(args.config)
    except ValueError as exc:
        # A malformed/missing config still opens the UI with an explanation rather than a traceback.
        system = _degraded_system(str(exc))
    else:
        system = build_system(app_config, embodiment)
    # build → UI → wire → start → launch → close; everything after the build is `_serve`'s, under
    # the one `finally` that tears the entered body down.
    _serve(system)
