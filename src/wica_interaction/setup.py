"""The setup contracts of the config sections this app owns: ``vision``, ``audio_processing``
and ``camera`` (specs/setup.md, "The app's own contracts").

A library describes its own config document to app-setup through seven functions (its
``setup`` module: ``config_schema``, ``required_extras``, ``check_config``, ``check_setup``,
``prepare``, ``options``, ``probe``); no library owns these three sections, so the app writes
their contracts itself, in the same shape, as the objects ``VISION``, ``AUDIO_PROCESSING`` and
``CAMERA``. Like a library's, they import nothing from app-setup and return plain data.

**Fields are declared once.** Each section's fields are ``Field``s below. The section's loader
(``VisionConfig.from_dict``, ``CameraConfig.from_dict``, the audio-processing parser) reads
them through ``read_section``, raising ``ValueError`` on the first invalid one; ``check_config``
reports every invalid one with the same message; ``config_schema`` is drawn from them. So the
form, the check and the loader cannot disagree.

**The three sections are optional**, like ``asr``: absent from the config means off, present (any
document) means on (specs/configuration.md, "Optional sections"). Each has a **starting
document** here (``VISION_START``, ``AUDIO_PROCESSING_START``, ``CAMERA_START``): what the Setup tab
writes when the user adds the section.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from wica_interaction.fields import Field, field_problems, fields_schema, read_fields

__all__ = [
    "AUDIO_PROCESSING",
    "AUDIO_PROCESSING_FEATURES",
    "AUDIO_PROCESSING_START",
    "CAMERA",
    "CAMERA_FIELDS",
    "CAMERA_START",
    "ECHO_CANCELLATION_FIELDS",
    "NOISE_SUPPRESSION_FIELDS",
    "VISION",
    "VISION_ENABLED_GONE",
    "VISION_FIELDS",
    "VISION_START",
    "read_audio_processing",
    "read_section",
]

# The distribution the app's own requirements name (`wica-interaction[vision]`): app-setup
# maps a requirement on the host itself to that extra.
DISTRIBUTION = "wica-interaction"

# --- The fields ----------------------------------------------------------------------------

VISION_FIELDS: tuple[Field, ...] = (
    Field(
        "threshold",
        "number",
        "How sure the gesture classifier must be before a hand's sign counts, from 0 to 1.",
        default=0.55,
        minimum=0.0,
        maximum=1.0,
    ),
    Field(
        "fps",
        "number",
        "Frames per second read from the camera and run through the hand detector and the "
        "classifier.",
        default=10.0,
        exclusive_minimum=0.0,
        advanced=True,
    ),
    Field(
        "max_hands",
        "integer",
        "How many hands are detected and reported (one World entry each).",
        default=2,
        minimum=1,
        advanced=True,
    ),
    Field(
        "hold_s",
        "number",
        "Seconds a sign must be seen, unbroken, before the agent is told about it.",
        default=0.3,
        minimum=0.0,
        advanced=True,
    ),
    Field(
        "release_hold_s",
        "number",
        "Seconds a hand must show no sign, unbroken, before its sign counts as gone.",
        default=0.5,
        minimum=0.0,
        advanced=True,
    ),
    Field(
        "device",
        "string",
        "The torch device the classifier runs on (mps, cuda, cpu). Empty: the library picks.",
        advanced=True,
    ),
)

CAMERA_FIELDS: tuple[Field, ...] = (
    Field(
        "device",
        ("integer", "string"),
        "The webcam the hand signs are read from: its index (0 is the first camera) or a "
        "path or URL OpenCV can open.",
        default=0,
    ),
)

ECHO_CANCELLATION_FIELDS: tuple[Field, ...] = (
    Field(
        "enabled",
        "boolean",
        "Removes the app's own voice from the microphone, so the agent does not answer "
        "itself. Turn it off with a headset.",
        default=False,
    ),
    Field(
        "stream_delay_ms",
        "integer",
        "The delay between the speakers and the microphone, in milliseconds, the canceller "
        "starts from.",
        default=0,
        advanced=True,
    ),
)

NOISE_SUPPRESSION_FIELDS: tuple[Field, ...] = (
    Field(
        "enabled",
        "boolean",
        "Attenuates steady background noise in the microphone.",
        default=False,
    ),
    Field(
        "level",
        "integer",
        "How hard noise is suppressed, from 0 (mild) to 3 (strongest).",
        default=2,
        minimum=0,
        maximum=3,
    ),
)

# `vision` had an `enabled` switch; the section's presence is the switch now. A config still
# holding it is refused with this, rather than the field silently ignored (an `enabled: false`
# would then turn hand signs on).
VISION_ENABLED_GONE = (
    "config: vision.enabled is no longer a field: hand signs are on when the vision section "
    "is present and off when it is absent. Remove 'enabled' (and, to turn hand signs off, "
    "remove the whole vision section)."
)

# The starting documents: what the Setup tab writes into the config when the user adds the
# section (one an app wants on at first launch is in its default config instead). Hand signs at
# every default; the computer's microphone processing as its default config has it (echo
# cancellation on, at the delay the default config starts from); the first webcam.
VISION_START: dict[str, Any] = {}
AUDIO_PROCESSING_START: dict[str, Any] = {
    "echo_cancellation": {
        "enabled": True,
        "stream_delay_ms": 50,
    },
    "noise_suppression": {
        "enabled": False,
        "level": 2,
    },
}
CAMERA_START: dict[str, Any] = {
    "device": 0,
}

# The two features of `audio_processing`: name, title, description, fields.
AUDIO_PROCESSING_FEATURES: tuple[tuple[str, str, str, tuple[Field, ...]], ...] = (
    (
        "echo_cancellation",
        "Echo cancellation",
        "WebRTC echo cancellation, the app's voice as the reference.",
        ECHO_CANCELLATION_FIELDS,
    ),
    (
        "noise_suppression",
        "Noise suppression",
        "WebRTC noise suppression.",
        NOISE_SUPPRESSION_FIELDS,
    ),
)


# --- Reading a section ---------------------------------------------------------------------


def _label(name: str) -> str:
    return f"config: {name}"


def _object_problem(data: Any, name: str) -> str | None:
    if not isinstance(data, dict):
        return f"{_label(name)} must be an object"
    return None


def _unknown(data: Mapping[str, Any], known: Sequence[str]) -> list[str]:
    return sorted(set(data) - set(known))


def _unknown_message(name: str, unknown: Sequence[str]) -> str:
    keys = ", ".join(repr(k) for k in unknown)
    return f"{_label(name)} has unknown key(s): {keys}"


def read_section(data: Any, fields: Sequence[Field], *, name: str) -> dict[str, Any]:
    """The values of the section *name* (its defaults for an absent field), or ``ValueError``
    on the first problem: not an object, an unknown key, an invalid field. *name* is the
    section's dotted path (``audio_processing.echo_cancellation``), for the messages."""
    problem = _object_problem(data, name)
    if problem is not None:
        raise ValueError(problem)
    unknown = _unknown(data, [f.name for f in fields])
    if unknown:
        raise ValueError(_unknown_message(name, unknown))
    return read_fields(data, fields, label=_label(name))


