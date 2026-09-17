"""TTS output: the agent's voice as a WICA output Command.

The speech-out half of the app: ``VoiceOutput`` holds the one shared ``ReachyMiniApi`` and exposes
``say(text)``, the coroutine registered as WICA's output Command. Everything the agent chooses to say
to the user is spoken **through the robot** (via ``api.say``), while the model's free text stays
private reasoning on the output sink. Playing through the robot is required for the on-board XVF3800
echo cancellation to work (see reachy-mini-bridge specs/audio.md), so the app owns no local
``TTSEngine`` or speaker; ``say`` is a thin, non-raising wrapper over ``api.say``. See
specs/tts-output.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reachy_mini_bridge.api import ReachyMiniApi


class VoiceOutput:
    """The agent's user-facing voice, registered as WICA's ``output_command``.

    Constructed with the app's single ``ReachyMiniApi`` (built with the configured synthesizer and
    robot backend, and shared with the robot-control commands), so there is one robot connection and
    one media session. ``say`` is a bound method, so registering it preserves its name, signature, and
    docstring — the docstring below is what the model sees as the voice's description.
    """

    def __init__(self, api: ReachyMiniApi) -> None:
        self._api = api

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the person, out of the robot's speaker.

        This is your voice: whatever you want the person to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
        if not text.strip():
            # Guard before touching the robot: an empty utterance is nothing to say. Mirrors the
            # non-raising Command contract — the agent always gets a readable string, never an error.
            return "TTS error: text must not be empty"
        try:
            await self._api.say(text)
        except Exception as exc:  # noqa: BLE001 - any synth/media/bridge failure becomes a readable reply
            return f"TTS error: {exc}"
        return "OK"
