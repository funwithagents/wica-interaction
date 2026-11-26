"""The app's wiring + lifecycle, written against an embodiment rather than a body.

Stands up one **session** — the WICA agent, the ASR engine and the vision pipeline around whatever
[Embodiment](embodiment.py) the entry point hands it (the computer it runs on, a Reachy Mini) — on
an event loop of its own: [AsrBridge](asr_bridge.py) in (over the embodiment's mic),
[GestureInput](gesture_input.py) in (over its camera), the WICA Agent in the middle, the
embodiment's ``voice`` and action Commands out. Nothing here knows what kind of body it has; the
Gradio page lives in [app_ui.py](app_ui.py), the start/stop cycle in [session.py](session.py). See
specs/app.md and specs/session.md.

A session's composition follows WICA's conversation demo — **build → presenters → wire → start**,
then **close** — the order the framework's output-wiring setters exist for (``Wica.init`` takes no
application callables; the output Command is set afterwards):

- ``build_system(config, embodiment)`` — pure build, no Gradio, no wiring: owns the session's loop
  and its thread, *enters* the embodiment on it, folds the embodiment's ``prompt_addendum`` (and,
  with vision on, the hand-signs section) into the persona (an inline-prompt variant of the WICA
  config — see ``_seeded_wica_config``), ``Wica.init``s the agent and registers the
  ``speech_input`` entry, registers the embodiment's own inputs (``embodiment.inputs()``, each with a
  guarded ``deliver``), builds the ASR bridge over ``embodiment.mic`` and the gesture input over
  ``embodiment.camera``. Its ``asr_engine_factory=`` / ``gesture_models=`` kwargs are test seams.
- ``wire(system)`` — Gradio-free: sets the embodiment's ``voice`` as the output Command on the
  agent (the model speaks it as a ``<say>`` tag in its text), registers the dictation Commands and
  each of ``embodiment.commands()``.
- ``open_session(used_config, folder, …)`` — one session in order: load the config against the
  app's folder, construct the embodiment, build, the presenters (subscribed to the agent's Events
  before it starts), wire, start; whatever raises after the build closes what was built.
- ``close_session(system)`` — the teardown, in isolated and time-bounded steps, the bridge and the
  vision stopped before the embodiment exits, then the loop stopped, its thread joined and the loop
  closed: nothing of the session is left in the process.
- ``run_app(app, …)`` — each embodiment's ``main()`` is this call: the app's folder or a config
  file, the session, autostart, the page; or, as ``<app> configure``, the Setup and Secrets tabs
  alone over the folder or a file, with no session.

``AppSystem`` is a frozen snapshot of the resolved components (loop, embodiment, wica, bridge,
gesture) and the typed-text ``submit_speech`` hatch — fully formed at construction, nothing
back-patched. The build order — embodiment → **wica → bridge → vision** — is deliberate: the
bridge's ``on_final_segment`` and the gesture callback need WICA, so building WICA *before* them
lets every reference resolve forward with no placeholder.

WICA exposes no accessor for the loop it runs on, so the app *owns* the loop and injects it into
``Wica.init(loop=…)`` — the only way to schedule the ASR engine and the embodiment's async verbs
onto the same loop the Agent runs on. Each build that can raise on an unset key is caught, so a
session still starts with something missing and says what.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Protocol

from app_setup.app import App, AppModel
from app_setup.autostart import add_start_arguments, starts_at_launch
from app_setup.config_file import ConfigFile, ConfigFileError
from app_setup.folder import ConfigFolder, default_folder
from app_setup.launch import add_config_arguments, open_source
from app_setup.ui import config_page
from asr_engine.audio import AudioSource
from asr_engine.config import ASREngineConfig, AudioSourceConfig
from asr_engine.engine import ASREngine
from wica import Content, TextPart, Wica
from wica.config import (
    ConfigError,
    MissingSecretError,
    WicaConfig,
    resolve_system_prompt,
)
from wica.contrib.gradio import ReactionLog, TranscriptLog

from wica_interaction.app_ui import build_page, entry_display, entry_label
from wica_interaction.asr_bridge import AsrBridge, DictatedPassage
from wica_interaction.config import AppConfig
from wica_interaction.embodiment import Deliver, Embodiment, Input
from wica_interaction.gesture_input import (
    GestureInput,
    GestureModels,
    hand_signs_prompt_section,
    preload_mediapipe,
    preload_transformers,
    sign_shown,
)
from wica_interaction.latency import LatencyCollector, install_tracing
from wica_interaction.session import Running, Session

logger = logging.getLogger(__name__)

# Builds the ASR engine over the embodiment's mic (`None` ⇒ the engine opens its own default
# device). A seam so the lifecycle tests can hand in a fake *and* see which source it was given —
# the hand-off that decides whether the robot listens through its own echo-cancelled mic.
AsrEngineFactory = Callable[[ASREngineConfig, AudioSource | None], ASREngine]


def _build_asr_engine(
    config: ASREngineConfig, audio_source: AudioSource | None
) -> ASREngine:
    return ASREngine(asr_config_for(config, audio_source), audio_source=audio_source)


def asr_config_for(
    config: ASREngineConfig, audio_source: AudioSource | None
) -> ASREngineConfig:
    """The engine config for the mic the body supplies (specs/asr-bridge.md, "The mic is the embodiment's").

    The engine checks ``audio_source.type`` against how it is built: a source handed to it needs
    ``"custom"``. An ``"input-device"`` block names a device, and whether the engine opens it
    itself or the body opens it and hands over what it made of it (the computer's echo canceller)
    is the body's decision, taken at ``enter()`` — so with a mic supplied, that block becomes
    ``"custom"`` here. Any other type is left as written: ``"custom"`` already fits, and a
    ``"file"`` next to a supplied mic is the engine's own error, not silently overridden.
    """
    if audio_source is None or config.audio_source.type != "input-device":
        return config
    return replace(config, audio_source=AudioSourceConfig(type="custom"))


# The inputs are transient (specs/app.md, "Inputs are transient"): each perception is a one-shot
# event the step it triggers observes, then the World clears it (a TTL reset — no trigger, a new
# id) and it is gone from every later prompt. The values are set so the coalescing window (~200 ms)
# always observes the input, and little else does: a step woken by something else — a command
# completing, a sign, the model's own `<next>` note after it spoke — never re-reads an utterance from earlier as if it were
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
    does, once the session's presenters exist) nor *starts* the agent or ASR capture (``_start`` does),
    so a test can inspect a built-but-not-listening system. ``close_session`` is what tears it down
    (agent, then vision, then ASR bridge, then ``embodiment.exit()``, then the loop).
    """

    loop: asyncio.AbstractEventLoop | None
    # The thread running `loop`, joined by `close_session` once the loop stops.
    loop_thread: threading.Thread | None
    # The launched embodiment's `display_name` — kept even when its `enter()` raised, so the page
    # still says which body it was launched for; None only when the config never loaded.
    display_name: str | None
    # The body: None when `enter()` raised (its message is in `missing`) — the agent then reasons
    # on typed text with nothing to speak, act or see through. See specs/embodiment.md.
    embodiment: Embodiment | None
    wica: Wica | None
    bridge: AsrBridge | None
    # Vision-in: the hand-sign input over the embodiment's camera; None without a `vision` config
    # section (hand signs off) or without an embodiment. See specs/gesture-input.md.
    gesture: GestureInput | None
    missing: list[str]
    # The typed-text hatch: writes the same `speech_input` World update a closed ASR segment makes (so
    # both flow through the identical reason → say → sink path), but ignores empty/whitespace input —
    # a guard ASR finals deliberately bypass. Built in build_system.
    submit_speech: Callable[[str], None]
    # Whether the config turns speech input (an `asr` section) and hand signs (a `vision` section)
    # on: absent means off, by choice — the page says "off" rather than "unavailable" then.
    speech_on: bool = True
    signs_on: bool = False
    # The body's own perceptions (`embodiment.inputs()`), each with the guarded `deliver` built for
    # it: registered at build, started by `_start` once the agent runs, stopped by `close_session` right
    # after it. Empty without a body or an agent. See specs/embodiment.md, "`inputs()` is the mirror
    # of `commands()`".
    inputs: tuple[tuple[Input, Deliver], ...] = ()
    # How the transcript labels the body's entries: key → the entry's `display_fn` (app_ui's
    # `entry_display` tries these before the app's own `display_entry`).
    input_displays: Mapping[str, Callable[[Any], str]] = field(default_factory=dict)
    # The latency view's collector (specs/latency-view.md): the spans of this run and the agent's
    # records, joined per reaction. None without an agent: there is no reaction to measure.
    latency: LatencyCollector | None = None


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
    (``Wica.init`` → ``MissingSecretError``, any other ``ConfigError`` such as an unreadable
    ``system_prompt_file``, or ``ImportError`` for a missing provider extra; ``ASREngine`` →
    ``ValueError``) is likewise caught into ``missing`` rather than crashing, so the returned system
    opens even with credentials or a body absent. ``asr_engine_factory`` / ``gesture_models`` inject
    fakes for tests; the factory is called exactly as the real build is — only with a body, over its
    ``mic`` — so a test sees the source the engine was given.
    """
    loop = asyncio.new_event_loop()
    # A daemon so an interrupted process never waits on it; `close_session` joins it.
    loop_thread = threading.Thread(
        target=loop.run_forever, name="wica-interaction-loop", daemon=True
    )
    loop_thread.start()

    missing: list[str] = []

    # MediaPipe before the body: imported once the robot's media runs, it aborts the process on
    # Linux (see `preload_mediapipe`). Only for the shipped detector — an injected one needs none.
    # Hand signs are on when the config has a `vision` section (absent means off).
    vision_on = config.vision is not None
    if vision_on and (gesture_models is None or gesture_models.detector is None):
        preload_mediapipe()
    # Transformers before any worker thread: imported on two at once (the classifier's stage and
    # the mlx-audio ASR's model load), it fails (see `preload_transformers`). Only for the shipped
    # classifier — an injected one needs none.
    if vision_on and (gesture_models is None or gesture_models.classifier is None):
        preload_transformers()

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

    # --- The agent, on the app-owned loop. No callables here: the output Command is set in `wire`,
    # after the UI's presenters have subscribed (build, then UI, then wire, then start). The persona is
    # resolved *here* (not inside Wica.init) so the startup sections can be appended to it: the
    # embodiment's own addendum (the robot's emotion names) and, when vision is on, the hand-signs
    # vocabulary — so a persona never describes hands it cannot see or emotions it cannot play. ---
    addenda = [body.prompt_addendum if body is not None else ""]
    vision = config.vision
    if vision is not None and body is not None:
        addenda.append(hand_signs_prompt_section())

    # --- The latency view's collection, before the agent so its first spans are seen: the SDK is
    # installed once per process, and this run's collector takes the forwarding over. An exporter
    # the environment asks for and cannot be had is a banner line, never a failure. -------------
    latency = LatencyCollector()
    missing.extend(install_tracing(latency))

    wica: Wica | None = None
    try:
        wica = Wica.init(_seeded_wica_config(config.wica, addenda), loop=loop)
    except MissingSecretError as exc:
        missing.append(f"Agent: {exc}")
    except ConfigError as exc:
        # After MissingSecretError (its subclass). resolve_system_prompt (in _seeded_wica_config) reads
        # `system_prompt_file` and raises this when the file is missing/unreadable — a config typo
        # degrades to the banner, not a traceback.
        missing.append(f"Agent: {exc}")
    except ImportError as exc:
        # The configured provider's extra isn't installed (a setup gap, not a missing key). Surface
        # it in the banner rather than crashing, so the app still opens and says what to install.
        missing.append(f"Agent: {exc}")

    if wica is not None:
        latency.attach(wica)
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

    # --- The body's own inputs (specs/embodiment.md, "`inputs()` is the mirror of `commands()`"):
    # each input's entries registered as it declares them, and a `deliver` of its own that writes
    # only those keys, guarded on a running agent like the funnel above. Only with a body and an
    # agent; nothing is started here (`_start` does, once the agent runs). ----------------------
    inputs: list[tuple[Input, Deliver]] = []
    input_displays: dict[str, Callable[[Any], str]] = {}
    if body is not None and wica is not None:
        for body_input in body.inputs():
            registered = _register_input(wica, body_input)
            if isinstance(registered, str):
                missing.append(f"{body.display_name}: {registered}")
                continue
            inputs.append((body_input, registered))
            for entry in body_input.entries:
                if entry.display_fn is not None:
                    input_displays[entry.key] = entry.display_fn

    # --- Speech in: the ASR bridge, or None with a captured reason. Built after the agent, so its
    # on_final_segment callback (_deliver_speech) resolves forward with no placeholder. The engine
    # reads the embodiment's mic (None there means it opens its own default device), so speech-in
    # needs a body: with none, the bridge stays None and typed text still drives the agent. With no
    # `asr` section speech input is off: no engine, no bridge, no dictation Commands, and no banner
    # line either — off is the config's choice, not something missing. -------------------------
    bridge: AsrBridge | None = None
    asr_config = config.asr_engine
    if body is not None and asr_config is not None:
        embodied = body  # bound for the closure below: `body` is not reassigned after this point
        try:
            asr = asr_engine_factory(asr_config, body.mic)
            bridge = AsrBridge(
                asr,
                on_final_segment=_deliver_speech,
                mic_error=lambda: embodied.mic_error,
                # The engine keeps its trigger words private; `start_dictation`'s reply names them.
                trigger_words=asr_config.segmentation.trigger_words,
                # The person speaking, for the latency timeline's You row.
                on_speech=latency.speech if wica is not None else None,
            )
        except (ValueError, ImportError) as exc:
            missing.append(f"ASR: {exc}")

    # --- Vision in: the hand-sign input over the embodiment's camera, only with a `vision` section
    # and a body (no embodiment ⇒ no camera) and an agent (there is nothing else to deliver to). The
    # World entries are registered only then, so a vision-less run never tells the model about hands
    # it cannot see. Construction loads no model and opens no camera (both happen on start()), so a
    # failure here is a programming/config error — still a banner line, never a crash. -----------
    gesture: GestureInput | None = None
    if vision is not None and body is not None and wica is not None:
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
                release_hold_s=vision.release_hold_s,
                device=vision.device,
                models=gesture_models,
            )
        except Exception as exc:  # noqa: BLE001 - vision degrades to a banner line, never a crash
            missing.append(f"Vision: {exc}")
            gesture = None

    return AppSystem(
        loop=loop,
        loop_thread=loop_thread,
        display_name=embodiment.display_name,
        embodiment=body,
        wica=wica,
        bridge=bridge,
        gesture=gesture,
        missing=missing,
        submit_speech=submit_speech,
        speech_on=asr_config is not None,
        signs_on=vision is not None,
        inputs=tuple(inputs),
        input_displays=input_displays,
        latency=latency if wica is not None else None,
    )


def _register_input(wica: Wica, body_input: Input) -> Deliver | str:
    """Register *body_input*'s entries in *wica*'s World and build its ``deliver``; or, when the
    World refuses one of them (a key already held, an invalid key), unregister the ones this input
    already registered and return why, for the banner — the input is then dropped, the rest of the
    app up.

    ``deliver(key, value)`` writes ``world.update(key, value)`` for the input's own keys only (any
    other is logged and dropped: an input writes what it declared) and only while the agent runs
    (``world.update`` raises while stopped; a perception before start or after teardown is
    dropped, as speech and signs are)."""
    world = wica.world
    entries = tuple(body_input.entries)
    done: list[str] = []
    try:
        for entry in entries:
            world.register(
                entry.key,
                entry.type,
                serialize_fn=entry.serialize_fn,
                triggers_llm_call=entry.triggers_llm_call,
                trigger_condition_fn=entry.trigger_condition_fn,
                ttl=entry.ttl,
            )
            done.append(entry.key)
    except ValueError as exc:
        for key in done:
            world.unregister(key)
        return str(exc)
    keys = frozenset(done)

    def deliver(key: str, value: Any) -> None:
        if key not in keys:
            logger.warning(
                "an input delivered %r, which it did not declare; dropped", key
            )
            return
        if wica.is_running:
            world.update(key, value)

    return deliver


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


def wire(system: AppSystem) -> None:
    """Wire the output channel and the Commands onto the agent — before ``start()``. Gradio-free.

    The embodiment's ``voice`` becomes the output Command: the model speaks by writing a ``<say>``
    tag in its text, and setting it recomposes the system prompt to teach that tag, which is why it
    precedes ``start()``. The model's text outside the tag is private: the transcript shows it (💭)
    from the agent's Events, with nothing wired here. The dictation Commands and each of the
    embodiment's action Commands are registered. Each is wired only when its component was built. A
    no-op without an agent — there is nothing to wire into, and the banner already says why."""
    wica = system.wica
    if wica is None:
        return
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
    # What the latency view takes as each Command's effect start: the body's declarations, and the
    # output Command's name (its tail is the Voice lane). After the Commands, so every name exists.
    if system.latency is not None:
        system.latency.set_effects(
            embodiment.effects() if embodiment is not None else {},
            output_command=wica.agent.output_command_name,
        )


def _start(system: AppSystem) -> None:
    """Start the agent (if built), then — only with it, there being nothing else to consume them —
    the ASR bridge, the gesture input and the body's own inputs."""
    if system.wica is not None:
        system.wica.start()
        if system.bridge is not None and system.loop is not None:
            asyncio.run_coroutine_threadsafe(
                system.bridge.start(), system.loop
            ).result()
        if system.gesture is not None:
            # Threads, not a coroutine: the camera source hops to the loop itself per poll.
            system.gesture.start()
        if system.loop is not None:
            for body_input, deliver in system.inputs:
                # After the agent: a producer may deliver at once (what it already perceives). One
                # that fails to start is logged and stays silent; the app, and the next input, go on.
                try:
                    asyncio.run_coroutine_threadsafe(
                        body_input.start(deliver), system.loop
                    ).result()
                except Exception:
                    logger.exception("an embodiment input failed to start")


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


