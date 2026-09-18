"""The computer embodiment's config: the shared sections plus its voice, mic processing and webcam.

``load_config`` reads the file once, validates the shared part through
``wica_interaction.config.load_app_config`` (which also rejects any section outside this
embodiment's set — a ``reachy`` section in a computer file is an error, not a silent no-op), and
parses this embodiment's own: ``tts`` (required, delegated verbatim to tts-engine's loader),
``audio_processing`` (optional, app-owned — it is a bridge between two engines, not an engine) and
``camera`` (optional, the webcam the gesture input reads). See specs/computer-embodiment.md and
specs/audio-processing.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tts_engine.config import TTSEngineConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    engine_block,
    load_app_config,
    read_config_file,
)

# Every top-level key a computer config file may carry.
SECTIONS = SHARED_SECTIONS | {"tts", "audio_processing", "camera"}

_CAMERA_KEYS = frozenset({"device"})
_AUDIO_PROCESSING_KEYS = frozenset({"echo_cancellation", "noise_suppression"})
_ECHO_CANCELLATION_KEYS = frozenset({"enabled", "stream_delay_ms"})
_NOISE_SUPPRESSION_KEYS = frozenset({"enabled", "level"})


@dataclass(frozen=True)
class EchoCancellationConfig:
    """Echo-cancellation feature settings (a sub-section of ``audio_processing``)."""

    enabled: bool = False
    stream_delay_ms: int = 0


@dataclass(frozen=True)
class NoiseSuppressionConfig:
    """Noise-suppression feature settings; ``level`` is the WebRTC aggressiveness 0 (mild)–3 (max)."""

    enabled: bool = False
    level: int = 2


@dataclass(frozen=True)
class AudioProcessingConfig:
    """Mic/playback audio processing — app-owned, not delegated to an engine loader.

    Unlike the engine sections, this is a bridge the embodiment builds itself, so its (optional)
    ``audio_processing`` section is parsed here. It groups the independent features that share the
    one WebRTC processor: ``echo_cancellation`` (removes the app's own TTS from the mic) and
    ``noise_suppression`` (attenuates ambient noise). Absent ⇒ all defaults ⇒ everything disabled.
    See specs/audio-processing.md.
    """

    echo_cancellation: EchoCancellationConfig = field(
        default_factory=EchoCancellationConfig
    )
    noise_suppression: NoiseSuppressionConfig = field(
        default_factory=NoiseSuppressionConfig
    )

    @property
    def any_enabled(self) -> bool:
        """Whether any feature is on — i.e. whether the processing coordinator should be built."""
        return self.echo_cancellation.enabled or self.noise_suppression.enabled


@dataclass(frozen=True)
class CameraConfig:
    """The optional `camera` section: the webcam `GestureInput` reads when `vision.enabled`.

    `device` is whatever `cv2.VideoCapture` takes — an index (default `0`) or a path/URL. Absent ⇒
    device `0`. The microphone needs no section here: it is the ASR config's own
    `asr.engine.audio.device`, which the engine opens itself.
    """

    device: int | str = 0

    @classmethod
    def from_dict(cls, data: Any, *, path: str) -> CameraConfig:
        if not isinstance(data, dict):
            # ValueError (not TypeError) keeps one uniform error surface for a malformed file.
            message = f"config file {path!r}: section 'camera' must be an object"
            raise ValueError(message)  # noqa: TRY004
        unknown = sorted(set(data) - _CAMERA_KEYS)
        if unknown:
            raise ValueError(
                f"config file {path!r}: section 'camera' has unknown key(s): "
                f"{', '.join(repr(k) for k in unknown)}"
            )
        device = data.get("device", cls.device)
        # bool is a subclass of int, so guard the index from silently accepting True/False.
        if isinstance(device, bool) or not isinstance(device, (int, str)):
            message = (
                f"config file {path!r}: camera.device must be an integer or a string"
            )
            raise ValueError(message)  # noqa: TRY004
        return cls(device=device)


@dataclass
class ComputerConfig:
    """This embodiment's whole config: the shared sections plus its voice, processing and webcam."""

    app: AppConfig
    tts_engine: TTSEngineConfig
    audio_processing: AudioProcessingConfig
    camera: CameraConfig


def load_config(path: str) -> ComputerConfig:
    """Load the composed config at *path* for the computer embodiment.

    Raises ``ValueError`` if the file is missing, is not a JSON object, lacks a required section
    (``wica``, ``asr``, ``tts``), or carries a section this embodiment does not know. Field-level
    errors propagate from the owners' loaders unchanged (tts-engine's ``ConfigError`` is a
    ``ValueError`` subclass, so the one error type holds for ``tts`` too).
    """
    data = read_config_file(path)
    app = load_app_config(data, path=path, sections=SECTIONS)
    if "tts" not in data:
        raise ValueError(f"config file {path!r} is missing required section(s): 'tts'")
    return ComputerConfig(
        app=app,
        tts_engine=TTSEngineConfig.from_dict(
            engine_block(data["tts"], name="tts", path=path)
        ),
        audio_processing=_audio_processing_config(
            data.get("audio_processing"), path=path
        ),
        camera=(
            CameraConfig.from_dict(data["camera"], path=path)
            if data.get("camera") is not None
            else CameraConfig()
        ),
    )


def _typed(
    section: dict[str, Any], key: str, expected: type, default: Any, *, path: str
) -> Any:
    """Read ``section[key]`` (or *default*), asserting its type; ``key`` is a dotted path for errors."""
    value = section.get(key.rsplit(".", 1)[-1], default)
    # bool is a subclass of int, so guard int fields from silently accepting True/False.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ValueError(
            f"config file {path!r}: '{key}' must be a {expected.__name__}, got {value!r}"
        )
    return value


