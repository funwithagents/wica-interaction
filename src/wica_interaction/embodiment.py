"""The ``Embodiment`` protocol: what the shared lifecycle (system.py) needs from a body.

The app is one program that can be embodied in different things — the computer it runs on, a Reachy
Mini, later a Nao. Everything WICA-side is the same whatever the body; what varies is only **where
audio comes in and goes out, what the agent can act with, what it sees through, and what the body
itself perceives** (``inputs()``, the mirror of ``commands()``). This module
names that variation, and nothing else: it imports no robot SDK and no audio engine.

A ``typing.Protocol``, not a base class, so each real embodiment is checked structurally by pyright
at the ``build_system(config, embodiment)`` call — an implementation that drifts from this surface
is a type error in its own package — and the lifecycle tests run over a ``FakeEmbodiment`` that
imports neither. See specs/embodiment.md for who calls each member and when.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from asr_engine.audio import AudioSource
from wica import Command, Content

from wica_interaction.gesture_input import CameraFeed

# The `say` output Command: non-raising, "OK" on success, "TTS error: …" otherwise, awaiting real
# playback so the Command stays `running` for the duration of the speech. Registered directly, so
# its name, signature and docstring are what the model reads as the voice's description.
Voice = Callable[[str], Awaitable[str]]

# How an input's producer writes one of its own World entries: `deliver(key, value)`. The lifecycle
# builds it (system.py): it accepts the input's declared keys only and drops a write while the agent
# is not running, as every input of the app does.
Deliver = Callable[[str, Any], None]


@dataclass(frozen=True)
class InputEntry:
    """One World entry an embodiment's input writes: what ``world.register`` takes, and how the
    transcript names it. The wording is the perceiving body's, so it is declared here, with it.

    ``display_fn`` turns a value into the transcript's label (``None``: the contrib's default)."""

    key: str
    type: type
    serialize_fn: Callable[[Any, Any], Content]
    triggers_llm_call: bool = False
    trigger_condition_fn: Callable[[Any, Any], bool] | None = None
    ttl: timedelta | None = None
    display_fn: Callable[[Any], str] | None = None


class Input(Protocol):
    """A body's own perception — the mirror of a Command: the body *describes* what it delivers
    (``entries``), the lifecycle registers the entries and runs the producer (specs/embodiment.md,
    "``inputs()`` is the mirror of ``commands()``"). Both coroutines run on the app loop: ``start``
    once the agent runs (so it may deliver at once), ``stop`` before the body exits."""

    @property
    def entries(self) -> Sequence[InputEntry]: ...

    async def start(self, deliver: Deliver) -> None: ...

    async def stop(self) -> None: ...


class Embodiment(Protocol):
    """The body the agent has in a deployment.

    Degradation is per component, never per embodiment: ``enter()`` raises only when the body itself
    is unavailable, and anything narrower (a voice's key unset, an optional dependency absent) is
    one line in ``missing`` while the corresponding member degrades.
    """

    # "computer", "Reachy Mini": the page title, and the banner line when `enter` raises.
    display_name: str
    # Degrade lines gathered while entering, one per component; the banner shows them verbatim.
    missing: list[str]

    async def enter(self) -> None:
        """Connect / build, on the app loop. A raise means "no embodiment"."""
        ...

    async def exit(self) -> None:
        """Release, on the app loop, after ASR and vision have stopped."""
        ...

    @property
    def voice(self) -> Voice | None:
        """The output Command; ``None`` ⇒ none is set (the model's free text is then its voice)."""
        ...

    @property
    def mic(self) -> AudioSource | None:
        """The ASR engine's audio source; ``None`` ⇒ the engine opens its own default device."""
        ...

    @property
    def mic_error(self) -> str | None:
        """Why capture died on its own; ``None`` while healthy."""
        ...

    def camera(self, *, fps: float) -> CameraFeed:
        """The gesture input's upstream: a latest-value frame slot with ``open``/``close`` and a
        ``capture_error``. ``fps`` is the rate to deliver frames at *if the body can pace them* (a
        webcam); a camera running at its own rate (the robot's feed) ignores it — the stages pace
        themselves."""
        ...

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """The action Commands to register with the agent (empty when there is nothing to act with)."""
        ...

    def inputs(self) -> Sequence[Input]:
        """The body's own perceptions, each with the World entries it writes (empty when it
        perceives nothing of its own) — registered and run by the lifecycle, never written by the
        body itself."""
        ...

    @property
    def prompt_addendum(self) -> str:
        """Persona text appended to the resolved prompt at startup (``""`` for none)."""
        ...
