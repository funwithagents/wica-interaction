"""The agent embodied in a Reachy Mini: one ``ReachyMiniApi`` behind the voice, the mic and the camera.

This embodiment gathers everything robot-specific behind the [Embodiment](../embodiment.py)
protocol the shared lifecycle is written against: ``say`` out of the robot's speaker,
``RobotMicSource`` over its echo-cancelled mic, ``RobotCameraSource`` over its camera, and the
action Commands of [robot_control.py](robot_control.py). All four ride the *same* api instance —
one robot connection, one media session — built and entered in ``enter()`` on the app loop.

Audio on Reachy Mini is a daemon-owned pipeline with an XMOS XVF3800 doing echo cancellation in
firmware, which works only if playback goes *through* it: that is why speech is played through the
robot and the mic read from it, and why this embodiment needs none of the computer's audio
processing. See specs/reachy-embodiment.md.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from asr_engine.audio import AudioSource
from reachy_mini_bridge.api import ReachyMiniApi
from wica import Command

from wica_interaction.embodiment import Voice
from wica_interaction.gesture_input import CameraSource
from wica_interaction.reachy.config import ReachyConfig
from wica_interaction.reachy.robot_control import RobotControl, emotions_prompt_section

logger = logging.getLogger(__name__)

# Upper bound on buffered mic chunks. The engine drains in real time; if it stalls, the oldest audio
# is dropped (stale speech is useless) instead of PCM piling up at ~32 KB/s (16 kHz mono int16). A
# chunk is one robot capture block — 10 ms (160 frames) on the bridge's fake backend — so 200 chunks
# is ~2 s of backlog; a backend with larger blocks only makes that window longer.
_MIC_QUEUE_MAXSIZE = 200

# capture_error when the robot's mic iterator finishes on its own (the media session closed under us).
_STREAM_ENDED = "mic stream ended"

# Bound on one camera poll hop to the loop. The bridge runs the SDK read off the loop, so a poll is
# normally a few ms; a session that stalls longer than this is reported as a capture error rather
# than wedging the capture thread forever.
_POLL_TIMEOUT_S = 5.0


class RobotMicSource:
    """Feeds the ``ASREngine`` the robot's echo-cancelled mic instead of a local device.

    Implements the ASR engine's synchronous ``AudioSource`` protocol (``start() -> Queue``, ``stop()``
    — see asr-engine specs/audio-source.md): the engine calls ``start()`` (not awaited) from inside its
    start coroutine to obtain the queue it drains, and ``stop()`` on teardown. ``api.audio_input()``
    already yields int16 LE mono PCM at ``api.mic_sample_rate`` (16 kHz) — the streaming-ASR contract —
    so the pump just iterates and enqueues each chunk; no resampling or reformatting. The queue is
    bounded and drops its oldest chunk when full (see ``_MIC_QUEUE_MAXSIZE``). If the stream raises or
    ends on its own, the pump logs it and records why in ``capture_error`` — so a dead mic is visible in
    the ASR status rather than passing for silence. The source shares the one ``ReachyMiniApi`` the
    embodiment builds (same connection/media session as ``say``).
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