def close_session(system: AppSystem) -> None:
    """Tear the session down in dependency order: the agent, then the body's inputs, then vision,
    then ASR capture, then the embodiment, then the loop — stopped, its thread joined, closed.
    Vision and the bridge are stopped *before* the embodiment exits so the camera poll is joined
    and the mic pump cancelled while the body is still live, rather than polling a closed session.

    Runs on the thread that stops the session (a Gradio worker, or the main thread at exit — never
    from a sink, Command, or Event subscriber, which run on the loop). Each step is isolated (and
    each loop hop, the thread's join included, time-bounded): a step that raises or hangs is logged
    and the rest still run, so one failure cannot strand the body's session or the loop thread.
    Afterwards nothing of the session is left in the process (specs/session.md, "Stop")."""
    loop = system.loop
    if system.wica is not None:
        try:
            system.wica.close()
        except Exception:  # one teardown step must not abort the rest
            logger.exception("teardown step 'wica.close' failed")
    # The body's inputs right after the agent, before anything the body owns stops: a producer reads
    # from the body (the robot's users and head tracking), which its exit resets.
    if loop is not None:
        for body_input, _deliver in system.inputs:
            _run_on_loop(loop, body_input.stop(), step="input.stop")
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
        _stop_loop(loop, system.loop_thread)


