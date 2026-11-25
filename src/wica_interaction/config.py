"""The **shared** sections of the composed config: `wica`, and the optional `asr` and `vision`.

One config stands each app up — the `config.json` of its folder, handed here as the document
app-setup read (`AppModel.used_config`) — and this module owns the part every embodiment has in
common: rejecting a section the embodiment does not declare, and turning the shared sections into
the ``AppConfig`` the lifecycle (system.py) is built from. Each section holds exactly the document
its owner's own loader expects (the one its setup contract describes) and is handed to that
loader's ``from_dict`` with the app's folder as ``base_dir``, so all field-level validation,
defaults and relative paths come from the owner, not from here; ``vision`` is the one schema the
app owns itself (`vision-modules` has no config document), read from the fields its setup
contract declares (setup.py). `wica` is required; `asr` and `vision` are optional, and **absent
means off**: no `asr` section, no speech input; no `vision` section, no hand signs (present, any
document, means on). Each embodiment's own loader (`computer/config.py`,
`reachy/config.py`) calls ``load_app_config`` with its section set and parses its own sections
alongside. See specs/configuration.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asr_engine.config import ASREngineConfig
from wica.config import WicaConfig

from wica_interaction.setup import VISION_ENABLED_GONE, VISION_FIELDS, read_section

# The top-level keys every embodiment's config may carry, whatever its body; each embodiment's
# loader passes these plus its own to `load_app_config` (specs/configuration.md, "Loading").
SHARED_SECTIONS = frozenset({"wica", "asr", "vision"})
_REQUIRED_SECTIONS = ("wica",)


@dataclass(frozen=True)
class VisionConfig:
    """The optional `vision` section: the hand-sign input over the embodiment's camera (see
    specs/gesture-input.md). `vision-modules` has no config loader of its own, so this is the app's
    own small schema, its fields declared once in setup.py (``VISION_FIELDS``) for this loader, the
    setup contract's check and the form alike — its defaults are the library's (threshold 0.55, two
    hands) and the app's (10 fps for the camera poll, hand stage, and classifier alike; a 0.3 s hold
    before a sign counts and a 0.5 s one before it counts as gone; `device` None lets the classifier
    pick mps → cuda → cpu). There is no switch: the section present means hand signs are on,
    absent means off (specs/configuration.md, "Optional sections"); a document still holding the
    retired `enabled` field is refused, saying so."""

    threshold: float = 0.55
    fps: float = 10.0
    max_hands: int = 2
    hold_s: float = 0.3
    release_hold_s: float = 0.5
    device: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> VisionConfig:
        """Read the section; ``ValueError`` on the first problem (not an object, an unknown key,
        an invalid field, the retired `enabled`), worded as the setup contract's ``check_config``
        words it."""
        if isinstance(data, dict) and "enabled" in data:
            raise ValueError(VISION_ENABLED_GONE)
        return cls(**read_section(data, VISION_FIELDS, name="vision"))


@dataclass
class AppConfig:
    """The shared sections — what the lifecycle (system.py) is built from, whatever the embodiment.

    One live engine config per section. The optional ones are ``None`` when their section is absent
    from the config, and absent means off: no ``asr_engine`` ⇒ no speech input (the agent is driven by
    typing, and hand signs); no ``vision`` ⇒ no gesture input. Each embodiment's own config object
    holds this as its ``app`` field and adds its own sections next to it (specs/configuration.md).
    """

    wica: WicaConfig
    asr_engine: ASREngineConfig | None = None
    vision: VisionConfig | None = None


def load_app_config(
    data: Mapping[str, Any], *, base_dir: str | Path, sections: frozenset[str]
) -> AppConfig:
    """Validate the shared part of a config document into an ``AppConfig``.

    *sections* is every top-level key the calling embodiment accepts — ``SHARED_SECTIONS`` plus its
    own — so a key outside it is rejected here rather than silently ignored: a robot's config handed
    to the computer's loader fails on ``'reachy'``. Then each shared section is delegated to its
    owner's ``from_dict`` with *base_dir*, the app's folder, so a relative path in it (the persona's
    ``system_prompt_file``, an audio file) resolves there. No env var or prompt file is read here —
    that stays deferred to engine build — so this loads even with credentials unset.

    An optional section (``asr``, ``vision``) absent or ``null`` is off: ``None`` in the result.

    Raises ``ValueError`` for an unknown section or a missing required one (``wica``); field-level
    errors propagate from the owners' loaders unchanged.
    """
    unknown = sorted(set(data) - sections)
    if unknown:
        raise ValueError(
            f"config: unknown section(s): {', '.join(repr(k) for k in unknown)}"
        )
    # Absent and `null` alike: app-setup's `used_config` gives every declared section, an absent
    # one as `None`.
    missing = [key for key in _REQUIRED_SECTIONS if data.get(key) is None]
    if missing:
        raise ValueError(
            "config: missing required section(s): "
            f"{', '.join(repr(k) for k in missing)}"
        )
    folder = str(base_dir)
    return AppConfig(
        wica=WicaConfig.from_dict(data["wica"], base_dir=folder),
        asr_engine=(
            ASREngineConfig.from_dict(
                section_object(data["asr"], name="asr"), base_dir=folder
            )
            if data.get("asr") is not None
            else None
        ),
        vision=(
            VisionConfig.from_dict(data["vision"])
            if data.get("vision") is not None
            else None
        ),
    )


def section_object(section: Any, *, name: str) -> dict[str, Any]:
    """*section* when it is an object, else a clear error about its shape — before an owner's
    loader, which would say less. Public because every embodiment's loader calls it for its own
    engine sections (the computer's ``tts``), the same way this module calls it for ``asr``."""
    if not isinstance(section, dict):
        raise ValueError(f"config: section {name!r} must be an object")  # noqa: TRY004
    return section
