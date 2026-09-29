"""Functional tests for the computer embodiment (specs/computer-embodiment.md, "Testing").

Deterministic: no audio device, no camera, no provider. The voice runs over an injected fake
`TTSEngine`, the audio-processing wiring over a fake coordinator injected through the
`coordinator_factory` seam, and the webcam over a fake `OpenCVSource`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Coroutine
from typing import Any, cast

import numpy as np
import pytest
from asr_engine.config import ASREngineConfig
from tts_engine.config import ConfigError as TTSConfigError
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine
from vision_modules import FrameSource

import wica_interaction.computer.embodiment as embodiment_module
from tests.computer.fakes import FakeCoordinator, FakeOpenCVSource, FakeTTSEngine
from wica_interaction.computer.audio_processing import AudioProcessingCoordinator
from wica_interaction.computer.config import (
    AudioProcessingConfig,
    CameraConfig,
    ComputerConfig,
    EchoCancellationConfig,
    NoiseSuppressionConfig,
)
from wica_interaction.computer.embodiment import ComputerEmbodiment, WebcamSource
from wica_interaction.config import AppConfig

_FRAME = np.full((4, 4, 3), 200, dtype=np.uint8)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _config(
    *, aec: bool = False, ns: bool = False, camera: int | str = 0
) -> ComputerConfig:
    """A ComputerConfig whose ASR section is the 16 kHz mono linear16 the processor needs."""
    return ComputerConfig(
        app=AppConfig(
            wica=cast(Any, None),
            asr_engine=ASREngineConfig.from_dict(
                {"module": {"type": "deepgram_v2"}, "audio": {"device": None}}
            ),
        ),
        tts_engine=TTSEngineConfig.from_dict({"module": {"type": "elevenlabs"}}),
        audio_processing=AudioProcessingConfig(
            echo_cancellation=EchoCancellationConfig(enabled=aec),
            noise_suppression=NoiseSuppressionConfig(enabled=ns),
        ),
        camera=CameraConfig(device=camera),
    )


def _entered(
    config: ComputerConfig,
    *,
    engine: FakeTTSEngine | None = None,
    factory: Any = None,
) -> ComputerEmbodiment:
    """Construct the embodiment and enter it on a throwaway loop, as build_system does."""
    embodiment = ComputerEmbodiment(
        config,
        tts_engine=cast(TTSEngine, engine if engine is not None else FakeTTSEngine()),
        **({"coordinator_factory": factory} if factory is not None else {}),
    )
    run(embodiment.enter())
    return embodiment


# --- enter() and the protocol members ------------------------------------------------------------


def test_with_processing_off_the_voice_is_wired_and_the_mic_is_the_engines_own() -> (
    None
):
    """The default: no coordinator, so the ASR engine opens its own capture (`mic is None`) and the
    TTS engine gets no sink."""
    engine = FakeTTSEngine()
    embodiment = _entered(_config(), engine=engine)

    assert embodiment.voice is not None
    assert embodiment.mic is None
    assert embodiment.mic_error is None
    assert embodiment.commands() == ()  # nothing to act with
    assert embodiment.prompt_addendum == ""
    assert embodiment.missing == []


def test_echo_cancellation_wires_the_sink_the_source_and_binds_the_far_rate() -> None:
    """AEC on: the coordinator's source is the mic, its sink taps the engine's playback, and the
    far rate is bound with the module's own rate — the two-step specs/audio-processing.md
    describes."""
    coordinator = FakeCoordinator()
    built: list[Any] = []

    def factory(config: ComputerConfig) -> AudioProcessingCoordinator:
        built.append(config)
        return cast(AudioProcessingCoordinator, coordinator)

    # No injected engine: the embodiment must build one *with the sink*, so a recording double
    # stands in for the real TTSEngine constructor.
    engines: list[FakeTTSEngine] = []

    class _Embodiment(ComputerEmbodiment):
        pass

    embodiment = _Embodiment(
        _config(aec=True),
        tts_engine=None,
        coordinator_factory=factory,
    )
    # Patch the constructor the embodiment calls, recording the sink it was handed.
    import wica_interaction.computer.embodiment as module

    original = module.TTSEngine
    module.TTSEngine = lambda _c, *, sink=None: (
        engines.append(  # type: ignore[assignment]
            FakeTTSEngine(sink=sink)
        )
        or engines[-1]
    )
    try:
        run(embodiment.enter())
    finally:
        module.TTSEngine = original  # type: ignore[assignment]

    assert built == [embodiment._config]  # pyright: ignore[reportPrivateUsage]
    assert embodiment.mic is coordinator.source
    assert engines[0].sink is coordinator.sink
    assert coordinator.bound == [FakeTTSEngine.sample_rate]
    assert embodiment.missing == []


def test_noise_suppression_only_wires_the_source_but_no_sink() -> None:
    """NS on, AEC off: the mic is processed, but there is no playback reference to tap and no rate
    to bind."""
    coordinator = FakeCoordinator()
    engine = FakeTTSEngine()
    embodiment = _entered(
        _config(ns=True),
        engine=engine,
        factory=lambda _c: cast(AudioProcessingCoordinator, coordinator),
    )

    assert embodiment.mic is coordinator.source  # the mic is processed …
    assert coordinator.bound == []  # … but nothing binds the far rate


def test_a_failing_coordinator_degrades_to_direct_wiring() -> None:
    """A missing dependency is one `missing` line: no coordinator, the mic goes straight to ASR, and
    the voice still works."""

    def boom(_config: ComputerConfig) -> AudioProcessingCoordinator:
        raise ImportError("No module named 'aec_audio_processing'")

    engine = FakeTTSEngine()
    embodiment = _entered(_config(aec=True), engine=engine, factory=boom)

    assert any(
        m.startswith("Audio processing:") and "aec_audio_processing" in m
        for m in embodiment.missing
    )
    assert embodiment.mic is None  # degraded to the engine's own capture
    assert embodiment.voice is not None  # the voice is unaffected
    assert run(embodiment.say("hello")) == "OK"


def test_a_wrong_asr_format_degrades_the_processing_not_the_body() -> None:
    """The real `build_coordinator` rejects an ASR format the processor cannot work at; that
    `RuntimeError` is a `missing` line, not a failed body."""
    config = _config(aec=True)
    config.app.asr_engine = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram_v2"}, "audio": {"sample_rate": 48000}}
    )
    embodiment = _entered(config)  # the default factory: the real build_coordinator

    assert any(
        m.startswith("Audio processing:") and "16 kHz mono linear16" in m
        for m in embodiment.missing
    )
    assert embodiment.mic is None
    assert embodiment.voice is not None


@pytest.mark.parametrize(
    "failure",
    [
        TTSConfigError("ELEVENLABS_API_KEY is not set"),
        ImportError("no sdk"),
        # Not a config or import error: whatever a provider's constructor raises (a local model
        # that fails to load) must still cost the voice alone, never the whole body.
        RuntimeError("model weights could not be loaded"),
    ],
)
def test_a_failed_voice_build_leaves_the_body_up_with_a_say_that_reports_it(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key unset, a provider extra absent or any other build failure degrades to `TTS: …` — and
    the voice keeps the robot's contract: `say` stays wired (the persona says "speak through `say`")
    and answers with the TTS error, so the model sees why nothing was heard. The rest of the body
    (its mic, its camera) is untouched."""

    def boom(_config: Any, *, sink: Any = None) -> Any:
        raise failure

    monkeypatch.setattr(embodiment_module, "TTSEngine", boom)
    embodiment = ComputerEmbodiment(_config())
    run(embodiment.enter())

    assert any(m.startswith("TTS:") and str(failure) in m for m in embodiment.missing)
    assert embodiment.voice == embodiment.say  # still wired as the output Command
    assert run(embodiment.say("hello")) == f"TTS error: {failure}"
    assert embodiment.camera(fps=5.0) is not None  # the camera does not need the voice