class RobotCameraSource:
    """The robot camera as a ``CameraSource``: a paced poll of the bridge's ``get_camera_frame()``
    on the app loop.

    ``read()`` runs on the ``StreamProvider``'s capture thread and hops each poll to the loop the api
    was entered on. It paces itself to ``fps`` (the provider calls ``read()`` back-to-back), skips a
    ``None`` frame (nothing buffered yet — the provider would otherwise end its loop for good), and
    keeps polling through a failing verb, recording why in ``capture_error`` (cleared by the next
    success) so a dead camera shows in the UI instead of silently killing vision. Only ``close()``
    makes ``read()`` return ``None``.
    """

    def __init__(
        self, api: ReachyMiniApi, loop: asyncio.AbstractEventLoop, *, fps: float
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self._api = api
        self._loop = loop
        self._interval = 1.0 / fps
        self._next_poll = time.monotonic()
        self._closed = threading.Event()
        self._error: str | None = None

    @property
    def capture_error(self) -> str | None:
        """Why the last poll failed; ``None`` while healthy (or before the first poll)."""
        return self._error

    def open(self) -> None:
        """Called by the provider on every ``start()``: (re)arm the source so a provider restarted
        after a ``stop()`` polls again. Nothing to open on the robot side — the api's media session
        is the embodiment's, entered long before."""
        self._closed.clear()
        self._next_poll = time.monotonic()

    def read(self) -> np.ndarray | None:
        """The next camera frame, or ``None`` once closed. Never ``None`` for "no frame yet"."""
        while not self._closed.is_set():
            # Pace: one poll per interval, measured from the previous poll — a frame is returned
            # the moment it is read (no added latency), and a `None` just waits for the next slot.
            remaining = self._next_poll - time.monotonic()
            if remaining > 0:
                self._closed.wait(remaining)
                continue
            self._next_poll = time.monotonic() + self._interval
            frame = self._poll()
            if frame is not None:
                return frame
        return None

    def _poll(self) -> np.ndarray | None:
        try:
            frame = asyncio.run_coroutine_threadsafe(
                self._api.get_camera_frame(), self._loop
            ).result(_POLL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - any failure is surfaced, and polling goes on
            message = f"camera read failed: {exc}"
            if self._error != message:  # log the transition, not every poll
                logger.warning(
                    "robot camera read failed; vision is receiving no frames: %s", exc
                )
            self._error = message
            return None
        if self._error is not None:
            logger.info("robot camera read recovered")
            self._error = None
        return frame

    def close(self) -> None:
        self._closed.set()

    def fps(self) -> float:
        """The nominal rate, per the ``FrameSource`` protocol: the poll rate this source paces
        itself to (the robot camera reports none of its own through the bridge). The rate actually
        achieved is the provider's ``published_count``, metered like every other node."""
        return 1.0 / self._interval


class ReachyEmbodiment:
    """The agent embodied in a Reachy Mini (specs/reachy-embodiment.md).

    Construction keeps the config (and the injected fake api, if any) and does no I/O; everything
    comes up in ``enter()``, on the app loop. Every other member is called only after a successful
    ``enter`` (specs/embodiment.md, "Member by member"), which is what the ``assert``s below state.
    Degradation is per component: ``enter`` raises only when the robot itself cannot be connected —
    a voice that failed to build or an unreadable emotion library is one line in ``missing`` and the
    rest of the body stays up.
    """

    display_name = "Reachy Mini"

    def __init__(
        self, config: ReachyConfig, *, api: ReachyMiniApi | None = None
    ) -> None:
        self._config = config
        self._api: ReachyMiniApi | None = api
        self._injected = api is not None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mic: RobotMicSource | None = None
        self._emotions: tuple[str, ...] | None = None
        self.missing: list[str] = []

    async def enter(self) -> None:
        """Bring the robot up: the api's daemon/connection/media session, the voice's health, the
        emotion names, the mic source. Any exception from ``__aenter__`` propagates — that is the
        "no embodiment" degrade the lifecycle catches."""
        self._loop = asyncio.get_running_loop()
        api = self._api if self._api is not None else ReachyMiniApi(self._config.reachy)
        # Construction never connects; __aenter__ does, so any raise here (an upstream reachy_mini
        # error, the bridge's DaemonError/BridgeError) means "no embodiment".
        await api.__aenter__()
        self._api = api
        if not self._injected:
            # The voice degrades independently of the rest of the robot: the bridge records a failed
            # synthesizer build rather than raising. Skipped for an injected fake (it carries its
            # own `say`, with nothing to report here).
            if self._config.reachy.tts is None:
                self.missing.append(
                    "TTS: no reachy.tts block configured (say is unavailable)"
                )
            elif api.synthesizer_error is not None:
                self.missing.append(f"TTS: {api.synthesizer_error}")
        # One fetch per session, so the model knows the names before its first turn. A failed
        # library load (on the real backend, a HuggingFace dataset read) degrades to a banner line
        # and no names: no emotion section in the prompt, and play_emotion keeps its plain docstring.
        try:
            self._emotions = tuple(await api.list_emotions())
        except Exception as exc:  # noqa: BLE001 - a library load failure degrades, never crashes
            self.missing.append(f"Robot: emotions unavailable: {exc}")
        self._mic = RobotMicSource(api)

    async def exit(self) -> None:
        """Close the media session, the robot connection and the managed daemon."""
        if self._api is not None:
            await self._api.__aexit__(None, None, None)

    @property
    def voice(self) -> Voice | None:
        """The robot's voice — always set once it came up; ``say`` reports the TTS error itself when
        the bridge built no synthesizer."""
        return self.say

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the person, out of the robot's speaker.

        This is your voice: whatever you want the person to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
        assert self._api is not None
        if not text.strip():
            # Guard before touching the robot: an empty utterance is nothing to say. Mirrors the
            # non-raising Command contract — the agent always gets a readable string, never an error.
            return "TTS error: text must not be empty"
        try:
            await self._api.say(text)
        except Exception as exc:  # noqa: BLE001 - any synth/media/bridge failure becomes a readable reply
            return f"TTS error: {exc}"
        return "OK"

    @property
    def mic(self) -> AudioSource | None:
        return self._mic

    @property
    def mic_error(self) -> str | None:
        return self._mic.capture_error if self._mic is not None else None

    def camera(self, *, fps: float) -> CameraSource:
        assert self._api is not None and self._loop is not None
        return RobotCameraSource(self._api, self._loop, fps=fps)

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """The robot's action Commands, over the same api as the voice: the emotion (its description
        naming the names fetched at enter) and the motors."""
        assert self._api is not None
        return RobotControl(self._api, emotions=self._emotions).commands()

    @property
    def prompt_addendum(self) -> str:
        return emotions_prompt_section(self._emotions) if self._emotions else ""
