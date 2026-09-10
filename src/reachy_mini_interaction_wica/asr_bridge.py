"""ASR→World bridge and the agent's dictation command.

The speech-in half of the app: it owns the live ``ASREngine``, turns its stream of speech *segments*
into World updates, and exposes the dictation control the agent can invoke. Interim segments only
feed a live display view (the agent is never triggered mid-utterance); a closed segment reaches the
agent, exactly once, via the injected ``on_final_segment`` callback. See specs/asr-bridge.md.
"""

from __future__ import annotations

from collections.abc import Callable

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment

OnFinalSegment = Callable[[str], None]


class AsrBridge:
    """Maps ASR segments onto the World and hosts the dictation command.

    Constructed with a live ``ASREngine`` and an injected ``on_final_segment`` callback (the app
    wires this to ``world.update("speech_input", transcript)``). The callback is injected rather than
    the World itself, so the bridge is decoupled from WICA and unit-testable with a recording callback
    and a fake engine.
    """

    def __init__(self, engine: ASREngine, *, on_final_segment: OnFinalSegment) -> None:
        self._engine = engine
        self._on_final_segment = on_final_segment
        self._display_text = ""
        engine.on_speech_segment = self._on_segment

    async def start(self) -> None:
        """Start the engine in its default ``utterance`` mode (one segment per utterance).

        The app never auto-starts dictation and does not honor the ASR config's ``auto_start`` /
        ``auto_start_dictation`` signals: normal conversation stays in utterance mode until the agent
        asks otherwise.
        """
        await self._engine.start()

    async def stop(self) -> None:
        """Stop the engine."""
        await self._engine.stop()

    async def _on_segment(self, segment: SpeechSegment) -> None:
        """Handle one segment change (set as ``engine.on_speech_segment``).

        Interim segments only update the live display text — the World is untouched, so the agent is
        not triggered. A final segment calls ``on_final_segment`` with its transcript (once, for every
        final, including an empty one) and then clears the display text back to ``""``. Only in-memory
        state is mutated and the callback is synchronous, so this never blocks the ASR loop.
        """
        if not segment.is_final:
            self._display_text = segment.transcript
            return
        self._on_final_segment(segment.transcript)
        self._display_text = ""

    @property
    def display_text(self) -> str:
        """The current interim transcript for the UI; ``""`` after a final segment closes."""
        return self._display_text

    def status(self) -> dict:
        """Compose the engine's accessors into the state the ASR panel renders."""
        return {
            "running": self._engine.status()["running"],
            "dictating": self._engine.dictating,
            "segmentation_mode": self._engine.segmentation_mode,
        }

    async def start_dictation(self, segmentation_mode: str | None = None) -> str:
        """Switch speech capture to dictation mode for one longer passage.

        Use when the user wants to dictate free-form text rather than converse in short
        turns. Speech keeps flowing to you as usual, but the current utterance is only
        delivered once the user pauses (or says the trigger word), instead of after every
        sentence. Dictation ends automatically after that one passage.
        """
        try:
            await self._engine.start_dictation(
                end_on_final_segment=True, segmentation_mode=segmentation_mode
            )
        except ValueError as exc:
            return f"Could not start dictation: {exc}"
        return f"Dictation started ({self._engine.segmentation_mode} mode)."

    async def stop_dictation(self) -> str:
        """Leave dictation immediately, without waiting for the passage to close.

        Reverts speech capture to normal one-utterance-per-turn conversation. Backs the UI stop
        button; also a valid agent command, though the common path ends on its own.
        """
        try:
            await self._engine.stop_dictation()
        except ValueError as exc:
            return f"Could not stop dictation: {exc}"
        return "Dictation stopped."