def test_exit_releases_nothing_and_never_raises() -> None:
    """Stopping the ASR bridge releases the devices; `exit()` is a no-op (specs/audio-processing.md,
    "Teardown")."""
    embodiment = _entered(_config())
    run(embodiment.exit())  # must not raise


# --- say ------------------------------------------------------------------------------------------


def test_say_speaks_through_the_engine_and_returns_ok() -> None:
    engine = FakeTTSEngine()
    embodiment = _entered(_config(), engine=engine)

    assert run(embodiment.say("hello")) == "OK"
    assert engine.said == ["hello"]


def test_empty_text_is_guarded_without_calling_the_engine() -> None:
    engine = FakeTTSEngine()
    embodiment = _entered(_config(), engine=engine)

    assert run(embodiment.say("")) == "TTS error: text must not be empty"
    assert engine.said == []


def test_a_synthesis_failure_is_mapped_not_raised() -> None:
    engine = FakeTTSEngine(raise_message="voice unavailable")
    embodiment = _entered(_config(), engine=engine)

    result = run(embodiment.say("hi"))

    assert result == "TTS error: voice unavailable"
    assert engine.said == ["hi"]  # it did attempt to speak before failing


# --- camera(): the webcam behind a StreamFeed ----------------------------------------------------


def test_camera_is_the_webcam_behind_a_stream_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`camera(fps=…)` hands the gesture input a feed: `open()` opens the device (through the
    module's provider), frames then publish at the paced rate, `close()` releases the device, and
    `capture_error` is the webcam's own (an unopenable device shows through it)."""
    inner = FakeOpenCVSource(_FRAME)
    monkeypatch.setattr(embodiment_module, "OpenCVSource", lambda _device: inner)
    embodiment = _entered(_config(camera=2), engine=FakeTTSEngine())
    feed = embodiment.camera(fps=100.0)
    assert inner.opens == 0  # nothing opened until the gesture input opens the feed
    assert feed.latest() is None and feed.capture_error is None
    feed.open()
    try:
        assert inner.opens == 1
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and feed.published_count < 3:
            time.sleep(0.005)
        assert feed.published_count >= 3
        frame = feed.latest()
        assert frame is not None and frame.image is _FRAME
    finally:
        feed.close()
    assert inner.closes == 1

    # An unopenable device: the webcam's error is the feed's, and open() does not raise.
    inner.raise_on_open = "device 2 not found"
    failing = embodiment.camera(fps=100.0)
    failing.open()
    try:
        assert failing.capture_error is not None
        assert "device 2 not found" in failing.capture_error
    finally:
        failing.close()


# --- WebcamSource ---------------------------------------------------------------------------------


def _webcam(inner: FakeOpenCVSource, *, fps: float = 100.0) -> WebcamSource:
    return WebcamSource(0, fps=fps, opener=lambda _device: cast(FrameSource, inner))


def test_webcam_reads_frames_and_paces_them_to_fps() -> None:
    inner = FakeOpenCVSource(_FRAME)
    source = _webcam(inner, fps=50.0)
    source.open()
    try:
        assert source.fps() == 50.0  # the protocol's nominal rate is the poll rate
        t0 = time.monotonic()
        for _ in range(6):
            assert source.read() is _FRAME
        elapsed = time.monotonic() - t0
        assert (
            elapsed >= 5 * 0.02 * 0.9
        )  # 6 reads span at least 5 intervals (with slack)
        assert inner.opens == 1  # opened once, not per read
    finally:
        source.close()


def test_a_device_that_cannot_be_opened_is_visible_and_heals() -> None:
    """An unplugged webcam shows on the Vision line and keeps retrying at the poll rate; `open()`
    never raises, and a later success clears the error."""
    inner = FakeOpenCVSource(_FRAME)
    inner.raise_on_open = "device 0 not found"
    source = _webcam(inner)

    source.open()  # must not raise
    assert source.capture_error is not None
    assert "camera open failed" in source.capture_error
    assert "device 0 not found" in source.capture_error

    threading.Timer(0.05, lambda: setattr(inner, "raise_on_open", None)).start()
    try:
        frame = source.read()  # blocks through the failed opens, returns once it works
        assert frame is _FRAME
        assert source.capture_error is None  # cleared by the success
        assert inner.opens > 1  # it kept retrying the open
    finally:
        source.close()


def test_a_failing_read_is_recorded_and_retried() -> None:
    inner = FakeOpenCVSource(_FRAME)
    source = _webcam(inner)
    source.open()
    try:
        inner.raise_on_read = "device disappeared"
        threading.Timer(0.05, lambda: setattr(inner, "raise_on_read", None)).start()
        assert source.read() is _FRAME  # it polled through the failure
        assert source.capture_error is None
    finally:
        source.close()


def test_a_missing_frame_is_not_the_end_of_the_stream() -> None:
    """The provider ends for good on a `None`, so "nothing yet" must wait and poll again."""
    inner = FakeOpenCVSource(_FRAME)
    inner.frame = None
    source = _webcam(inner)
    source.open()
    threading.Timer(0.05, lambda: setattr(inner, "frame", _FRAME)).start()
    try:
        assert source.read() is _FRAME
        assert inner.reads >= 2
    finally:
        source.close()


def test_close_unblocks_read_and_releases_the_device() -> None:
    inner = FakeOpenCVSource(_FRAME)
    inner.frame = None
    source = _webcam(inner, fps=1.0)
    source.open()
    threading.Timer(0.05, source.close).start()

    t0 = time.monotonic()
    assert source.read() is None  # only close() makes read() return None
    assert time.monotonic() - t0 < 0.5  # did not wait out the 1 s interval
    assert inner.closes == 1


def test_an_open_that_completes_after_close_is_closed_again() -> None:
    """The retry-open runs on the capture thread while `close()` runs on the main one. When the
    close lands *during* the open it finds nothing to close — so the device the open then yields
    must be released, not kept past the source's life."""
    inner = FakeOpenCVSource()
    source = _webcam(inner)
    real_open = inner.open

    def open_while_closing() -> None:
        real_open()
        source.close()  # teardown arrives while the device is still opening

    inner.open = open_while_closing  # type: ignore[method-assign]
    source.open()

    assert inner.opens == 1
    assert inner.closes == 1  # released, even though close() itself saw it unopened
    assert source.read() is None


def test_a_non_positive_fps_is_rejected() -> None:
    with pytest.raises(ValueError, match="fps must be > 0"):
        WebcamSource(
            0, fps=0.0, opener=lambda _d: cast(FrameSource, FakeOpenCVSource())
        )
