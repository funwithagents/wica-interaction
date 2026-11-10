"""Functional tests for audio processing (specs/audio-processing.md).

Two tiers, both in the fast suite:

- The ``AudioProcessingCoordinator`` is driven with fakes (a recording processor/player, a scripted
  capture, an identity resampler) — no audio devices, no WebRTC — so the framing, far-end pairing,
  reference tap, and drain behaviour are pinned deterministically.
- ``WebRtcAudioProcessor`` is exercised against synthetic echo and noise, skipping if the optional
  ``aec-audio-processing`` dependency is absent (so the tier stays runnable without it).

The ``audio_processing`` config section is parsed by ``computer/config.py``; its cases are at the
bottom of this file, with the feature they configure.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from wica_interaction.computer.audio_processing import (
    FRAME_SAMPLES,
    AudioProcessingCoordinator,
    ContinuousPlayer,
)
from wica_interaction.computer.config import (
    AudioProcessingConfig,
    ComputerConfig,
    EchoCancellationConfig,
    NoiseSuppressionConfig,
    load_config,
)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


class RecordingProcessor:
    """Records each (near, far) frame pair and passes the near frame through unchanged."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, bytes]] = []

    def process(self, near: bytes, far: bytes) -> bytes:
        self.calls.append((near, far))
        return near


class RecordingPlayer:
    """An ``AudioSink`` that records fed chunks and drain calls."""

    def __init__(self) -> None:
        self.fed: list[bytes] = []
        self.drained = 0
        self.closed = 0
        self.on_played: Any = None
        # By default a fed chunk is played at once, as far as the coordinator can tell: the
        # reference it taps then lands in the far buffer straight away.
        self.plays_on_feed = True

    def feed(self, chunk: bytes) -> None:
        self.fed.append(chunk)
        if self.plays_on_feed:
            self.on_played(np.frombuffer(chunk, dtype=np.int16))

    def drain(self) -> None:
        self.drained += 1

    def close(self) -> None:
        self.closed += 1