def _stop_loop(
    loop: asyncio.AbstractEventLoop, thread: threading.Thread | None
) -> None:
    """Stop *loop*, wait for its *thread* to return from ``run_forever``, and close it. A thread
    that does not return in time is logged and the loop left open: closing a running loop raises."""
    if loop.is_closed():
        return
    loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(_SHUTDOWN_TIMEOUT_S)
        if thread.is_alive():
            logger.error(
                "the session's loop did not stop within %ss", _SHUTDOWN_TIMEOUT_S
            )
            return
    try:
        loop.close()
    except Exception:  # one teardown step must not abort the rest
        logger.exception("teardown step 'loop.close' failed")


class _Loaded(Protocol):
    """What an embodiment's ``load_config`` returns: its config, the shared part as ``.app``."""

    @property
    def app(self) -> AppConfig: ...


def open_session(
    used_config: Mapping[str, Any],
    folder: Path,
    *,
    load: Callable[..., _Loaded],
    embodiment: Callable[[Any], Embodiment],
    asr_engine_factory: AsrEngineFactory = _build_asr_engine,
    gesture_models: GestureModels | None = None,
) -> Running:
    """One session, in order (specs/app.md, "Wiring and lifecycle"): *load* the config document
    against the app's *folder*, construct the *embodiment* from it, ``build_system``, the presenters
    (subscribed to the agent's Events before it starts, so they see the first reaction), ``wire``,
    ``_start``. A raise after the build closes what was built before it propagates: the body is
    already entered by then (a robot session, a daemon the bridge spawned). The test seams of
    ``build_system`` pass through."""
    loaded = load(used_config, base_dir=folder)
    system = build_system(
        loaded.app,
        embodiment(loaded),
        asr_engine_factory=asr_engine_factory,
        gesture_models=gesture_models,
    )
    try:
        display = entry_display(system)
        transcript = TranscriptLog(system.wica, display)
        reactions = ReactionLog(system.wica, display)
        if system.latency is not None:
            # The latency table's "heard / seen" reads like the transcript's labels.
            system.latency.describe = lambda entry: entry_label(display, entry)
        wire(system)
        _start(system)
    except BaseException:
        close_session(system)
        raise
    return Running(
        system=system,
        transcript=transcript,
        reactions=reactions,
        close=lambda: close_session(system),
    )


