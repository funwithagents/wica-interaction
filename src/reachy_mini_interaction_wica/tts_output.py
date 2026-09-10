"""TTS output: the agent's voice as a WICA output Command.

The speech-out half of the app: ``VoiceOutput`` owns a ``TTSEngine`` and exposes ``say(text)``, the
coroutine registered as WICA's output Command. Everything the agent chooses to say to the user is
spoken here, while the model's free text stays private reasoning on the output sink. ``say`` delegates
to the sibling ``TTSTools(engine).say`` for its guarded, non-raising contract (``"OK"`` on success, a
readable ``"TTS error: <message>"`` otherwise) rather than calling ``engine.say`` directly. See
specs/tts-output.md.
"""

from __future__ import annotations

from tts_engine.engine import TTSEngine
from tts_engine.tools import TTSTools


class VoiceOutput:
    """The agent's user-facing voice, registered as WICA's ``output_command``.

    Constructed with a live ``TTSEngine``; wraps it in ``TTSTools`` once so ``say`` is a thin,
    non-raising delegate. ``say`` is a bound method, so registering it preserves its name, signature,
    and docstring — the docstring below is what the model sees as the voice's description.
    """

    def __init__(self, engine: TTSEngine) -> None:
        self._tools = TTSTools(engine)

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the user through the machine's speakers.

        This is your voice: whatever you want the user to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
        return await self._tools.say(text)
