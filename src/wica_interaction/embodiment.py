"""The ``Embodiment`` protocol: what the shared lifecycle (system.py) needs from a body.

The app is one program that can be embodied in different things — the computer it runs on, a Reachy
Mini, later a Nao. Everything WICA-side is the same whatever the body; what varies is only **where
audio comes in and goes out, what the agent can act with, and what it sees through**. This module
names that variation, and nothing else: it imports no robot SDK and no audio engine.

A ``typing.Protocol``, not a base class, so each real embodiment is checked structurally by pyright
at the ``build_system(config, embodiment)`` call — an implementation that drifts from this surface
is a type error in its own package — and the lifecycle tests run over a ``FakeEmbodiment`` that
imports neither. See specs/embodiment.md for who calls each member and when.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from asr_engine.audio import AudioSource
from wica import Command

from wica_interaction.gesture_input import CameraSource

# The `say` output Command: non-raising, "OK" on success, "TTS error: …" otherwise, awaiting real
# playback so the Command stays `running` for the duration of the speech. Registered directly, so
# its name, signature and docstring are what the model reads as the voice's description.
Voice = Callable[[str], Awaitable[str]]


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

    def camera(self, *, fps: float) -> CameraSource:
        """A paced ``FrameSource`` reporting its own ``capture_error``, for the gesture input."""
        ...

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """The action Commands to register with the agent (empty when there is nothing to act with)."""
        ...

    @property
    def prompt_addendum(self) -> str:
        """Persona text appended to the resolved prompt at startup (``""`` for none)."""
        ...