class ScriptedCapture:
    """An ``AudioSource`` a test feeds by hand: ``push`` puts raw mic chunks on the returned queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.started = False
        self.stopped = False

    def start(self) -> asyncio.Queue[bytes]:
        self.started = True
        return self.queue

    def stop(self) -> None:
        self.stopped = True

    async def push(self, chunk: bytes) -> None:
        await self.queue.put(chunk)


class _IdentityResampler:
    """Stands in for the real resampler: returns input samples unchanged (no rate change), so a
    test can predict exactly which reference samples land in the far buffer."""

    def process(self, pcm: bytes) -> np.ndarray:
        return np.frombuffer(pcm, dtype=np.int16).copy()


def _make_coordinator() -> tuple[
    AudioProcessingCoordinator, RecordingProcessor, RecordingPlayer, ScriptedCapture
]:
    processor = RecordingProcessor()
    player = RecordingPlayer()
    capture = ScriptedCapture()

    def make_player(_dev: Any, _rate: int, on_played: Any) -> RecordingPlayer:
        player.on_played = on_played
        return player

    coordinator = AudioProcessingCoordinator(
        processor,
        asr_device=None,
        tts_device=None,
        resampler_factory=lambda _src_rate: _IdentityResampler(),
        player_factory=make_player,
        capture_factory=lambda _dev, _loop, _fmt: capture,
    )
    coordinator.bind_far_rate(44100)
    return coordinator, processor, player, capture


def test_sink_forwards_to_player_and_drain() -> None:
    coordinator, _processor, player, _capture = _make_coordinator()

    chunk = (np.arange(100, dtype=np.int16)).tobytes()
    coordinator.sink.feed(chunk)
    assert player.fed == [chunk]  # user hears the full-quality playback

    coordinator.sink.drain()
    assert player.drained == 1


def test_the_reference_is_what_the_player_played() -> None:
    """The reference is taken as the player sends a block to the device, not when the TTS engine
    hands it a chunk: a fed chunk not yet played queues no reference; once played, it does. So the
    reference cannot run late behind its echo, whatever the size of the chunks."""
    coordinator, _processor, player, _capture = _make_coordinator()
    player.plays_on_feed = False

    coordinator.sink.feed(np.arange(1600, dtype=np.int16).tobytes())
    assert len(coordinator._far) == 0

    player.on_played(np.arange(800, dtype=np.int16))
    assert np.array_equal(coordinator._far, np.arange(800, dtype=np.int16))


def test_close_closes_the_player() -> None:
    coordinator, _processor, player, _capture = _make_coordinator()
    coordinator.close()
    assert player.closed == 1
    coordinator.close()  # once closed, a no-op
    assert player.closed == 1


# --- ContinuousPlayer, over a fake stream ---------------------------------------------------------


class FakeStream:
    """An output stream a test drives by hand: `pull(frames)` runs the callback as the device would
    and returns what it wrote."""

    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.active = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True

    def pull(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 1), dtype=np.int16)
        self.callback(out, frames, None, None)
        return out[:, 0].copy()


def _player(rate: int = 1000) -> tuple[ContinuousPlayer, FakeStream, list[np.ndarray]]:
    played: list[np.ndarray] = []
    streams: list[FakeStream] = []

    def factory(_device: Any, _rate: int, callback: Any) -> FakeStream:
        streams.append(FakeStream(callback))
        return streams[-1]

    player = ContinuousPlayer(None, rate, played.append, stream_factory=factory)
    return player, streams[0], played


def test_the_player_plays_what_is_fed_and_silence_between() -> None:
    """One stream, started at once: a fed chunk goes out block by block, each block handed to
    `on_played`; with nothing queued it plays silence and reports nothing."""
    player, stream, played = _player()
    assert stream.active

    assert not stream.pull(10).any()  # silence before anything is fed
    assert played == []

    player.feed(
        np.arange(1, 51, dtype=np.int16).tobytes()
    )  # 50 ms at 1 kHz: within the ahead
    first = stream.pull(30)
    assert np.array_equal(first, np.arange(1, 31))
    last = stream.pull(30)
    assert np.array_equal(last[:20], np.arange(31, 51)) and not last[20:].any()
    assert [len(b) for b in played] == [30, 30]
    assert not stream.pull(10).any()  # silence again
    assert len(played) == 2


def test_feed_blocks_until_the_queue_is_within_its_lead() -> None:
    """Feeding more than the lead (100 ms) blocks until the device has played the excess, as a
    blocking write would — so a cancelled utterance stops within that much audio."""
    player, stream, _played = _player()
    done = threading.Event()
    worker = threading.Thread(
        target=lambda: (player.feed(np.ones(300, dtype=np.int16).tobytes()), done.set())
    )
    worker.start()
    assert not done.wait(0.2)  # 300 ms queued, 100 ms allowed
    stream.pull(250)
    assert done.wait(2.0)
    worker.join()


def test_drain_waits_for_the_queue_and_keeps_the_stream_open() -> None:
    player, stream, _played = _player()
    player.feed(np.ones(50, dtype=np.int16).tobytes())
    done = threading.Event()
    worker = threading.Thread(target=lambda: (player.drain(), done.set()))
    worker.start()
    assert not done.wait(0.2)
    stream.pull(50)
    assert done.wait(2.0)
    worker.join()
    assert (
        stream.active and not stream.closed
    )  # the next utterance plays on the same stream

    player.close()
    assert not stream.active and stream.closed


def test_a_stream_that_stopped_does_not_hang_the_tts_worker() -> None:
    """A device gone mid-utterance stops the stream on its own: `drain` returns, dropping what was
    queued, instead of waiting forever."""
    player, stream, _played = _player()
    player.feed(np.ones(50, dtype=np.int16).tobytes())
    stream.active = False
    player.drain()  # returns


def test_feed_before_bind_is_dropped_not_raised() -> None:
    # No bind_far_rate() → no player yet; feeding must not crash the TTS worker.
    coordinator = AudioProcessingCoordinator(
        RecordingProcessor(), asr_device=None, tts_device=None
    )
    coordinator.sink.feed(
        b"\x00\x00" * 10
    )  # returns quietly, nothing to assert but no exception


def test_source_reframes_chunk_into_160_sample_frames_paired_with_reference() -> None:
    """A 1600-sample mic chunk becomes ten 160-sample frames, each paired with the buffered reference
    (real reference while it lasts, silence once exhausted); output preserves chunk length."""
    coordinator, processor, _player, capture = _make_coordinator()

    # Buffer 320 reference samples (two frames' worth) via the sink before the mic chunk arrives.
    reference = np.arange(1, 321, dtype=np.int16)  # 1..320, all non-zero
    coordinator.sink.feed(reference.tobytes())

    near = np.full(1600, 7, dtype=np.int16)

    async def scenario() -> bytes:
        out_queue = coordinator.source.start()
        await capture.push(near.tobytes())
        return await asyncio.wait_for(out_queue.get(), timeout=1.0)

    processed = run(scenario())

    # Ten frames processed, each 160 near samples of value 7.
    assert len(processor.calls) == 1600 // FRAME_SAMPLES == 10
    for near_bytes, _far_bytes in processor.calls:
        frame = np.frombuffer(near_bytes, dtype=np.int16)
        assert frame.tolist() == [7] * FRAME_SAMPLES

    # Far frames: first two are the buffered reference, the remaining eight are silence.
    far_frames = [np.frombuffer(f, dtype=np.int16) for _n, f in processor.calls]
    assert far_frames[0].tolist() == reference[:160].tolist()
    assert far_frames[1].tolist() == reference[160:320].tolist()
    for silent in far_frames[2:]:
        assert silent.tolist() == [0] * FRAME_SAMPLES

    # Passthrough processor → processed output preserves the input chunk length.
    assert len(np.frombuffer(processed, dtype=np.int16)) == 1600


def test_source_buffers_sub_frame_remainder_across_chunks() -> None:
    """A chunk that isn't a multiple of 160 emits only whole frames; the remainder waits for more."""
    coordinator, processor, _player, capture = _make_coordinator()

    async def scenario() -> tuple[int, int]:
        out_queue = coordinator.source.start()
        await capture.push(
            np.zeros(200, dtype=np.int16).tobytes()
        )  # 200 = 1 frame + 40 leftover
        first = await asyncio.wait_for(out_queue.get(), timeout=1.0)
        await capture.push(
            np.zeros(120, dtype=np.int16).tobytes()
        )  # 40 + 120 = 160 → 1 more frame
        second = await asyncio.wait_for(out_queue.get(), timeout=1.0)
        return len(first), len(second)

    first_len, second_len = run(scenario())
    assert len(processor.calls) == 2  # exactly two whole frames across the two chunks
    assert first_len == FRAME_SAMPLES * 2  # 160 samples → 320 bytes
    assert second_len == FRAME_SAMPLES * 2


