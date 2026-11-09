"""Documentation drift guards: relative links and heading anchors resolve, and the
tables that mirror code match it (specs/project.md, "Documentation checks")."""

from __future__ import annotations

import dataclasses
import json
import re
import tomllib
from pathlib import Path

import pytest

from wica_interaction.computer.config import (
    CameraConfig,
    EchoCancellationConfig,
    NoiseSuppressionConfig,
)
from wica_interaction.config import VisionConfig

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"

# The Markdown a reader can reach from the repository: the root files, the tracked
# documentation trees and the two plan index files. Left out: the plan bodies (records
# of what was built, not rewritten when a file they link to later moves) and
# `analysis/` (dated working notes).
_ROOT_FILES = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
_DOC_DIRS = ["docs", "specs"]
_PLAN_FILES = ["plans/_index.md", "plans/_plan-template.md"]


def _markdown_files() -> list[Path]:
    files = [_REPO_ROOT / name for name in _ROOT_FILES + _PLAN_FILES]
    for directory in _DOC_DIRS:
        files.extend(sorted((_REPO_ROOT / directory).rglob("*.md")))
    return files


# --- Relative links -----------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _prose(text: str) -> str:
    """The text with code blocks and code spans removed: a link inside code is an
    example, not a link."""
    return _CODE_SPAN.sub("", _FENCE.sub("", text))


def _slug(heading: str) -> str:
    """GitHub's heading anchor: markup dropped, lower-cased, punctuation removed,
    spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for heading in _HEADING.findall(_FENCE.sub("", path.read_text(encoding="utf-8"))):
        slug = _slug(heading)
        candidate, n = slug, 0
        while candidate in anchors:  # GitHub numbers repeated headings
            n += 1
            candidate = f"{slug}-{n}"
        anchors.add(candidate)
    return anchors


def _broken_links(path: Path) -> list[str]:
    broken: list[str] = []
    for target in _LINK.findall(_prose(path.read_text(encoding="utf-8"))):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            broken.append(f"{target} (missing file)")
        elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
            broken.append(f"{target} (no such heading)")
    return broken


@pytest.mark.parametrize(
    "path", _markdown_files(), ids=lambda p: p.relative_to(_REPO_ROOT).as_posix()
)
def test_relative_links_resolve(path: Path) -> None:
    assert _broken_links(path) == []


# --- Tables that mirror code --------------------------------------------------

_SUBHEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")


def _section(path: Path, heading: str) -> str:
    """The Markdown of `path` under `heading`, up to the next heading of the same or
    a higher level."""
    out: list[str] = []
    level = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SUBHEADING.match(line)
        if match and match.group(2) == heading and not out:
            level = len(match.group(1))
            out.append(line)
        elif out:
            if match and len(match.group(1)) <= level:
                break
            out.append(line)
    assert out, f"no heading {heading!r} in {path.name}"
    return "\n".join(out)


def _first_column_names(section: str) -> set[str]:
    """The code-span names in the first column of the section's table rows; a row
    whose first cell is not a code span (the italic *(base)* row) is skipped."""
    names: set[str] = set()
    for line in section.splitlines():
        match = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if match:
            names.add(match.group(1))
    return names


def test_the_install_page_lists_every_extra() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    extras = set(pyproject["project"]["optional-dependencies"])
    documented = _first_column_names(
        _section(_DOCS / "reference" / "install.md", "Extras")
    )
    assert documented == extras


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        (
            "`vision`: hand signs",
            {f.name for f in dataclasses.fields(VisionConfig)},
        ),
        (
            "`camera`: the computer's webcam",
            {f.name for f in dataclasses.fields(CameraConfig)},
        ),
        (
            "`audio_processing`: the computer's mic processing",
            {
                f"echo_cancellation.{f.name}"
                for f in dataclasses.fields(EchoCancellationConfig)
            }
            | {
                f"noise_suppression.{f.name}"
                for f in dataclasses.fields(NoiseSuppressionConfig)
            },
        ),
    ],
    ids=["vision", "camera", "audio_processing"],
)
def test_the_configuration_page_lists_the_app_owned_fields(
    heading: str, expected: set[str]
) -> None:
    section = _section(_DOCS / "reference" / "configuration.md", heading)
    assert _first_column_names(section) == expected


@pytest.mark.parametrize(
    "template", ["config.computer.example.json", "config.reachy.example.json"]
)
def test_the_configuration_page_has_a_heading_per_template_section(
    template: str,
) -> None:
    sections = json.loads((_REPO_ROOT / template).read_text("utf-8")).keys()
    text = (_DOCS / "reference" / "configuration.md").read_text("utf-8")
    headings = _HEADING.findall(_FENCE.sub("", text))
    for section in sections:
        assert any(f"`{section}`" in heading for heading in headings), section
