"""The **shared** sections of the composed config: `wica`, `asr` and the optional `vision`.

One JSON file stands the app up, and this module owns the part every embodiment has in common —
reading the file, rejecting a section no embodiment declares, and turning the shared sections into
the ``AppConfig`` the lifecycle (system.py) is built from. Each section holds exactly the document
its owner's own loader expects and is handed to that loader's ``from_dict`` constructor, so all
field-level validation and defaults come from the owner, not from here; ``vision`` is the one
schema the app owns itself (`vision-modules` has no config document). Each embodiment's own loader
(`computer/config.py`, `reachy/config.py`) reads the file once, calls ``load_app_config`` with its
section set, and parses its own sections alongside. See specs/configuration.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from asr_engine.config import ASREngineConfig
from wica.config import WicaConfig

# The top-level keys every embodiment's config may carry, whatever its body; each embodiment's
# loader passes these plus its own to `load_app_config` (specs/configuration.md, "Loading").
SHARED_SECTIONS = frozenset({"wica", "asr", "vision"})

_REQUIRED_SECTIONS = ("wica", "asr")

_VISION_KEYS = frozenset({"enabled", "threshold", "fps", "max_hands", "device"})


@dataclass(frozen=True)
class VisionConfig:
    """The optional `vision` section: the hand-sign input over the robot camera (see
    specs/gesture-input.md). `vision-modules` has no config loader of its own, so this is the app's
    own small schema — its defaults are the library's (threshold 0.55, two hands) and the app's
    (5 fps for the camera poll, hand stage, and classifier alike; `device` None lets the classifier pick
    mps → cuda → cpu). `enabled` is the switch, off by default: the section can stay in the file
    (tuned) with vision off, and flipping it on is a one-key edit."""

    enabled: bool = False
    threshold: float = 0.55
    fps: float = 5.0
    max_hands: int = 2
    device: str | None = None

    @classmethod
    def from_dict(cls, data: Any, *, path: str) -> VisionConfig:
        if not isinstance(data, dict):
            # ValueError (not TypeError) keeps one uniform error surface for a malformed file.
            message = f"config file {path!r}: section 'vision' must be an object"
            raise ValueError(message)  # noqa: TRY004
        unknown = sorted(set(data) - _VISION_KEYS)
        if unknown:
            raise ValueError(
                f"config file {path!r}: section 'vision' has unknown key(s): "
                f"{', '.join(repr(k) for k in unknown)}"
            )
        enabled = data.get("enabled", cls.enabled)
        threshold = data.get("threshold", cls.threshold)
        fps = data.get("fps", cls.fps)
        max_hands = data.get("max_hands", cls.max_hands)
        device = data.get("device", cls.device)
        if not isinstance(enabled, bool):
            message = f"config file {path!r}: vision.enabled must be a boolean"
            raise ValueError(message)  # noqa: TRY004
        if not _is_number(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"config file {path!r}: vision.threshold must be a number in [0, 1]"
            )
        if not _is_number(fps) or fps <= 0:
            raise ValueError(f"config file {path!r}: vision.fps must be a number > 0")
        if not _is_number(max_hands) or isinstance(max_hands, float) or max_hands < 1:
            raise ValueError(
                f"config file {path!r}: vision.max_hands must be an integer >= 1"
            )
        if device is not None and not isinstance(device, str):
            raise ValueError(
                f"config file {path!r}: vision.device must be a string or null"
            )
        return cls(
            enabled=enabled,
            threshold=float(threshold),
            fps=float(fps),
            max_hands=max_hands,
            device=device,
        )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class AppConfig:
    """The shared sections — what the lifecycle (system.py) is built from, whatever the embodiment.

    One live engine config per section, plus the optional vision section (`None`, or `enabled` false
    ⇒ no gesture input). Each embodiment's own config object holds this as its ``app`` field and adds
    its own sections next to it (specs/configuration.md).
    """

    wica: WicaConfig
    asr_engine: ASREngineConfig
    vision: VisionConfig | None = None


def read_config_file(path: str) -> dict[str, Any]:
    """Read and JSON-parse the config file at *path* into the raw object its loader validates.

    The first of the two loading layers (specs/configuration.md): every embodiment's ``load_config``
    calls this once, then hands the result to ``load_app_config`` plus its own section parsers, so
    the file is read exactly once. Raises ``ValueError`` if it is missing, unreadable, not valid
    JSON, or not a JSON object — the one error type ``run_app`` catches to open in degraded mode.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise ValueError(f"could not read config file {path!r}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in config file {path!r}: {exc}") from exc

    if not isinstance(data, dict):
        # ValueError (not TypeError) keeps one uniform error surface for any malformed config file.
        raise ValueError(f"config file {path!r} must contain a JSON object")  # noqa: TRY004

    return data


def load_app_config(
    data: dict[str, Any], *, path: str, sections: frozenset[str]
) -> AppConfig:
    """Validate the shared part of an already-parsed config object into an ``AppConfig``.

    *sections* is every top-level key the calling embodiment accepts — ``SHARED_SECTIONS`` plus its
    own — so a key outside it is rejected here rather than silently ignored: a reachy file handed to
    the computer command fails on ``'reachy'``. Then each shared section is delegated to its owner's
    ``from_dict`` (no temp files): WICA's is located against the config file's own directory so a
    relative ``system_prompt_file`` resolves there, exactly as ``WicaConfig.from_json`` would. No env
    var or prompt file is read here — that stays deferred to engine build — so this loads even with
    credentials unset.

    Raises ``ValueError`` for an unknown section or a missing required one; field-level errors
    propagate from the owners' loaders unchanged.
    """
    unknown = sorted(set(data) - sections)
    if unknown:
        raise ValueError(
            f"config file {path!r}: unknown section(s): "
            f"{', '.join(repr(k) for k in unknown)}"
        )

    missing = [key for key in _REQUIRED_SECTIONS if key not in data]
    if missing:
        raise ValueError(
            f"config file {path!r} is missing required section(s): "
            f"{', '.join(repr(k) for k in missing)}"
        )

    return AppConfig(
        wica=WicaConfig.from_dict(data["wica"], base_dir=os.path.dirname(path)),
        asr_engine=ASREngineConfig.from_dict(
            engine_block(data["asr"], name="asr", path=path)
        ),
        vision=(
            VisionConfig.from_dict(data["vision"], path=path)
            if data.get("vision") is not None
            else None
        ),
    )


def engine_block(section: Any, *, name: str, path: str) -> dict[str, Any]:
    """Return the `engine` block of an engine section, or raise a clear error about its shape.

    Public because every embodiment's loader calls it for its own engine sections (the computer's
    ``tts``), the same way this module calls it for ``asr``."""
    if not isinstance(section, dict) or "engine" not in section:
        raise ValueError(
            f"config file {path!r}: section {name!r} must be an object with an 'engine' block"
        )
    return section["engine"]
