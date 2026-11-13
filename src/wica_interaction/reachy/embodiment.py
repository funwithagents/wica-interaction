"""The agent embodied in a Reachy Mini: one ``ReachyMiniBridge`` behind the voice, the mic and the camera.

This embodiment gathers everything robot-specific behind the [Embodiment](../embodiment.py)
protocol the shared lifecycle is written against: ``say`` out of the robot's speaker,
``RobotMicSource`` over its echo-cancelled mic (one subscriber of ``bridge.mic``, the bridge's one
reader of the microphone), ``RobotCameraFeed`` over its camera feed
(``bridge.camera``, the bridge's one reader of the camera), and the action Commands of
[robot_control.py](robot_control.py) — following a person with its head among them, when it sees
people: with a face detector configured, ``UsersInput`` ([users.py](users.py)) tells the agent who
arrives and leaves, who is present, who is in its personal space and whom it follows, through
``inputs()``. All of it rides the *same*
bridge instance — one robot connection,
one media session, one mic feed, one camera feed — built and started in ``enter()`` on the app loop: the
bridge's session is its ``start()`` / ``stop()`` pair, which is what a host with lifecycle hooks of
its own (this protocol's ``enter`` / ``exit``) drives, instead of the ``async with`` sugar over it.

Audio on Reachy Mini is a daemon-owned pipeline with an XMOS XVF3800 doing echo cancellation in
firmware, which works only if playback goes *through* it: that is why speech is played through the
robot and the mic read from it, and why this embodiment needs none of the computer's audio
processing. See specs/reachy-embodiment.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

from asr_engine.audio import AudioSource
from reachy_mini_bridge import CameraFrame, ReachyMiniBridge
from wica import Command

from wica_interaction.embodiment import Input, Voice
from wica_interaction.gesture_input import CameraFeed
from wica_interaction.reachy.config import ReachyConfig
from wica_interaction.reachy.robot_control import RobotControl, emotions_prompt_section
from wica_interaction.reachy.users import UsersInput, users_prompt_section

logger = logging.getLogger(__name__)

# Upper bound on buffered mic chunks. The engine drains in real time; if it stalls, the oldest audio
# is dropped (stale speech is useless) instead of PCM piling up at ~32 KB/s (16 kHz mono int16). A
# chunk is one robot capture block — 10 ms (160 frames) on the bridge's fake backend — so 200 chunks
# is ~2 s of backlog; a backend with larger blocks only makes that window longer.
_MIC_QUEUE_MAXSIZE = 200

# capture_error when the robot's mic iterator finishes on its own (the media session closed under us).
_STREAM_ENDED = "mic stream ended"

# Appended to a failed connection's message (the "no embodiment" banner line) on a backend that
# needs something outside this process — a robot, a daemon, a simulator.
_OFFLINE_HINT = (
    '(to develop with no robot, set reachy.backend to "fake" and remove the reachy.daemon '
    'block, or to "sim" for the simulator)'
)


class RobotMicSource:
    """Feeds the ``ASREngine`` the robot's echo-cancelled mic instead of a local device.

    Implements the ASR engine's synchronous ``AudioSource`` protocol (``start() -> Queue``, ``stop()``
    — see asr-engine specs/audio-source.md): the engine calls ``start()`` (not awaited) from inside its
    start coroutine to obtain the queue it drains, and ``stop()`` on teardown. ``bridge.audio_input()`` is
    one subscriber of the bridge's mic feed — every chunk, in order, never split with another consumer
    — and already yields int16 LE mono PCM at ``bridge.mic_sample_rate`` (16 kHz) — the streaming-ASR contract —
    so the pump just iterates and enqueues each chunk; no resampling or reformatting. The queue is
    bounded and drops its oldest chunk when full (see ``_MIC_QUEUE_MAXSIZE``). If the stream raises or
    ends on its own, the pump logs it and records why in ``capture_error`` — so a dead mic is visible in
    the ASR status rather than passing for silence. The source shares the one ``ReachyMiniBridge`` the
    embodiment builds (same connection/media session as ``say``).
    """

    def __init__(
        self, bridge: ReachyMiniBridge, *, maxsize: int = _MIC_QUEUE_MAXSIZE
    ) -> None:
        self._bridge = bridge
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
            async for chunk in self._bridge.audio_input():
                if queue.full():
                    queue.get_nowait()  # the engine fell behind: drop the stalest chunk
                queue.put_nowait(chunk)
        except Exception as exc:  # any stream failure is surfaced, not lost
            logger.exception("robot mic stream failed")
            self._error = f"mic stream failed: {exc}"
            return
        logger.warning("robot mic stream ended; ASR is receiving no audio")
        self._error = _STREAM_ENDED


class RobotCameraFeed:
    """The robot camera as a ``CameraFeed``: a thin adapter over the bridge's ``bridge.camera``.

    The bridge reads its camera in exactly one place — ``bridge.camera``, a feed thread the session
    owns, publishing the newest ``CameraFrame`` (``frame_id``, ``ts``, the BGR image, ``head_pose``)
    for any number of consumers to sample without stealing frames from one another (upstream's
    one-shot ``get_frame()`` hands each frame out once). The bridge's own face detection samples that
    feed; so does this app's hand stage, through this adapter: ``latest()`` and ``published_count``
    forward to it, nothing is copied, paced or re-numbered, and reading is an instant, thread-safe
    call from the hand stage's own thread — no hop to the app loop, nothing async.

    ``open()``/``close()`` are no-ops: the feed's lifecycle is the session's (started by the
    bridge's ``start()``, stopped and reset to ``None`` by its ``stop()``), and stopping it here would blind
    the bridge's face detection and head tracking. ``capture_error`` is always ``None`` — the feed
    has no failure of its own to report (a failing read is logged by the bridge and the last frame
    stays published); a camera that goes quiet shows through the gesture input's stall line instead.
    See specs/reachy-embodiment.md, "``RobotCameraFeed``".
    """

    def __init__(self, bridge: ReachyMiniBridge) -> None:
        self._bridge = bridge

    def open(self) -> None:
        """Nothing: the feed is the session's, started at enter."""

    def close(self) -> None:
        """Nothing: never stops the bridge's feed."""

    def latest(self) -> CameraFrame | None:
        return self._bridge.camera.latest()

    @property
    def published_count(self) -> int:
        return self._bridge.camera.published_count

    @property
    def capture_error(self) -> str | None:
        return None


class ReachyEmbodiment:
    """The agent embodied in a Reachy Mini (specs/reachy-embodiment.md).

    Construction keeps the config (and the injected fake bridge, if any) and does no I/O; everything
    comes up in ``enter()``, on the app loop. Every other member is called only after a successful
    ``enter`` (specs/embodiment.md, "Member by member"), which is what the ``assert``s below state.
    Degradation is per component: ``enter`` raises only when the robot itself cannot be connected —
    a voice that failed to build or an unreadable emotion library is one line in ``missing`` and the
    rest of the body stays up.
    """

    display_name = "Reachy Mini"

    def __init__(
        self, config: ReachyConfig, *, bridge: ReachyMiniBridge | None = None
    ) -> None:
        self._config = config
        self._bridge: ReachyMiniBridge | None = bridge
        self._injected = bridge is not None
        self._mic: RobotMicSource | None = None
        self._emotions: tuple[str, ...] | None = None
        self._users: UsersInput | None = None
        self.missing: list[str] = []

    async def enter(self) -> None:
        """Bring the robot up: the bridge's session (``start()`` — daemon, connection, media
        session, camera feed, motion), the voice's health, the emotion names, the mic source. Any
        exception from ``start()`` propagates — that is the "no embodiment" degrade the lifecycle
        catches."""
        bridge = (
            self._bridge
            if self._bridge is not None
            else ReachyMiniBridge(self._config.reachy)
        )
        # Construction never connects; start() does, so any raise here (an upstream reachy_mini
        # error, the bridge's DaemonError/BridgeError) means "no embodiment". The offline hint rides
        # the message, since the banner shows it verbatim and only this body knows its backends.
        # A failed start() has already unwound whatever it brought up, so there is nothing to stop.
        try:
            await bridge.start()
        except Exception as exc:
            if self._config.reachy.backend == "fake":
                raise
            raise RuntimeError(f"{exc} {_OFFLINE_HINT}") from exc
        self._bridge = bridge
        if not self._injected:
            # The voice degrades independently of the rest of the robot: the bridge records a failed
            # synthesizer build rather than raising. Skipped for an injected fake (it carries its
            # own `say`, with nothing to report here).
            if self._config.reachy.tts is None:
                self.missing.append(
                    "TTS: no reachy.tts block configured (say is unavailable)"
                )
            elif bridge.synthesizer_error is not None:
                self.missing.append(f"TTS: {bridge.synthesizer_error}")
        # One fetch per session, so the model knows the names before its first turn. A failed
        # library load (on the real backend, a HuggingFace dataset read) degrades to a banner line
        # and no names: no emotion section in the prompt, and play_emotion keeps its plain docstring.
        try:
            self._emotions = tuple(await bridge.list_emotions())
        except Exception as exc:  # noqa: BLE001 - a library load failure degrades, never crashes
            self.missing.append(f"Robot: emotions unavailable: {exc}")
        self._mic = RobotMicSource(bridge)
        if self._config.reachy.face_detection.detector is not None:
            await self._see_users(bridge)

    async def _see_users(self, bridge: ReachyMiniBridge) -> None:
        """With a face detector configured: detection on for the whole session, and the users
        input built (the lifecycle starts it once the agent runs). The users the agent is told
        about are the bridge's users present, which exist only while detection runs — tracking
        alone would not keep it running: the agent's ``stop_tracking`` would stop the loop and every
        user would leave. A failure costs the users (no input, no tracking Commands), never the
        rest of the robot."""
        try:
            if not self._config.reachy.face_detection.enabled:
                logger.info(
                    "face detection turned on for the session: the users input needs it "
                    "(reachy.face_detection.enabled is false in the config)"
                )
                await bridge.set_face_detection(True)
            self._users = UsersInput(bridge)
        except Exception as exc:  # noqa: BLE001 - the users degrade alone, never the robot
            self.missing.append(f"Users: {exc}")

    async def exit(self) -> None:
        """Stop the bridge's session: the motion loop, the camera feed, the media session, the
        robot connection and the managed daemon (a no-op on a bridge that never started)."""
        if self._bridge is not None:
            await self._bridge.stop()

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
        assert self._bridge is not None
        if not text.strip():
            # Guard before touching the robot: an empty utterance is nothing to say. Mirrors the
            # non-raising Command contract — the agent always gets a readable string, never an error.
            return "TTS error: text must not be empty"
        try:
            await self._bridge.say(text)
        except Exception as exc:  # noqa: BLE001 - any synth/media/bridge failure becomes a readable reply
            return f"TTS error: {exc}"
        return "OK"

    @property
    def mic(self) -> AudioSource | None:
        return self._mic

    @property
    def mic_error(self) -> str | None:
        return self._mic.capture_error if self._mic is not None else None

    def camera(self, *, fps: float) -> CameraFeed:
        """The bridge's camera feed behind ``RobotCameraFeed``. ``fps`` is ignored: the feed runs at
        the daemon's rate (10 fps on a local daemon), the gesture stages pace themselves."""
        assert self._bridge is not None
        return RobotCameraFeed(self._bridge)

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """The robot's action Commands, over the same bridge as the voice: the emotion (its description
        naming the names fetched at enter), the motors, and — when it sees people — tracking."""
        assert self._bridge is not None
        return RobotControl(
            self._bridge, emotions=self._emotions, tracking=self._users is not None
        ).commands()

    def inputs(self) -> Sequence[Input]:
        """The users in view, when the robot sees people (a face detector configured)."""
        return (self._users,) if self._users is not None else ()

    @property
    def prompt_addendum(self) -> str:
        """The emotion names, when fetched, then the users section, when the robot sees people."""
        sections = []
        if self._emotions:
            sections.append(emotions_prompt_section(self._emotions))
        if self._users is not None:
            sections.append(users_prompt_section())
        return "".join(sections)
