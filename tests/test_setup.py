"""The app's own setup contracts, and the requirements every section can name (specs/setup.md).

Drives `VISION`, `AUDIO_PROCESSING` and `CAMERA` the way app-setup does — the conformance check,
a draft's problems, the items a machine misses, one item prepared, a probe — with the imports, the
model caches and the devices replaced, so nothing is downloaded or opened. Then the carrier test:
every requirement a section of either app can return is installed by one of this project's extras.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app_setup.contract import check_contract
from app_setup.extras import host_extras

from wica_interaction.computer.app import DEFAULT_CONFIG as COMPUTER_DEFAULT
from wica_interaction.computer.app import SECTIONS as COMPUTER_SECTIONS
from wica_interaction.computer.config import CameraConfig
from wica_interaction.config import VisionConfig
from wica_interaction.reachy.app import DEFAULT_CONFIG as REACHY_DEFAULT
from wica_interaction.reachy.app import SECTIONS as REACHY_SECTIONS
from wica_interaction.setup import AUDIO_PROCESSING, CAMERA, VISION

_E2E = Path(__file__).resolve().parents[1] / "tests-e2e" / "configs"


def _e2e(name: str) -> dict[str, Any]:
    return json.loads((_E2E / f"{name}.json").read_text(encoding="utf-8"))


def _examples(section: str) -> list[dict[str, Any]]:
    """The section as the committed configs hold it: both defaults, both end-to-end configs."""
    configs = [COMPUTER_DEFAULT, REACHY_DEFAULT, _e2e("computer"), _e2e("reachy")]
    return [c[section] for c in configs if isinstance(c.get(section), dict)]


# --- Conformance ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contract", "section"),
    [(VISION, "vision"), (AUDIO_PROCESSING, "audio_processing"), (CAMERA, "camera")],
)
def test_each_contract_honours_app_setups_conformance_check(
    contract: Any, section: str
) -> None:
    examples = _examples(section) or [{}]
    check_contract(contract, examples)


@pytest.mark.parametrize(
    ("sections", "config"),
    [(COMPUTER_SECTIONS, COMPUTER_DEFAULT), (REACHY_SECTIONS, REACHY_DEFAULT)],
    ids=["computer", "reachy"],
)
def test_every_section_of_each_default_config_passes_its_contract(
    sections: Any, config: dict[str, Any]
) -> None:
    for section in sections:
        assert section.contract.check_config(config.get(section.name, {})) == [], (
            section.name
        )


# --- A draft's problems, worded as the loaders word them --------------------------------------


@pytest.mark.parametrize(
    ("document", "pointer"),
    [
        ({"fps": 0}, "/fps"),
        ({"threshold": 1.5}, "/threshold"),
        ({"max_hands": 2.0}, "/max_hands"),
        ({"enabled": "yes"}, "/enabled"),
        ({"mirror": True}, "/mirror"),
    ],
)
def test_a_vision_problem_is_at_its_pointer_with_the_loaders_message(
    document: dict[str, Any], pointer: str
) -> None:
    [problem] = VISION.check_config(document)
    assert problem["path"] == pointer
    with pytest.raises(ValueError) as refused:
        VisionConfig.from_dict(document)
    assert problem["message"] == str(refused.value)


def test_every_problem_is_reported_at_once() -> None:
    problems = VISION.check_config({"fps": 0, "hold_s": -1, "colour": "red"})
    assert sorted(p["path"] for p in problems) == ["/colour", "/fps", "/hold_s"]


def test_an_audio_processing_problem_inside_a_feature_is_pointed_there() -> None:
    problems = AUDIO_PROCESSING.check_config(
        {"noise_suppression": {"level": 5}, "echo_cancelation": {}}
    )
    assert {p["path"] for p in problems} == {
        "/noise_suppression/level",
        "/echo_cancelation",
    }


def test_a_camera_device_of_the_wrong_kind_is_its_problem() -> None:
    [problem] = CAMERA.check_config({"device": True})
    assert problem["path"] == "/device"
    with pytest.raises(ValueError, match=problem["message"]):
        CameraConfig.from_dict({"device": True})


def test_a_section_that_is_not_an_object_is_one_problem_at_its_root() -> None:
    for contract in (VISION, AUDIO_PROCESSING, CAMERA):
        [problem] = contract.check_config("on")  # pyright: ignore[reportArgumentType]
        assert problem["path"] == ""


# --- Which extras a config needs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({}, []),
        ({"enabled": False}, []),
        ({"enabled": True}, ["wica-interaction[vision]"]),
        ("nonsense", []),
    ],
)
def test_vision_needs_its_extra_only_when_on(
    document: Any, expected: list[str]
) -> None:
    assert VISION.required_extras(document) == expected


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({}, []),
        ({"echo_cancellation": {"enabled": False}}, []),
        (
            {"echo_cancellation": {"enabled": True}},
            ["wica-interaction[audio-processing]"],
        ),
        (
            {"noise_suppression": {"enabled": True}},
            ["wica-interaction[audio-processing]"],
        ),
        ({"echo_cancellation": "on"}, []),
    ],
)
def test_audio_processing_needs_its_extra_when_a_feature_is_on(
    document: Any, expected: list[str]
) -> None:
    assert AUDIO_PROCESSING.required_extras(document) == expected


# --- What the machine misses, and preparing it -------------------------------------------------


class _Models:
    """Stand-ins for the two models' caches: present or not, and what a download did."""

    def __init__(self) -> None:
        self.cached: set[str] = set()
        self.fetched: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for item_id, (title, what, _, _) in list(VISION.models.items()):
            monkeypatch.setitem(
                VISION.models,
                item_id,
                (
                    title,
                    what,
                    lambda item_id=item_id: item_id in self.cached,
                    lambda item_id=item_id: self._fetch(item_id),
                ),
            )

    def _fetch(self, item_id: str) -> None:
        self.fetched.append(item_id)
        self.cached.add(item_id)


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Models]:
    fake = _Models()
    fake.install(monkeypatch)
    monkeypatch.setattr(VISION, "try_imports", lambda: None)
    yield fake


