"""ASR→World bridge and the agent's dictation command.

The speech-in half of the app: it owns the live ``ASREngine`` — fed whatever audio source the
embodiment provides (the robot's echo-cancelled stream, the computer's processed or raw mic) —
turns its stream of speech *segments* into World updates, and exposes the dictation control the
agent can invoke. Interim segments only feed a live display view (the agent is never triggered
mid-utterance); a closed segment reaches the agent, exactly once, via the injected
``on_final_segment`` callback. The bridge never sees the source itself: what it is given is
``mic_error``, a callable reporting the embodiment's capture health, because the engine can keep
reporting ``running`` while its stream has died. ``start_dictation``'s reply says how the passage
it opens will end (a pause, or one of the configured trigger words) — for the model's own
understanding, not to be announced — so the personas never hard-code a segmentation mode the config
can change. See specs/asr-bridge.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment

logger = logging.getLogger(__name__)

OnFinalSegment = Callable[[str], None]
# The embodiment's capture health, read on every status() poll (specs/embodiment.md, `mic_error`).
MicError = Callable[[], str | None]


class AsrBridge:
    """Maps ASR segments onto the World and hosts the dictation command.

    Constructed with a live ``ASREngine`` and an injected ``on_final_segment`` callback (the app
    wires this to ``world.update("speech_input", transcript)``). The callback is injected rather than
    the World itself, so the bridge is decoupled from WICA and unit-testable with a recording callback
    and a fake engine. ``mic_error`` reads the capture health of the source the engine was built
    over — the embodiment's, since the mic is its choice and the engine keeps its source private;
    ``None`` when the caller gave none. ``trigger_words`` are the ASR config's
    (``segmentation.trigger_words``) — the engine keeps them private, and ``start_dictation`` names
    them in its reply when the passage will end on one.
    """

    def __init__(
        self,
        engine: ASREngine,
        *,
        on_final_segment: OnFinalSegment,
        mic_error: MicError | None = None,
        trigger_words: Sequence[str] = (),
    ) -> None:
        self._engine = engine
        self._on_final_segment = on_final_segment
        self._mic_error = mic_error
        self._trigger_words = tuple(trigger_words)
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
        """Compose the engine's accessors — plus the embodiment's capture health — into the state the
        ASR panel renders. ``capture_error`` is set when the mic stream died even though the engine
        still reports ``running`` (it is then hearing nothing)."""
        return {
            "running": self._engine.status()["running"],
            "dictating": self._engine.dictating,
            "segmentation_mode": self._engine.segmentation_mode,
            "capture_error": self._mic_error() if self._mic_error is not None else None,
        }

    async def start_dictation(self, segmentation_mode: str | None = None) -> str:
        """Switch speech capture to dictation mode for one longer passage.

        Use when the user wants to dictate free-form text rather than converse in short
        turns. Speech keeps flowing to you as usual, but it is delivered as one passage
        instead of after every sentence. The reply tells you what ends the passage (a pause,
        or a word the user says). Dictation ends automatically after that one passage.
        """
        try:
            await self._engine.start_dictation(
                end_on_final_segment=True, segmentation_mode=segmentation_mode
            )
        except ValueError as exc:
            return f"Could not start dictation: {exc}"
        mode = self._engine.segmentation_mode
        return f"Dictation started ({mode} mode): {self._how_it_ends(mode)}"

    def _how_it_ends(self, mode: str) -> str:
        """What closes the passage in *mode* — the one fact the model cannot know from its persona,
        since the mode is the config's (``dictation_default_segmentation_mode``) or the call's."""
        if mode == "trigger_word":
            # No timer runs in this mode: only a trigger word closes the segment.
            words = ", ".join(f'"{word}"' for word in self._trigger_words)
            named = f" one of these words: {words}" if words else " the trigger word"
            return (
                f"the passage is delivered when the person says{named} — "
                "a pause does not end it."
            )
        if mode == "timeout":
            return "the passage is delivered once the person stops speaking for a few seconds."
        return "the passage is delivered after the person's next sentence."

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
