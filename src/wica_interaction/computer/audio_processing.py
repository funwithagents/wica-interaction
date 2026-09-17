"""The computer embodiment's mic processing: keep ASR from hearing the app's own TTS, and denoise.

A coordinator sits between the two engines and bridges their existing injection seams,
running the mic through one WebRTC audio-processing module that can do **echo cancellation** (remove
the app's own TTS) and **noise suppression** (attenuate ambient noise), independently:

- ``.sink`` wraps the real TTS ``AudioPlayer`` and — **only when echo cancellation is on** — taps
  every played chunk as a **far-end reference** (resampled 44.1 k→16 k), and
- ``.source`` wraps the real mic ``AudioCapture`` and runs each frame through the processor before the
  cleaned audio reaches ASR (subtracting the reference when echo cancellation is on; denoising when
  noise suppression is on).

Noise suppression works without echo cancellation: the mic is always routed through the processor
when either feature is on, and the TTS far-end tap is wired only for echo cancellation.

Playback stays at the TTS module's native rate (full quality); only the *reference copy* is
downsampled — with a stateful ``samplerate`` resampler, so a long utterance resamples without drift or
boundary artifacts (drift would de-align the reference from the mic, which AEC punishes). The WebRTC
backend and the resampler are imported lazily so the whole feature is an optional dependency: absent,
the embodiment degrades to no-processing (``ComputerEmbodiment.enter`` catches the ``ImportError``).
The Reachy Mini embodiment needs none of this — its XVF3800 does it in firmware. See
specs/audio-processing.md.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import numpy as np
from asr_engine.audio import (
    DEFAULT_AUDIO_FORMAT,
    AudioCapture,
    AudioFormat,
    AudioSource,
)
from tts_engine.audio import AudioPlayer, AudioSink

if TYPE_CHECKING:
    from wica_interaction.computer.config import ComputerConfig

logger = logging.getLogger(__name__)

NEAR_RATE = 16000  # the ASR/processing working rate; the WebRTC APM runs here
FRAME_SAMPLES = 160  # one 10 ms frame at 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2  # s16 mono

# Cap the far-end reference backlog (samples @16 kHz) so a stalled mic pump can't grow it without
# bound. Reached only pathologically — feed is real-time-paced by blocking playback — so on overflow
# the oldest reference is dropped (a brief realignment) rather than leaking memory.
_MAX_FAR_SAMPLES = NEAR_RATE * 5


class FrameProcessor(Protocol):
    """A swappable processing backend operating on aligned 10 ms frames of s16 mono @16 kHz.

    ``near`` and ``far`` are each one ``FRAME_BYTES``-byte frame (``far`` is the playback reference,
    ignored when echo cancellation is off); ``process`` returns the cleaned near frame."""

    def process(self, near: bytes, far: bytes) -> bytes: ...


class WebRtcAudioProcessor:
    """``FrameProcessor`` over the WebRTC audio-processing module (``aec-audio-processing``).

    Imported lazily so the dependency stays optional. Runs at 16 kHz mono. Echo cancellation and noise
    suppression are independent flags on the one processor; the far-end reference is fed via
    ``process_reverse_stream`` only when echo cancellation is enabled.
    """

    def __init__(
        self,
        *,
        echo_cancellation: bool = False,
        stream_delay_ms: int = 0,
        noise_suppression: bool = False,
        ns_level: int = 2,
    ) -> None:
        from aec_audio_processing import AudioProcessor  # lazy: optional dependency

        ap = AudioProcessor(
            enable_aec=echo_cancellation,
            enable_ns=noise_suppression,
            ns_level=ns_level,
            enable_agc=False,
            enable_vad=False,
        )
        ap.set_stream_format(NEAR_RATE, 1)
        ap.set_reverse_stream_format(NEAR_RATE, 1)
        frame = ap.get_frame_size()
        if frame != FRAME_SAMPLES:
            raise RuntimeError(
                f"WebRTC APM frame size is {frame} samples, expected {FRAME_SAMPLES} "
                f"(is it running at {NEAR_RATE} Hz?)"
            )
        if echo_cancellation:
            ap.set_stream_delay(stream_delay_ms)
        self._ap = ap
        self._aec_enabled = echo_cancellation

    def process(self, near: bytes, far: bytes) -> bytes:
        if self._aec_enabled:
            self._ap.process_reverse_stream(far)
        return self._ap.process_stream(near)


class Resampler(Protocol):
    """Downsamples one chunk of s16 mono PCM (bytes) to int16 samples at the near rate."""

    def process(self, pcm: bytes) -> np.ndarray: ...


class _StreamingResampler:
    """Stateful downsampler (src_rate→16 kHz) over ``samplerate``; int16 bytes in, int16 array out.

    One instance, fed sequential chunks: libsamplerate carries filter state across ``process`` calls,
    so there is no per-chunk boundary artifact and no cumulative length drift.
    """

    def __init__(self, src_rate: int, dst_rate: int = NEAR_RATE) -> None:
        import samplerate  # lazy: optional dependency

        self._ratio = dst_rate / src_rate
        self._resampler = samplerate.Resampler("sinc_fastest", channels=1)

    def process(self, pcm: bytes) -> np.ndarray:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        out = self._resampler.process(samples, self._ratio)
        return np.clip(np.round(out * 32768.0), -32768, 32767).astype(np.int16)


# Injection seams (defaulted to the real implementations; tests pass fakes).
ResamplerFactory = Callable[[int], Resampler]
PlayerFactory = Callable[[str | int | None, int], AudioSink]
CaptureFactory = Callable[
    [str | None, asyncio.AbstractEventLoop, AudioFormat], AudioSource
]


def _default_resampler(src_rate: int) -> Resampler:
    return _StreamingResampler(src_rate)


def _default_player(device: str | int | None, sample_rate: int) -> AudioSink:
    return AudioPlayer(device=device, sample_rate=sample_rate)


def _default_capture(
    device: str | None, loop: asyncio.AbstractEventLoop, fmt: AudioFormat
) -> AudioSource:
    return AudioCapture(device, loop, audio_format=fmt)


class _ProcessingSink:
    """The ``AudioSink`` handed to ``TTSEngine`` — plays each chunk and taps it as the reference."""

    def __init__(self, coordinator: AudioProcessingCoordinator) -> None:
        self._c = coordinator

    def feed(self, chunk: bytes) -> None:
        self._c._feed(chunk)

    def drain(self) -> None:
        self._c._drain()


class _ProcessingSource:
    """The ``AudioSource`` handed to ``ASREngine`` — processed mic frames onto the queue it drains."""

    def __init__(self, coordinator: AudioProcessingCoordinator) -> None:
        self._c = coordinator

    def start(self) -> asyncio.Queue[bytes]:
        return self._c._source_start()

    def stop(self) -> None:
        self._c._source_stop()


class AudioProcessingCoordinator:
    """Bridges the TTS sink (far-end reference) to the ASR source (processed mic), via a ``FrameProcessor``.

    Construct it, wire ``.source`` into ``ASREngine(audio_source=…)`` always, and — when echo
    cancellation is on — ``.sink`` into ``TTSEngine(sink=…)`` and then call
    ``bind_far_rate(tts_engine.sample_rate)`` (the sink's real ``AudioPlayer`` and the reference
    resampler are built there, once the TTS module's rate is known — the two-step in
    specs/audio-processing.md). Noise-suppression-only setups skip the sink and ``bind_far_rate``: the
    source still runs, cancelling against silence. ``.source`` requires a 16 kHz mono ``near_format``
    (the working rate); the caller guards that before building the coordinator.
    """

    def __init__(
        self,
        processor: FrameProcessor,
        *,
        asr_device: str | None,
        tts_device: str | int | None,
        near_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
        resampler_factory: ResamplerFactory = _default_resampler,
        player_factory: PlayerFactory = _default_player,
        capture_factory: CaptureFactory = _default_capture,
    ) -> None:
        self._processor = processor
        self._asr_device = asr_device
        self._tts_device = tts_device
        self._near_format = near_format
        self._resampler_factory = resampler_factory
        self._player_factory = player_factory
        self._capture_factory = capture_factory

        # Far-end (playback reference) path — built at bind_far_rate (echo cancellation only).
        self._player: AudioSink | None = None
        self._resampler: Resampler | None = None
        self._far = np.zeros(0, dtype=np.int16)
        self._far_lock = threading.Lock()

        # Near-end (mic) path — built at source start.
        self._capture: AudioSource | None = None
        self._out_queue: asyncio.Queue[bytes] | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._near_buf = np.zeros(0, dtype=np.int16)

        self.sink: AudioSink = _ProcessingSink(self)
        self.source: AudioSource = _ProcessingSource(self)

    def bind_far_rate(self, far_rate: int) -> None:
        """Build the real player + reference resampler once the TTS module's ``sample_rate`` is known.

        Called only when echo cancellation is on (the sink is wired to TTS). Noise-suppression-only
        setups never call it — the far buffer stays empty and the processor sees silence references.
        """
        self._resampler = self._resampler_factory(far_rate)
        self._player = self._player_factory(self._tts_device, far_rate)

    # --- Far-end reference (runs on the TTS worker thread; calls never overlap) ------------------

    def _feed(self, chunk: bytes) -> None:
        player = self._player
        if player is None:
            # feed before bind_far_rate — a wiring bug; drop rather than crash the TTS worker.
            logger.warning(
                "audio-processing sink fed before bind_far_rate(); dropping chunk"
            )
            return
        player.feed(chunk)  # user hears full-quality playback
        if not chunk or self._resampler is None:
            return
        reference = self._resampler.process(chunk)  # → int16 @16 kHz
        with self._far_lock:
            self._far = np.concatenate([self._far, reference])
            if (
                len(self._far) > _MAX_FAR_SAMPLES
            ):  # pathological backlog: keep the most recent
                self._far = self._far[-_MAX_FAR_SAMPLES:]

    def _drain(self) -> None:
        if self._player is not None:
            self._player.drain()
        # The far buffer is left to empty as the pump consumes it: at drain time it still holds the
        # reference for audio the mic has not finished hearing (output-buffer + acoustic latency), so
        # clearing it would leak the echo tail.

    def _pop_far(self, n: int) -> np.ndarray:
        """Take the next *n* reference samples, zero-padded when the buffer is short (no playback)."""
        with self._far_lock:
            if len(self._far) >= n:
                frame = self._far[:n]
                self._far = self._far[n:]
                return frame
            frame = np.concatenate(
                [self._far, np.zeros(n - len(self._far), dtype=np.int16)]
            )
            self._far = np.zeros(0, dtype=np.int16)
            return frame

    # --- Near-end (mic) path (runs on the app event loop) ---------------------------------------

    def _source_start(self) -> asyncio.Queue[bytes]:
        loop = asyncio.get_running_loop()
        self._capture = self._capture_factory(self._asr_device, loop, self._near_format)
        raw_queue = self._capture.start()
        self._out_queue = asyncio.Queue()
        self._near_buf = np.zeros(0, dtype=np.int16)
        self._pump_task = asyncio.create_task(self._pump(raw_queue))
        return self._out_queue

    async def _pump(self, raw_queue: asyncio.Queue[bytes]) -> None:
        assert self._out_queue is not None
        while True:
            chunk = await raw_queue.get()
            processed = self._process_chunk(chunk)
            if processed:
                self._out_queue.put_nowait(processed)

    def _process_chunk(self, chunk: bytes) -> bytes:
        """Run one mic chunk through the processor, framing it into 160-sample frames.

        Buffers across chunks so any chunk size works; emits processed audio for whole frames only, so
        the output stream tracks the mic's cadence (a <160-sample remainder waits for the next chunk).
        """
        self._near_buf = np.concatenate(
            [self._near_buf, np.frombuffer(chunk, dtype=np.int16)]
        )
        out: list[bytes] = []
        while len(self._near_buf) >= FRAME_SAMPLES:
            near_frame = self._near_buf[:FRAME_SAMPLES]
            self._near_buf = self._near_buf[FRAME_SAMPLES:]
            far_frame = self._pop_far(FRAME_SAMPLES)
            out.append(
                self._processor.process(near_frame.tobytes(), far_frame.tobytes())
            )
        return b"".join(out)

    def _source_stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None
        if self._capture is not None:
            self._capture.stop()
            self._capture = None


def build_coordinator(config: ComputerConfig) -> AudioProcessingCoordinator:
    """Build the audio-processing coordinator (WebRTC backend + resampler), or raise.

    The one caught build point, called from ``ComputerEmbodiment.enter()`` through its
    ``coordinator_factory`` seam. Raises ``ImportError`` if an optional dependency is absent — the
    WebRTC backend, or (when echo cancellation is on) the resampler the far-end tap needs — and
    ``RuntimeError`` if the ASR audio format is not the 16 kHz mono linear16 the processor needs.
    Either is caught by the caller and reported in ``missing`` so the embodiment degrades to
    no-processing rather than failing. Validating the resampler here, not lazily in
    ``bind_far_rate``, keeps a partial install (backend present, resampler absent) on the
    graceful-degrade path.
    """
    ap = config.audio_processing
    audio = config.app.asr_engine.audio
    if (audio.sample_rate, audio.channels, audio.encoding) != (
        NEAR_RATE,
        1,
        "linear16",
    ):
        raise RuntimeError(
            f"audio processing needs 16 kHz mono linear16 ASR audio, got "
            f"{audio.sample_rate} Hz / {audio.channels}ch / {audio.encoding}"
        )
    processor = WebRtcAudioProcessor(
        echo_cancellation=ap.echo_cancellation.enabled,
        stream_delay_ms=ap.echo_cancellation.stream_delay_ms,
        noise_suppression=ap.noise_suppression.enabled,
        ns_level=ap.noise_suppression.level,
    )
    if ap.echo_cancellation.enabled:
        # Echo cancellation resamples the far-end reference (in bind_far_rate, during the TTS build);
        # validate that dependency here — at the caught build point — so a missing resampler degrades
        # like a missing backend instead of surfacing later as an uncaught error.
        importlib.import_module("samplerate")
    return AudioProcessingCoordinator(
        processor,
        asr_device=audio.device,
        tts_device=config.tts_engine.player.device,
        near_format=AudioFormat(
            sample_rate=audio.sample_rate,
            channels=audio.channels,
            encoding=audio.encoding,
        ),
    )
