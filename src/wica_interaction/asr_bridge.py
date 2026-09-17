"""ASR→World bridge, the robot mic source, and the agent's dictation command.

The speech-in half of the app: it owns the live ``ASREngine`` — fed the robot's echo-cancelled mic
stream via ``RobotMicSource`` — turns its stream of speech *segments* into World updates, and exposes
the dictation control the agent can invoke. Interim segments only feed a live display view (the agent
is never triggered mid-utterance); a closed segment reaches the agent, exactly once, via the injected
``on_final_segment`` callback. See specs/asr-bridge.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment

if TYPE_CHECKING:
    from reachy_mini_bridge.api import ReachyMiniApi

logger = logging.getLogger(__name__)

OnFinalSegment = Callable[[str], None]

# Upper bound on buffered mic chunks. The engine drains in real time; if it stalls, the oldest audio
# is dropped (stale speech is useless) instead of PCM piling up at ~32 KB/s (16 kHz mono int16). A
# chunk is one robot capture block — 10 ms (160 frames) on the bridge's fake backend — so 200 chunks
# is ~2 s of backlog; a backend with larger blocks only makes that window longer.
_MIC_QUEUE_MAXSIZE = 200

# capture_error when the robot's mic iterator finishes on its own (the media session closed under us).
_STREAM_ENDED = "mic stream ended"


class RobotMicSource:
    """Feeds the ``ASREngine`` the robot's echo-cancelled mic instead of a local device.

    Implements the ASR engine's synchronous ``AudioSource`` protocol (``start() -> Queue``, ``stop()``
    — see asr-engine specs/audio-source.md): the engine calls ``start()`` (not awaited) from inside its
    start coroutine to obtain the queue it drains, and ``stop()`` on teardown. ``api.audio_input()``
    already yields int16 LE mono PCM at ``api.mic_sample_rate`` (16 kHz) — the streaming-ASR contract —
    so the pump just iterates and enqueues each chunk; no resampling or reformatting. The queue is
    bounded and drops its oldest chunk when full (see ``_MIC_QUEUE_MAXSIZE``). If the stream raises or
    ends on its own, the pump logs it and records why in ``capture_error`` — so a dead mic is visible in
    the ASR status rather than passing for silence. The source shares the one ``ReachyMiniApi`` the app
    builds (same connection/media session as ``say``).
    """

    def __init__(
        self, api: ReachyMiniApi, *, maxsize: int = _MIC_QUEUE_MAXSIZE
    ) -> None:
        self._api = api
        self._maxsize = maxsize
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

    def start(self) -> asyncio.Queue[bytes]:
        """Spawn the pump task on the running loop and return the queue the engine drains.

        Called synchronously by the engine from within its start coroutine, so a running loop exists.
        Resets the queue, task, and ``capture_error`` each call so the source is restartable (the
        engine may ``start()`` again after a ``stop()``); a still-live pump from a ``start()`` with no
        intervening ``stop()`` is cancelled first rather than orphaned.
        """
        loop = asyncio.get_running_loop()
        self.stop()
        self._error = None
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._task = loop.create_task(self._pump(self._queue))
        return self._queue

    def stop(self) -> None:
        """Cancel the pump task; leaves the source restartable."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    @property
    def capture_error(self) -> str | None:
        """Why capture stopped on its own (the stream raised or ended); ``None`` while healthy."""
        return self._error

    async def _pump(self, queue: asyncio.Queue[bytes]) -> None:
        """Drain the robot mic stream into *queue* until cancelled, dropping the oldest chunk when the
        queue is full. A raise or a normal end of the stream is logged and kept as ``capture_error``
        (cancellation by ``stop()`` is neither — it propagates untouched)."""
        try:
            async for chunk in self._api.audio_input():
                if queue.full():
                    queue.get_nowait()  # the engine fell behind: drop the stalest chunk
                queue.put_nowait(chunk)
        except Exception as exc:  # any stream failure is surfaced, not lost
            logger.exception("robot mic stream failed")
            self._error = f"mic stream failed: {exc}"
            return
        logger.warning("robot mic stream ended; ASR is receiving no audio")
        self._error = _STREAM_ENDED


class AsrBridge:
    """Maps ASR segments onto the World and hosts the dictation command.

    Constructed with a live ``ASREngine`` and an injected ``on_final_segment`` callback (the app
    wires this to ``world.update("speech_input", transcript)``). The callback is injected rather than
    the World itself, so the bridge is decoupled from WICA and unit-testable with a recording callback
    and a fake engine. ``mic`` is the same ``RobotMicSource`` the engine was built over, passed here so
    ``status()`` can report its capture health (the engine keeps its source private); ``None`` when the
    engine reads some other source.
    """

    def __init__(
        self,
        engine: ASREngine,
        *,
        on_final_segment: OnFinalSegment,
        mic: RobotMicSource | None = None,
    ) -> None:
        self._engine = engine
        self._on_final_segment = on_final_segment
        self._mic = mic
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
        """Compose the engine's accessors — plus the mic source's capture health — into the state the
        ASR panel renders. ``capture_error`` is set when the robot stream died even though the engine
        still reports ``running`` (it is then hearing nothing)."""
        return {
            "running": self._engine.status()["running"],
            "dictating": self._engine.dictating,
            "segmentation_mode": self._engine.segmentation_mode,
            "capture_error": self._mic.capture_error if self._mic is not None else None,
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