def copy_persona(source: Traversable, target: Path) -> Path:
    """*target*, copied from *source* (a persona shipped in the package) when it does not exist.
    Never overwritten: the file in the app's folder is the user's (specs/setup.md)."""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


# The subcommand that edits the config with no session (specs/setup.md, "Three launches").
CONFIGURE = "configure"


def run_app[C: _Loaded](
    app: App,
    *,
    load: Callable[..., C],
    embodiment: Callable[[C], Embodiment],
    ensure_persona: Callable[[Path], Path],
    check: Callable[[Mapping[str, Any]], list[str]] | None = None,
    argv: Sequence[str] | None = None,
) -> None:
    """The whole app, from the command line: each embodiment's ``main()`` is one call to this
    (specs/app.md, "The entry point").

    *app* is the embodiment's app-setup declaration (its sections, its default config, autostart);
    *load* is its ``load_config(data, *, base_dir)``; *embodiment* constructs the body from what
    *load* returned (no I/O in the constructor — ``build_system`` enters it); *ensure_persona*
    copies its persona into the folder; *check*, when given, says what is wrong across the
    config's sections (the computer's hand signs without a camera), which Start refuses like a
    blocker (``Session``).

    The app runs from its folder (``--folder``, default ``~/.config/<app.name>``): building the
    ``AppModel`` creates it, writes the default config into a new one and loads its ``.env``. Or it
    runs a config file as it is (``--config FILE``): never written, read-only on the Setup tab, its
    relative paths and its ``.env`` next to it, nothing copied anywhere; a file that is missing or
    unreadable stops the app at launch. ``--env PATH`` names another ``.env`` for either. The
    session starts at launch when ``starts_at_launch`` says so (from a file, unless
    ``--no-start``; else ``--start`` / ``--no-start`` over the folder's choice); what keeps it from
    starting is logged and shown on the page, which stays up. The page is served until the process
    is asked to stop; a running session is stopped then.

    ``<app> configure …`` serves the Setup and Secrets tabs alone, with no session
    (``_configure``).
    """
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("wica").setLevel(logging.INFO)

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [CONFIGURE]:
        _configure(app, ensure_persona, args[1:])
        return

    parser = argparse.ArgumentParser(
        description=app.title,
        epilog=f"{app.name} {CONFIGURE} --help: edit the config, with no session.",
    )
    add_config_arguments(parser, app)
    _add_serve_arguments(parser)
    add_start_arguments(parser)
    options = parser.parse_args(args)

    def opened(used_config: Mapping[str, Any], path: Path) -> Running:
        return open_session(used_config, path, load=load, embodiment=embodiment)

    session = Session(opened, check=check)
    try:
        source = open_source(options, app)
    except ConfigFileError as exc:
        parser.error(str(exc))
    # Building the model makes the folder ready and loads the .env; the config is locked while a
    # session runs. A file is run as it is: nothing is copied next to it.
    model = AppModel(app, source, locked=session.locked)
    logger.warning("config: %s", source.config_path)
    if isinstance(source, ConfigFolder):
        ensure_persona(model.path)
    starts = starts_at_launch(model, options.start)
    if starts:
        for reason in session.start(model):
            logger.warning("the session did not start at launch: %s", reason)
    try:
        build_page(model, session, open_on="interaction" if starts else "setup").launch(
            server_name=options.host, server_port=options.port
        )
    finally:
        session.stop()