def _reject_unknown(
    section: dict[str, Any], known: frozenset[str], name: str, *, path: str
) -> None:
    unknown = sorted(set(section) - known)
    if unknown:
        raise ValueError(
            f"config file {path!r}: section {name!r} has unknown key(s): "
            f"{', '.join(repr(k) for k in unknown)}"
        )


def _feature_section(section: dict[str, Any], key: str, *, path: str) -> dict[str, Any]:
    """One feature's sub-section: absent or ``null`` ⇒ ``{}`` (all defaults); anything else that is
    not an object — ``false``, a list — is an error rather than a silent "off"."""
    raw = section.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004
            f"config file {path!r}: 'audio_processing.{key}' must be an object"
        )
    return raw


def _audio_processing_config(section: Any, *, path: str) -> AudioProcessingConfig:
    """Parse the optional ``audio_processing`` section (absent ⇒ all features disabled)."""
    if section is None:
        return AudioProcessingConfig()
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004
            f"config file {path!r}: section 'audio_processing' must be an object"
        )

    # Strict like every other app-owned section: a misspelt `echo_cancelation` must not load as
    # "echo cancellation off" — the assistant would then answer its own voice with nothing to say why.
    _reject_unknown(section, _AUDIO_PROCESSING_KEYS, "audio_processing", path=path)
    aec_raw = _feature_section(section, "echo_cancellation", path=path)
    ns_raw = _feature_section(section, "noise_suppression", path=path)
    _reject_unknown(
        aec_raw,
        _ECHO_CANCELLATION_KEYS,
        "audio_processing.echo_cancellation",
        path=path,
    )
    _reject_unknown(
        ns_raw, _NOISE_SUPPRESSION_KEYS, "audio_processing.noise_suppression", path=path
    )

    echo = EchoCancellationConfig(
        enabled=_typed(
            aec_raw,
            "audio_processing.echo_cancellation.enabled",
            bool,
            False,
            path=path,
        ),
        stream_delay_ms=_typed(
            aec_raw,
            "audio_processing.echo_cancellation.stream_delay_ms",
            int,
            0,
            path=path,
        ),
    )
    level = _typed(
        ns_raw, "audio_processing.noise_suppression.level", int, 2, path=path
    )
    if not 0 <= level <= 3:
        raise ValueError(
            f"config file {path!r}: 'audio_processing.noise_suppression.level' must be 0-3, "
            f"got {level}"
        )
    noise = NoiseSuppressionConfig(
        enabled=_typed(
            ns_raw, "audio_processing.noise_suppression.enabled", bool, False, path=path
        ),
        level=level,
    )
    return AudioProcessingConfig(echo_cancellation=echo, noise_suppression=noise)