def _missing_import() -> None:
    raise ImportError("No module named 'mediapipe'")


def test_vision_off_needs_nothing(models: _Models) -> None:
    assert VISION.check_setup({"enabled": False}) == []


def test_a_missing_vision_extra_is_the_only_item(
    models: _Models, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(VISION, "try_imports", _missing_import)
    [item] = VISION.check_setup({"enabled": True})
    assert item["id"] == "extra:wica-interaction[vision]"
    assert item["status"] == "missing"
    assert item["required"] is True and item["preparable"] is False
    assert "mediapipe" in item["detail"]


def test_with_the_extra_the_models_are_optional_items_to_prepare(
    models: _Models,
) -> None:
    items = {item["id"]: item for item in VISION.check_setup({"enabled": True})}
    assert items["extra:wica-interaction[vision]"]["status"] == "ready"
    for model in ("model:hand-landmarker", "model:gesture-classifier"):
        assert items[model]["status"] == "missing"
        # The session downloads a missing model itself, slower: it never blocks Start.
        assert items[model]["required"] is False
        assert items[model]["preparable"] is True


def test_one_model_is_prepared_alone_with_its_progress(models: _Models) -> None:
    events: list[dict[str, Any]] = []
    VISION.prepare(
        {"enabled": True}, "model:hand-landmarker", on_progress=events.append
    )

    assert models.fetched == ["model:hand-landmarker"]
    assert events[-1]["fraction"] == 1.0
    statuses = {i["id"]: i["status"] for i in VISION.check_setup({"enabled": True})}
    assert statuses["model:hand-landmarker"] == "ready"
    assert statuses["model:gesture-classifier"] == "missing"


def test_preparing_a_ready_model_fetches_nothing(models: _Models) -> None:
    models.cached.add("model:gesture-classifier")
    VISION.prepare({"enabled": True}, "model:gesture-classifier")
    assert models.fetched == []


def test_a_cache_that_cannot_be_read_is_unknown_not_a_raise(
    models: _Models, monkeypatch: pytest.MonkeyPatch
) -> None:
    title, what, _, fetch = VISION.models["model:gesture-classifier"]

    def unreadable() -> bool:
        raise OSError("permission denied")

    monkeypatch.setitem(
        VISION.models, "model:gesture-classifier", (title, what, unreadable, fetch)
    )
    items = {item["id"]: item for item in VISION.check_setup({"enabled": True})}
    assert items["model:gesture-classifier"]["status"] == "unknown"


@pytest.mark.parametrize(
    ("contract", "document"),
    [
        (VISION, {"enabled": True}),
        (AUDIO_PROCESSING, {"echo_cancellation": {"enabled": True}}),
        (CAMERA, {}),
    ],
)
def test_prepare_refuses_an_item_that_is_not_one_of_the_configs(
    models: _Models, contract: Any, document: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="not a preparable item"):
        contract.prepare(document, "model:nothing")


def test_audio_processing_reports_its_extra_only_when_a_feature_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AUDIO_PROCESSING, "try_imports", _missing_import)
    assert AUDIO_PROCESSING.check_setup({}) == []
    [item] = AUDIO_PROCESSING.check_setup({"noise_suppression": {"enabled": True}})
    assert item["id"] == "extra:wica-interaction[audio-processing]"
    assert item["status"] == "missing"


