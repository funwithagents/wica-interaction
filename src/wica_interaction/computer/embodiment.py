"""The agent embodied in the machine it runs on: a TTS voice, the local mic, a webcam.

Speech out through the local speakers via a [`tts-engine`](../../../../tts-engine) ``TTSEngine``,
speech in from the local microphone — optionally cleaned by
[audio processing](audio_processing.py) (WebRTC echo cancellation and noise suppression) — and the
person's hand signs read through a webcam. It has no actions beyond speaking. One implementation of
the [Embodiment](../embodiment.py) protocol; see specs/computer-embodiment.md.

Construction does no I/O and opens no device: ``enter()`` builds the coordinator (when a processing
feature is on) and the TTS engine, wiring the two together through the seams the engines already
expose — the sink for the far-end reference, the source for the cleaned mic — and recording each
failure in ``missing`` rather than raising, so a body with a degraded voice or no processing is
still a body. The voice keeps the one contract every embodiment's ``say`` has: it stays wired, and
with no engine it answers ``"TTS error: …"`` instead of disappearing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from asr_engine.audio import AudioSource
from tts_engine.engine import TTSEngine
from tts_engine.tools import TTSTools
from vision_modules import FrameSource, OpenCVSource
from wica import Command

from wica_interaction.computer.audio_processing import (
    AudioProcessingCoordinator,
    build_coordinator,
)
from wica_interaction.computer.config import ComputerConfig
from wica_interaction.embodiment import Voice
from wica_interaction.gesture_input import CameraFeed, StreamFeed

logger = logging.getLogger(__name__)


class WebcamSource:
    """The local webcam as a ``CameraSource``, over ``vision-modules``' own ``OpenCVSource`` (OpenCV
    is the library's ``opencv`` extra, pulled by the app's ``vision`` extra and imported only when
    the device is opened). The embodiment hands it to the gesture input behind a ``StreamFeed``.

    Obeys the three rules every camera source does (specs/gesture-input.md, "``StreamFeed``"): it
    paces itself to ``fps`` (the provider calls ``read()`` back-to-back, and a
    webcam delivering 30 fps into a 5 fps hand stage would be wasted captures); a device that cannot
    be opened — or a read that fails — is recorded in ``capture_error`` and retried at the poll rate,
    so an unplugged webcam shows on the Vision line and heals when plugged back in; and only
    ``close()`` makes ``read()`` return ``None``. A device that delivered frames and then yields
    none has ended (a video file played through, an image file read once, a webcam unplugged
    mid-stream: OpenCV returns no frame rather than raising), so it is reopened in the same poll;
    one that has delivered nothing yet is still warming up and is polled again as is. No
    mirroring: the classifier sees the frame as captured.
    """

    def __init__(
        self,
        device: int | str,
        *,
        fps: float,
        opener: Callable[[int | str], FrameSource] = OpenCVSource,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self._interval = 1.0 / fps
        self._inner = opener(device)  # constructed, not opened: open() does that
        self._opened = False
        # Whether the device delivered a frame since it was last opened: a missing frame after one
        # is the end of the stream, before one it is a webcam still warming up.
        self._delivered = False
        self._error: str | None = None
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._closed.set()

    @property
    def capture_error(self) -> str | None:
        """Why the device could not be opened or read; ``None`` while healthy."""
        with self._lock:
            return self._error

    def open(self) -> None:
        """Open the device — never raises: a device that is not there is a ``capture_error`` the UI
        shows, and ``read()`` keeps retrying the open at the poll rate."""
        self._closed.clear()
        self._open_device()

    def _open_device(self) -> None:
        if self._opened:
            return
        try:
            self._inner.open()
        except Exception as exc:  # noqa: BLE001 - a missing device is visible, not fatal
            self._record(f"camera open failed: {exc}")
            return
        # `close()` may have run while the open was in flight (a retry on the capture thread racing
        # teardown on the main one). It then found nothing to close, so the device opened here
        # would outlive the source: close it again instead of keeping it.
        with self._lock:
            closed_meanwhile = self._closed.is_set()
            self._opened = not closed_meanwhile
            self._delivered = False
        if closed_meanwhile:
            self._inner.close()
            return
        self._clear_error()

    def read(self) -> np.ndarray | None:
        """The next frame, or ``None`` once closed. Never ``None`` for "no frame yet"."""
        while not self._closed.is_set():
            deadline = time.monotonic() + self._interval
            self._open_device()  # a retry when a previous open failed; a no-op once open
            frame = self._read_frame() if self._opened else None
            if frame is not None:
                self._clear_error()
                # Pace: hold the rest of the interval before the provider asks again.
                self._closed.wait(max(deadline - time.monotonic(), 0.0))
                return frame
            self._closed.wait(max(deadline - time.monotonic(), 0.0))
        return None

    def _read_frame(self) -> np.ndarray | None:
        try:
            frame = self._inner.read()
        except Exception as exc:  # noqa: BLE001 - a device that went away is visible, not fatal
            self._record(f"camera read failed: {exc}")
            # The device is gone: drop it so the next poll re-opens rather than re-reading a dead one.
            self._drop_device()
            return None
        if frame is not None:
            self._delivered = True
            return frame
        if not self._delivered:
            return None  # still warming up: poll again
        # The stream ended: reopen now, so a file loops with no gap and a webcam that went away
        # shows its open failure.
        self._drop_device()
        self._open_device()
        if not self._opened:
            return None
        try:
            frame = self._inner.read()
        except Exception as exc:  # noqa: BLE001 - as above
            self._record(f"camera read failed: {exc}")
            self._drop_device()
            return None
        if frame is not None:
            self._delivered = True
        return frame

    def _drop_device(self) -> None:
        """Release the device so the next open is a real one (``OpenCVSource.open()`` is a no-op
        while its capture is held)."""
        with self._lock:
            opened, self._opened = self._opened, False
        if opened:
            self._inner.close()

    def _record(self, message: str) -> None:
        with self._lock:
            transition = self._error != message
            self._error = message
        if transition:  # log the transition, not every poll
            logger.warning(
                "webcam unavailable; vision is receiving no frames: %s", message
            )

    def _clear_error(self) -> None:
        with self._lock:
            recovered = self._error is not None
            self._error = None
        if recovered:
            logger.info("webcam recovered")

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            opened, self._opened = self._opened, False
        if opened:
            self._inner.close()

    def fps(self) -> float:
        """The poll rate this source paces itself to (a webcam's own rate is unknown or irrelevant)."""
        return 1.0 / self._interval


class ComputerEmbodiment:
    """The agent embodied in the machine the app runs on (specs/computer-embodiment.md)."""

    display_name = "computer"

    def __init__(
        self,
        config: ComputerConfig,
        *,
        tts_engine: TTSEngine | None = None,  # test seam
        coordinator_factory: Callable[
            [ComputerConfig], AudioProcessingCoordinator
        ] = build_coordinator,
    ) -> None:
        self._config = config
        self._injected_engine = tts_engine
        self._factory = coordinator_factory
        self._coordinator: AudioProcessingCoordinator | None = None
        self._tools: TTSTools | None = None
        # Why there is no engine, once `enter()` failed to build one: what `say` answers with.
        self._voice_error: str | None = None
        self.missing: list[str] = []

    async def enter(self) -> None:
        """Build the audio-processing coordinator (when a feature is on) and the voice, on the app
        loop. Neither failure is fatal: each is one ``missing`` line and the member it backs
        degrades — no coordinator means the mic goes straight to ASR, no engine means a ``say`` that
        reports the TTS error."""
        ap = self._config.audio_processing
        if ap.any_enabled:
            try:
                self._coordinator = self._factory(self._config)
            except (ImportError, RuntimeError, ValueError) as exc:
                self.missing.append(f"Audio processing: {exc}")
        # The TTS far-end tap (and its rate binding) is needed only for echo cancellation; noise
        # suppression processes the mic alone. So the sink is wired only when AEC is on.
        sink = (
            self._coordinator.sink
            if self._coordinator is not None and ap.echo_cancellation.enabled
            else None
        )
        try:
            engine = (
                self._injected_engine
                if self._injected_engine is not None
                else TTSEngine(self._config.tts_engine, sink=sink)
            )
            if sink is not None:
                assert (
                    self._coordinator is not None
                )  # sink is set only with a coordinator
                # Two-step: the sink's player/resampler need the module's rate, known only now.
                self._coordinator.bind_far_rate(engine.sample_rate)
            self._tools = TTSTools(engine)
        except Exception as exc:  # noqa: BLE001 - the voice degrades alone, whatever broke it
            # A key unset or a misconfigured module (tts-engine's ConfigError), the provider's extra
            # not installed (ImportError — the SDK import is lazy inside the module), or anything
            # else a provider's constructor raises (a local model that fails to load): the body is
            # up — mic and camera included — it just cannot speak, and `say` says why.
            self._voice_error = str(exc)
            self.missing.append(f"TTS: {exc}")

    async def exit(self) -> None:
        """Close the coordinator's voice stream, kept open for the app's lifetime so the echo's delay
        stays put (specs/audio-processing.md); stopping the ASR bridge has already stopped the
        injected source (capture + pump)."""
        if self._coordinator is not None:
            self._coordinator.close()

    @property
    def voice(self) -> Voice | None:
        """The ``say`` below, always — the same contract as the robot's: with no engine it stays
        wired and reports the TTS error itself, so the persona's "speak through `say`" still names a
        real Command and the model can see why nothing was heard."""
        return self.say

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the user through the machine's speakers.

        This is your voice: whatever you want the user to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
        if self._tools is None:
            return f"TTS error: {self._voice_error or 'no voice was built'}"
        return await self._tools.say(text)

    @property
    def mic(self) -> AudioSource | None:
        """The processed mic when a coordinator was built, else ``None``: the ASR engine then opens
        its own capture on the ASR config's ``audio.device``."""
        return self._coordinator.source if self._coordinator is not None else None

    @property
    def mic_error(self) -> str | None:
        """Always ``None``: a local capture that dies raises inside the engine, which reports it its
        own way."""
        return None

    def camera(self, *, fps: float) -> CameraFeed:
        """The webcam, paced at ``fps``, behind the gesture module's ``StreamProvider`` adapter: the
        hand stage samples a latest-value slot here as it does on the robot; ``open()`` opens the
        device, ``close()`` releases it, ``capture_error`` is the webcam's."""
        # The opener is named here, not left to the default, so the module attribute is what is
        # resolved at call time (a test swaps it for a fake device).
        return StreamFeed(
            WebcamSource(self._config.camera.device, fps=fps, opener=OpenCVSource)
        )

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """Empty: the computer has nothing to act with beyond speaking."""
        return ()

    @property
    def prompt_addendum(self) -> str:
        return ""