def _section_problems(
    data: Any, fields: Sequence[Field], *, name: str, pointer: str = ""
) -> list[dict[str, Any]]:
    """Every problem of a section as the contract reports them: ``{"path", "message"}``."""
    problem = _object_problem(data, name)
    if problem is not None:
        return [{"path": pointer, "message": problem}]
    found = [
        {"path": f"{pointer}/{key}", "message": _unknown_message(name, [key])}
        for key in _unknown(data, [f.name for f in fields])
    ]
    found += [
        {"path": f"{pointer}/{field}", "message": message}
        for field, message in field_problems(data, fields, label=_label(name))
    ]
    return found


# --- What the contracts share ----------------------------------------------------------------


def _object_schema(
    title: str, description: str, fields: Sequence[Field]
) -> dict[str, Any]:
    schema = fields_schema(fields)
    return {
        "type": "object",
        "title": title,
        "description": description,
        "properties": schema["properties"],
        "required": schema["required"],
    }


def _description(title: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": 1,
        "distribution": DISTRIBUTION,
        "title": title,
        "schema": schema,
    }


def _extra_item(extra: str, try_imports: Callable[[], None]) -> dict[str, Any]:
    """The item of one of the app's extras, found by trying the imports it provides."""
    requirement = f"{DISTRIBUTION}[{extra}]"
    try:
        try_imports()
    except ImportError as exc:
        status = "missing"
        detail = (
            f"Not installed ({exc}): install the {extra} extra, "
            f"pip install '{requirement}'."
        )
    else:
        status, detail = "ready", "Installed."
    return {
        "id": f"extra:{requirement}",
        "title": f"The {extra} extra",
        "status": status,
        "detail": detail,
        "preparable": False,
        "required": True,
    }


