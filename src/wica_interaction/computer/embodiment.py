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
still a body.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from asr_engine.audio import AudioSource
from tts_engine.config import ConfigError as TTSConfigError
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
from wica_interaction.gesture_input import CameraSource

logger = logging.getLogger(__name__)


class WebcamSource:
    """The local webcam as a ``CameraSource``, over ``vision-modules``' own ``OpenCVSource``.

    Obeys the three rules every camera source does (specs/gesture-input.md, "Frames come from the
    embodiment"): it paces itself to ``fps`` (the provider calls ``read()`` back-to-back, and a
    webcam delivering 30 fps into a 5 fps hand stage would be wasted captures); a device that cannot
    be opened — or a read that fails — is recorded in ``capture_error`` and retried at the poll rate,
    so an unplugged webcam shows on the Vision line and heals when plugged back in; and only
    ``close()`` makes ``read()`` return ``None``. No mirroring: the classifier sees the frame as
    captured.
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
        self._opened = True
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
            return self._inner.read()
        except Exception as exc:  # noqa: BLE001 - a device that went away is visible, not fatal
            self._record(f"camera read failed: {exc}")
            # The device is gone: drop it so the next poll re-opens rather than re-reading a dead one.
            self._opened = False
            return None

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
        if self._opened:
            self._opened = False
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
        self.missing: list[str] = []

    async def enter(self) -> None:
        """Build the audio-processing coordinator (when a feature is on) and the voice, on the app
        loop. Neither failure is fatal: each is one ``missing`` line and the member it backs
        degrades — no coordinator means the mic goes straight to ASR, no engine means no voice."""
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
        except (TTSConfigError, ImportError) as exc:
            # A key unset, a misconfigured module, or the provider's extra not installed (the SDK
            # import is lazy inside the module): the body is up, it just has no voice.
            self.missing.append(f"TTS: {exc}")

    async def exit(self) -> None:
        """Nothing to release: stopping the ASR bridge stops the injected source (capture + pump),
        and the sink's player drains at the end of every `say` (specs/audio-processing.md)."""

    @property
    def voice(self) -> Voice | None:
        return self.say if self._tools is not None else None

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the user through the machine's speakers.

        This is your voice: whatever you want the user to hear, say it here.
        Returns "OK" when the utterance finished playing.
        """
        assert self._tools is not None
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

    def camera(self, *, fps: float) -> CameraSource:
        return WebcamSource(self._config.camera.device, fps=fps)

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        """Empty: the computer has nothing to act with beyond speaking."""
        return ()

    @property
    def prompt_addendum(self) -> str:
        return ""
