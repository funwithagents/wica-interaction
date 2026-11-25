"""The computer embodiment's config: the shared sections plus its voice, mic processing and webcam.

``load_config`` takes the config document (the app's `config.json`, as app-setup read it) and the
app's folder, validates the shared part through ``wica_interaction.config.load_app_config`` (which
also rejects any section outside this embodiment's set — a ``reachy`` section in a computer config
is an error, not a silent no-op), and parses this embodiment's own: ``tts`` (required, tts-engine's
engine block handed verbatim to its loader), ``audio_processing`` (optional, app-owned — it is a
bridge between two engines, not an engine) and ``camera`` (optional, the webcam the gesture input
reads). Optional means **absent is off**: no processing, no webcam. The two app-owned sections read
the fields their setup contracts declare (setup.py). One rule spans two sections — hand signs need
the webcam — and is checked here, over the whole document (``config_problems``). See
specs/computer-embodiment.md and specs/audio-processing.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tts_engine.config import TTSEngineConfig

from wica_interaction.config import (
    SHARED_SECTIONS,
    AppConfig,
    load_app_config,
    section_object,
)
from wica_interaction.setup import CAMERA_FIELDS, read_audio_processing, read_section

# Every top-level key a computer config may carry.
SECTIONS = SHARED_SECTIONS | {"tts", "audio_processing", "camera"}


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
    ``noise_suppression`` (attenuates ambient noise), each on by its own ``enabled`` (default off).
    The section absent ⇒ no processing at all (``ComputerConfig.audio_processing`` is ``None``).
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
    """The optional `camera` section: the webcam `GestureInput` reads when hand signs are on.

    `device` is whatever `cv2.VideoCapture` takes — an index (default `0`) or a path/URL. The section
    absent ⇒ no webcam (``ComputerConfig.camera`` is ``None``), so hand signs cannot be on
    (``config_problems``). The microphone needs no section here: it is the ASR config's own
    `asr.audio_source.device`, opened by the engine itself, or by the audio processing when
    it is on.
    """

    device: int | str = 0

    @classmethod
    def from_dict(cls, data: Any) -> CameraConfig:
        """Read the section; ``ValueError`` on the first problem, worded as the setup contract's
        ``check_config`` words it."""
        return cls(**read_section(data, CAMERA_FIELDS, name="camera"))


@dataclass
class ComputerConfig:
    """This embodiment's whole config: the shared sections plus its voice, processing and webcam;
    ``None`` for an optional section the config leaves out (off)."""

    app: AppConfig
    tts_engine: TTSEngineConfig
    audio_processing: AudioProcessingConfig | None = None
    camera: CameraConfig | None = None


# The cross-section rule, in the words the page shows it.
VISION_NEEDS_CAMERA = "Hand signs need a camera: add the Camera section, or remove the Hand signs section."


def config_problems(data: Mapping[str, Any]) -> list[str]:
    """What is wrong *across* sections of the computer's config document *data*, in plain words;
    empty when nothing is. Each section's own problems are its contract's; this is the one rule
    that relates two of them: hand signs (``vision`` present) read the webcam, so the ``camera``
    section must be there too. An absent and a ``null`` section are alike (app-setup hands every
    declared section, an absent one as ``None``). Start refuses a config with a problem here and the
    session bar says why (specs/computer-embodiment.md, "Hand signs need the camera")."""
    if data.get("vision") is not None and data.get("camera") is None:
        return [VISION_NEEDS_CAMERA]
    return []


def load_config(data: Mapping[str, Any], *, base_dir: str | Path) -> ComputerConfig:
    """Load the composed config document *data* for the computer embodiment, relative paths
    against *base_dir* (the app's folder).

    Raises ``ValueError`` if a required section (``wica``, ``tts``) is missing, the document
    carries a section this embodiment does not know, or a rule across sections fails
    (``config_problems``: hand signs without the camera). An optional section absent is ``None``. Field-level errors propagate from
    the owners' loaders unchanged (tts-engine's ``ConfigError`` is a ``ValueError`` subclass, so
    the one error type holds for ``tts`` too).
    """
    app = load_app_config(data, base_dir=base_dir, sections=SECTIONS)
    if data.get("tts") is None:
        raise ValueError("config: missing required section(s): 'tts'")
    problems = config_problems(data)
    if problems:
        raise ValueError(f"config: {problems[0]}")
    return ComputerConfig(
        app=app,
        tts_engine=TTSEngineConfig.from_dict(
            section_object(data["tts"], name="tts"), base_dir=str(base_dir)
        ),
        audio_processing=(
            _audio_processing_config(data["audio_processing"])
            if data.get("audio_processing") is not None
            else None
        ),
        camera=(
            CameraConfig.from_dict(data["camera"])
            if data.get("camera") is not None
            else None
        ),
    )


def _audio_processing_config(section: Any) -> AudioProcessingConfig:
    """Parse a present ``audio_processing`` section (a feature left out ⇒ that feature off). Strict
    like every other app-owned section: a misspelt `echo_cancelation` must not load as "echo
    cancellation off" — the assistant would then answer its own voice with nothing to say why."""
    values = read_audio_processing(section)
    return AudioProcessingConfig(
        echo_cancellation=EchoCancellationConfig(**values["echo_cancellation"]),
        noise_suppression=NoiseSuppressionConfig(**values["noise_suppression"]),
    )