def test_the_camera_needs_nothing_on_the_machine() -> None:
    assert CAMERA.check_setup({"device": 1}) == []


# --- Test: a light real run that never raises --------------------------------------------------


def test_vision_probe_loads_with_the_configured_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, str | None]] = []

    def load(max_hands: int, device: str | None) -> str:
        seen.append((max_hands, device))
        return "loaded"

    monkeypatch.setattr(VISION, "load", load)
    result = VISION.probe({"enabled": True, "max_hands": 1, "device": "cpu"})
    assert result["ok"] is True and result["message"] == "loaded"
    assert seen == [(1, "cpu")]


def test_a_failing_probe_is_its_answer_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_camera(device: int | str) -> str:
        raise RuntimeError(f"Camera {device!r} could not be opened.")

    monkeypatch.setattr(CAMERA, "read_frame", no_camera)
    result = CAMERA.probe({"device": 3})
    assert result["ok"] is False
    assert result["message"] == "Camera 3 could not be opened."
    assert result["duration_s"] >= 0


def test_a_probe_over_an_invalid_section_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = AUDIO_PROCESSING.probe(
        {"noise_suppression": {"enabled": True, "level": 9}}
    )
    assert result["ok"] is False
    assert "'level'" in result["message"]


def test_a_probe_with_nothing_to_run_says_so() -> None:
    assert VISION.probe({"enabled": False})["ok"] is True
    assert AUDIO_PROCESSING.probe({})["ok"] is True


# --- Every requirement has a carrier (specs/setup.md, "Conformance and carriers") ---------------

# Requirements a section can name that no extra of this project carries, on purpose
# (specs/project.md, "Extras").
_NOT_MIRRORED = {
    "reachy-mini-bridge[placo]",
    "asr-engine[kyutai-mlx]",
    "asr-engine[kyutai-torch]",
}


def _module_choices(
    schema: dict[str, Any], path: tuple[str, ...] = ()
) -> Iterator[Any]:
    """A bare config selecting each branch of each module choice in *schema*."""
    if "oneOf" in schema:
        branches = schema["oneOf"]
        consts = [
            {k for k, v in b.get("properties", {}).items() if "const" in v}
            for b in branches
        ]
        common = set.intersection(*consts) if consts else set()
        for discriminator in common:
            for branch in branches:
                value = branch["properties"][discriminator]["const"]
                block: Any = {discriminator: value}
                for name in reversed(path):
                    block = {name: block}
                yield block
    for name, child in schema.get("properties", {}).items():
        if isinstance(child, dict) and child.get("type") in (
            "object",
            ["object", "null"],
        ):
            yield from _module_choices(child, (*path, name))


def _requirements(sections: Any, configs: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for section in sections:
        contract = section.contract
        probes = [c.get(section.name, {}) for c in configs]
        probes += list(_module_choices(contract.config_schema()["schema"]))
        for probe in probes:
            found.update(contract.required_extras(probe))
    return found


def test_every_requirement_a_section_can_name_has_an_extra_here() -> None:
    reachy_extras = [
        {"reachy": {"backend": "sim", "daemon": {"spawn": "auto"}}},
        {"reachy": {"backend": "sim", "daemon": {"kinematics_engine": "placo"}}},
        {"vision": {"enabled": True}},
    ]
    computer_extras = [
        {"audio_processing": {"noise_suppression": {"enabled": True}}},
        {"vision": {"enabled": True}},
    ]
    requirements = _requirements(
        COMPUTER_SECTIONS, [COMPUTER_DEFAULT, _e2e("computer"), *computer_extras]
    ) | _requirements(REACHY_SECTIONS, [REACHY_DEFAULT, _e2e("reachy"), *reachy_extras])

    uncarried = sorted(
        r
        for r in requirements
        if r not in _NOT_MIRRORED and not host_extras(r, host="wica-interaction")
    )
    assert uncarried == [], (
        f"no extra of wica-interaction installs {uncarried}: add a mirror to "
        "pyproject.toml, or the requirement to _NOT_MIRRORED with its reason"
    )
    # The probes reached what they are meant to: a voice, a recognizer, a provider, the sim.
    assert {
        "tts-engine[phonon]",
        "asr-engine[deepgram]",
        "wica[anthropic]",
    } <= requirements
    assert "reachy-mini-bridge[sim]" in requirements
    assert "wica-interaction[vision]" in requirements