def test_source_stop_stops_capture() -> None:
    coordinator, _processor, _player, capture = _make_coordinator()

    async def scenario() -> None:
        coordinator.source.start()
        coordinator.source.stop()

    run(scenario())
    assert capture.stopped


# --- WebRTC backend (skips without the optional dependency) ----------------------------------------


def test_webrtc_reduces_echo() -> None:
    pytest.importorskip("aec_audio_processing")
    from wica_interaction.computer.audio_processing import WebRtcAudioProcessor

    processor = WebRtcAudioProcessor(echo_cancellation=True, stream_delay_ms=0)
    sr = 16000
    n = sr * 4
    t = np.arange(n) / sr
    rng = np.random.default_rng(0)
    far = (
        (0.5 * np.sin(2 * np.pi * 320 * t) + 0.2 * rng.standard_normal(n)) * 20000
    ).astype(np.int16)
    delay = int(0.03 * sr)
    echo = np.zeros(n, dtype=np.float32)
    echo[delay:] = 0.5 * far[: n - delay]
    near = echo.astype(np.int16)

    out_frames = []
    for i in range(0, n - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        cleaned = processor.process(
            near[i : i + FRAME_SAMPLES].tobytes(), far[i : i + FRAME_SAMPLES].tobytes()
        )
        out_frames.append(np.frombuffer(cleaned, dtype=np.int16))
    out = np.concatenate(out_frames)

    # Measure steady-state (last second), after the adaptive filter has converged.
    w = sr
    erle = 20.0 * np.log10(_rms(near[-w:]) / max(_rms(out[-w:]), 1e-9))
    assert erle > 6.0, f"expected meaningful echo reduction, got {erle:.1f} dB"


def test_webrtc_preserves_near_speech_with_silent_far() -> None:
    pytest.importorskip("aec_audio_processing")
    from wica_interaction.computer.audio_processing import WebRtcAudioProcessor

    processor = WebRtcAudioProcessor(echo_cancellation=True, stream_delay_ms=0)
    sr = 16000
    n = sr * 2
    t = np.arange(n) / sr
    near = (0.4 * np.sin(2 * np.pi * 220 * t) * 20000).astype(np.int16)
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()

    out_frames = []
    for i in range(0, n - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        cleaned = processor.process(near[i : i + FRAME_SAMPLES].tobytes(), silence)
        out_frames.append(np.frombuffer(cleaned, dtype=np.int16))
    out = np.concatenate(out_frames)

    # With no far-end to cancel, near-end speech must survive largely intact.
    assert _rms(out[sr // 2 :]) > 0.5 * _rms(near[sr // 2 : len(out)])


def test_webrtc_noise_suppression_only_attenuates_stationary_noise() -> None:
    """Noise suppression works without echo cancellation: stationary noise on the near-end (no
    far-end reference) is attenuated."""
    pytest.importorskip("aec_audio_processing")
    from wica_interaction.computer.audio_processing import WebRtcAudioProcessor

    processor = WebRtcAudioProcessor(
        echo_cancellation=False, noise_suppression=True, ns_level=3
    )
    sr = 16000
    n = sr * 4
    rng = np.random.default_rng(1)
    near = (rng.standard_normal(n) * 6000).astype(
        np.int16
    )  # stationary broadband noise
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()

    out_frames = []
    for i in range(0, n - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        cleaned = processor.process(near[i : i + FRAME_SAMPLES].tobytes(), silence)
        out_frames.append(np.frombuffer(cleaned, dtype=np.int16))
    out = np.concatenate(out_frames)

    # After the suppressor has learned the noise floor, the tail should be quieter than the input.
    w = sr
    assert _rms(out[-w:]) < 0.9 * _rms(near[-w:]), (
        f"NS did not attenuate noise: in {_rms(near[-w:]):.0f} -> out {_rms(out[-w:]):.0f}"
    )


# --- The `audio_processing` config section (specs/audio-processing.md, "Configuration") --------


def _load_with(tmp_path: Path, section: object | None) -> ComputerConfig:
    """Load a valid computer config carrying *section* as its `audio_processing`."""
    config: dict[str, Any] = {
        "wica": {
            "agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "system_prompt": "a voice assistant",
            }
        },
        "asr": {"engine": {"module": {"type": "deepgram_v2"}}},
        "tts": {"engine": {"module": {"type": "elevenlabs"}}},
    }
    if section is not None:
        config["audio_processing"] = section
    path = tmp_path / "app.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return load_config(str(path))


def test_audio_processing_absent_defaults_to_disabled(tmp_path: Path) -> None:
    cfg = _load_with(tmp_path, None)
    assert cfg.audio_processing == AudioProcessingConfig()
    assert not cfg.audio_processing.any_enabled


def test_audio_processing_parses_both_features(tmp_path: Path) -> None:
    cfg = _load_with(
        tmp_path,
        {
            "echo_cancellation": {"enabled": True, "stream_delay_ms": 60},
            "noise_suppression": {"enabled": True, "level": 3},
        },
    )
    assert cfg.audio_processing == AudioProcessingConfig(
        echo_cancellation=EchoCancellationConfig(enabled=True, stream_delay_ms=60),
        noise_suppression=NoiseSuppressionConfig(enabled=True, level=3),
    )
    assert cfg.audio_processing.any_enabled


def test_audio_processing_partial_sections_keep_defaults(tmp_path: Path) -> None:
    # Noise suppression enabled alone: echo cancellation and the ns level keep their defaults, and
    # `any_enabled` is still true (the mic is routed through the processor, the sink is not wired).
    cfg = _load_with(tmp_path, {"noise_suppression": {"enabled": True}})
    assert cfg.audio_processing.echo_cancellation == EchoCancellationConfig(
        enabled=False
    )
    assert cfg.audio_processing.noise_suppression == NoiseSuppressionConfig(
        enabled=True, level=2
    )
    assert cfg.audio_processing.any_enabled


def test_audio_processing_not_an_object_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match="section 'audio_processing' must be an object"
    ):
        _load_with(tmp_path, "on")


def test_audio_processing_wrong_type_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="'audio_processing.echo_cancellation.stream_delay_ms' must be a int",
    ):
        _load_with(tmp_path, {"echo_cancellation": {"stream_delay_ms": "soon"}})


def test_audio_processing_rejects_bool_for_int_field(tmp_path: Path) -> None:
    # bool is an int subclass; stream_delay_ms must not silently accept True/False.
    with pytest.raises(
        ValueError,
        match="'audio_processing.echo_cancellation.stream_delay_ms' must be a int",
    ):
        _load_with(tmp_path, {"echo_cancellation": {"stream_delay_ms": True}})


def test_audio_processing_ns_level_out_of_range_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match="'audio_processing.noise_suppression.level' must be 0-3"
    ):
        _load_with(tmp_path, {"noise_suppression": {"level": 5}})


def test_a_misspelt_feature_is_rejected_not_read_as_off(tmp_path: Path) -> None:
    """The costly typo: `echo_cancelation` would otherwise load as "echo cancellation off", and the
    assistant would start answering its own voice with nothing on screen to say why."""
    with pytest.raises(ValueError, match="unknown key.*'echo_cancelation'"):
        _load_with(tmp_path, {"echo_cancelation": {"enabled": True}})


@pytest.mark.parametrize(
    ("section", "offender"),
    [
        ({"echo_cancellation": {"enabled": True, "delay_ms": 50}}, "delay_ms"),
        ({"noise_suppression": {"enable": True}}, "enable"),
    ],
)
def test_an_unknown_key_inside_a_feature_is_rejected(
    tmp_path: Path, section: dict[str, Any], offender: str
) -> None:
    with pytest.raises(ValueError, match=f"unknown key.*'{offender}'"):
        _load_with(tmp_path, section)


def test_a_feature_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    """`"echo_cancellation": false` reads like a switch, but it is not the schema — it must not be
    quietly taken for "all defaults"."""
    with pytest.raises(
        ValueError, match="'audio_processing.echo_cancellation' must be an object"
    ):
        _load_with(tmp_path, {"echo_cancellation": False})


def test_a_null_feature_keeps_its_defaults(tmp_path: Path) -> None:
    cfg = _load_with(tmp_path, {"echo_cancellation": None})
    assert cfg.audio_processing == AudioProcessingConfig()