def _probed(run: Callable[[], str]) -> dict[str, Any]:
    """``probe``'s result: *run* returns what it did, or raises what failed."""
    started = time.perf_counter()
    try:
        message = run()
    except Exception as exc:  # noqa: BLE001 - a probe never raises: the failure is its answer
        ok, message = False, str(exc) or type(exc).__name__
    else:
        ok = True
    return {
        "ok": ok,
        "message": message,
        "duration_s": round(time.perf_counter() - started, 3),
    }


def _no_item(item: str) -> ValueError:
    return ValueError(f"{item!r} is not a preparable item of this config")


# --- vision --------------------------------------------------------------------------------

_LANDMARKER = "model:hand-landmarker"
_CLASSIFIER = "model:gesture-classifier"


def _vision_imports() -> None:
    import cv2  # noqa: F401
    import mediapipe  # noqa: F401
    import torch  # noqa: F401
    import transformers  # noqa: F401


def _landmarker_path() -> Path:
    from vision_modules.hand import default_model_path

    return default_model_path()


def _landmarker_cached() -> bool:
    return _landmarker_path().is_file()


def _fetch_landmarker() -> None:
    from vision_modules.hand import DEFAULT_MODEL_URL, ensure_model

    ensure_model(_landmarker_path(), DEFAULT_MODEL_URL)


def _classifier_id() -> str:
    from vision_modules.gesture_classifier import HaGRIDViTClassifier

    return HaGRIDViTClassifier.MODEL_ID


def _classifier_cached() -> bool:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        snapshot_download(_classifier_id(), local_files_only=True)
    except LocalEntryNotFoundError:
        return False
    return True


def _fetch_classifier() -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(_classifier_id(), allow_patterns=["*.json", "*.safetensors"])


def _load_vision(max_hands: int, device: str | None) -> str:
    from vision_modules.gesture_classifier import HaGRIDViTClassifier
    from vision_modules.hand import MediaPipeHandDetector

    detector = MediaPipeHandDetector(num_hands=max_hands)
    try:
        classifier = HaGRIDViTClassifier(device=device or "cpu")
    finally:
        detector.close()
    del classifier
    return "Loaded the hand detector and the gesture classifier, then released them."