def _add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1", help="the address to serve on")
    parser.add_argument("--port", type=int, default=None, help="the port to serve on")


def _configure(
    app: App, ensure_persona: Callable[[Path], Path], argv: Sequence[str]
) -> None:
    """``<app> configure``: the Setup and Secrets tabs (app-setup's ``config_page``) and no
    session. With ``--config FILE`` it edits that file (Save writes it back; it must exist);
    without, the app's folder, made ready as a launch would (the default config, the persona)."""
    parser = argparse.ArgumentParser(
        prog=f"{app.name} {CONFIGURE}",
        description=f"{app.title}: edit the config, with no session.",
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--folder",
        type=Path,
        default=default_folder(app.name),
        metavar="DIR",
        help="the app's folder whose config.json is edited (default: %(default)s)",
    )
    where.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="the config file to edit instead (it must exist)",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        metavar="PATH",
        help="the .env file the keys are read from and saved to "
        "(default: the .env next to the config)",
    )
    _add_serve_arguments(parser)
    options = parser.parse_args(argv)
    source: ConfigFolder | ConfigFile
    try:
        if options.config is not None:
            source = ConfigFile(options.config, env_path=options.env, writable=True)
        else:
            source = ConfigFolder(
                options.folder, app.default_config, env_path=options.env
            )
    except ConfigFileError as exc:
        parser.error(str(exc))
    model = AppModel(app, source)
    logger.warning("config: %s", source.config_path)
    if isinstance(source, ConfigFolder):
        ensure_persona(model.path)
    config_page(model).launch(server_name=options.host, server_port=options.port)