class _Vision:
    """The `vision` section: the hand-sign input (specs/configuration.md)."""

    # Seams the tests replace: the imports tried, each model's cache lookup and download.
    try_imports: Callable[[], None] = staticmethod(_vision_imports)
    models: ClassVar[
        dict[str, tuple[str, str, Callable[[], bool], Callable[[], None]]]
    ] = {
        _LANDMARKER: (
            "Hand detector model",
            "MediaPipe's hand landmarker (about 8 MB)",
            _landmarker_cached,
            _fetch_landmarker,
        ),
        _CLASSIFIER: (
            "Gesture classifier model",
            "the HaGRID gesture classifier (about 340 MB)",
            _classifier_cached,
            _fetch_classifier,
        ),
    }
    load: Callable[[int, str | None], str] = staticmethod(_load_vision)

    def config_schema(self) -> dict[str, Any]:
        return _description(
            "Hand signs",
            _object_schema(
                "Hand signs",
                "The hand signs the agent sees through the body's camera. Left out: no "
                "camera is read for them and no model is loaded.",
                VISION_FIELDS,
            ),
        )

    def required_extras(self, config: dict[str, Any]) -> list[str]:
        # The section present is hand signs on: they need the extra.
        return [f"{DISTRIBUTION}[vision]"]

    def check_config(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(config, dict) and "enabled" in config:
            rest = {k: v for k, v in config.items() if k != "enabled"}
            return [
                {"path": "/enabled", "message": VISION_ENABLED_GONE},
                *_section_problems(rest, VISION_FIELDS, name="vision"),
            ]
        return _section_problems(config, VISION_FIELDS, name="vision")

    def _model_item(self, item_id: str) -> dict[str, Any]:
        title, what, cached, _ = self.models[item_id]
        try:
            ready = cached()
        except Exception as exc:  # noqa: BLE001 - a cache it cannot read is unknown, not a raise
            status, detail = "unknown", f"Could not look it up ({exc})."
        else:
            status = "ready" if ready else "missing"
            detail = (
                "Downloaded."
                if ready
                else f"Not downloaded yet: {what}. The first session downloads it."
            )
        return {
            "id": item_id,
            "title": title,
            "status": status,
            "detail": detail,
            "preparable": True,
            "required": False,
        }

    def check_setup(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        extra = _extra_item("vision", self.try_imports)
        if extra["status"] != "ready":
            return [extra]
        return [extra, *(self._model_item(item_id) for item_id in self.models)]

    def prepare(
        self,
        config: dict[str, Any],
        item: str,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        base_dir: str | None = None,
    ) -> None:
        if item not in self.models:
            raise _no_item(item)
        title, _, cached, fetch = self.models[item]
        if cached():
            return
        if on_progress is not None:
            on_progress(
                {"fraction": None, "message": f"Downloading the {title.lower()}"}
            )
        fetch()
        if on_progress is not None:
            on_progress({"fraction": 1.0, "message": "Downloaded"})

    def options(
        self, name: str, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    def probe(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> dict[str, Any]:
        def run() -> str:
            values = read_section(config, VISION_FIELDS, name="vision")
            return self.load(values["max_hands"], values["device"])

        return _probed(run)


# --- audio_processing ----------------------------------------------------------------------


def _audio_processing_imports() -> None:
    import aec_audio_processing  # noqa: F401
    import samplerate  # noqa: F401


def _feature_on(config: Any, feature: str) -> bool:
    block = config.get(feature) if isinstance(config, dict) else None
    return isinstance(block, dict) and block.get("enabled") is True


def _build_processor(values: Mapping[str, dict[str, Any]]) -> str:
    from wica_interaction.computer.audio_processing import WebRtcAudioProcessor

    echo = values["echo_cancellation"]
    noise = values["noise_suppression"]
    WebRtcAudioProcessor(
        echo_cancellation=echo["enabled"],
        stream_delay_ms=echo["stream_delay_ms"],
        noise_suppression=noise["enabled"],
        ns_level=noise["level"],
    )
    return "Built the WebRTC audio processor with these settings, then released it."


def read_audio_processing(data: Any) -> dict[str, dict[str, Any]]:
    """The values of `audio_processing`, feature by feature (an absent or ``null`` feature at
    its defaults), or ``ValueError`` on the first problem. Absent or ``null`` section: every
    feature at its defaults (off)."""
    if data is None:
        data = {}
    problem = _object_problem(data, "audio_processing")
    if problem is not None:
        raise ValueError(problem)
    unknown = _unknown(data, [f[0] for f in AUDIO_PROCESSING_FEATURES])
    if unknown:
        raise ValueError(_unknown_message("audio_processing", unknown))
    values: dict[str, dict[str, Any]] = {}
    for feature, _, _, fields in AUDIO_PROCESSING_FEATURES:
        block = data.get(feature)
        values[feature] = read_section(
            {} if block is None else block, fields, name=f"audio_processing.{feature}"
        )
    return values


class _AudioProcessing:
    """The computer's `audio_processing` section (specs/audio-processing.md)."""

    try_imports: Callable[[], None] = staticmethod(_audio_processing_imports)
    build: Callable[[Mapping[str, dict[str, Any]]], str] = staticmethod(
        _build_processor
    )

    def config_schema(self) -> dict[str, Any]:
        properties = {
            feature: _object_schema(title, description, fields)
            for feature, title, description, fields in AUDIO_PROCESSING_FEATURES
        }
        return _description(
            "Microphone processing",
            {
                "type": "object",
                "title": "Microphone processing",
                "description": "Echo cancellation and noise suppression on the microphone, "
                "before speech recognition, each on by its own switch. Left out: the "
                "microphone goes to speech recognition as it is.",
                "properties": properties,
                "required": [],
            },
        )

    def required_extras(self, config: dict[str, Any]) -> list[str]:
        if any(_feature_on(config, f[0]) for f in AUDIO_PROCESSING_FEATURES):
            return [f"{DISTRIBUTION}[audio-processing]"]
        return []

    def check_config(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        problem = _object_problem(config, "audio_processing")
        if problem is not None:
            return [{"path": "", "message": problem}]
        names = [f[0] for f in AUDIO_PROCESSING_FEATURES]
        found = [
            {"path": f"/{key}", "message": _unknown_message("audio_processing", [key])}
            for key in _unknown(config, names)
        ]
        for feature, _, _, fields in AUDIO_PROCESSING_FEATURES:
            block = config.get(feature)
            if block is not None:
                found += _section_problems(
                    block,
                    fields,
                    name=f"audio_processing.{feature}",
                    pointer=f"/{feature}",
                )
        return found

    def check_setup(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.required_extras(config):
            return []
        return [_extra_item("audio-processing", self.try_imports)]

    def prepare(
        self,
        config: dict[str, Any],
        item: str,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        base_dir: str | None = None,
    ) -> None:
        raise _no_item(item)

    def options(
        self, name: str, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    def probe(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> dict[str, Any]:
        if not self.required_extras(config):
            return _probed(lambda: "Both features are off: nothing to run.")
        return _probed(lambda: self.build(read_audio_processing(config)))


# --- camera --------------------------------------------------------------------------------


def _read_one_frame(device: int | str) -> str:
    from vision_modules.stream import OpenCVSource

    source = OpenCVSource(device)
    try:
        source.open()
        frame = None
        deadline = time.monotonic() + 3.0
        while frame is None and time.monotonic() < deadline:
            frame = source.read()
            if frame is None:
                time.sleep(0.05)
    finally:
        source.close()
    if frame is None:
        raise RuntimeError(f"Camera {device!r} opened but gave no frame within 3 s.")
    height, width = frame.shape[:2]
    return f"Read a {width}x{height} frame from camera {device!r}, then released it."


class _Camera:
    """The computer's `camera` section: the webcam hand signs are read from."""

    read_frame: Callable[[int | str], str] = staticmethod(_read_one_frame)

    def config_schema(self) -> dict[str, Any]:
        return _description(
            "Camera",
            _object_schema(
                "Camera",
                "The webcam the hand signs are read from. Left out: no webcam is opened, "
                "and hand signs cannot be on.",
                CAMERA_FIELDS,
            ),
        )

    def required_extras(self, config: dict[str, Any]) -> list[str]:
        return []

    def check_config(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        return _section_problems(config, CAMERA_FIELDS, name="camera")

    def check_setup(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    def prepare(
        self,
        config: dict[str, Any],
        item: str,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        base_dir: str | None = None,
    ) -> None:
        raise _no_item(item)

    def options(
        self, name: str, config: dict[str, Any], *, base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    def probe(
        self, config: dict[str, Any], *, base_dir: str | None = None
    ) -> dict[str, Any]:
        return _probed(
            lambda: self.read_frame(
                read_section(config, CAMERA_FIELDS, name="camera")["device"]
            )
        )


VISION = _Vision()
AUDIO_PROCESSING = _AudioProcessing()
CAMERA = _Camera()
