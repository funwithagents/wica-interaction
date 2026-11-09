import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_PKG = _REPO_ROOT / "src" / "wica_interaction"
_SPECS_DIR = _REPO_ROOT / "specs"

# Matches link targets like `src/wica_interaction/foo.py` or `src/wica_interaction/reachy/foo.py`
# anywhere in AGENTS.md.
_MODULE_LINK = re.compile(
    r"src/wica_interaction/((?:[a-z_]+/)*[A-Za-z_][A-Za-z0-9_]*\.py)"
)


def _mapped_modules() -> set[str]:
    text = _AGENTS_MD.read_text(encoding="utf-8")
    return set(_MODULE_LINK.findall(text))


def _actual_modules() -> set[str]:
    """Package-relative paths of every module: "config.py", "reachy/embodiment.py", ..."""
    return {path.relative_to(_PKG).as_posix() for path in _PKG.rglob("*.py")}


def _concept_modules() -> set[str]:
    """The modules a spec must govern — package glue (`__init__.py` at any depth) is exempt."""
    return {m for m in _actual_modules() if Path(m).name != "__init__.py"}


def test_agents_md_maps_exactly_the_package_modules():
    mapped = _mapped_modules()
    actual = _concept_modules()  # package glue (__init__.py) is exempt from the map

    missing_from_map = actual - mapped
    stale_in_map = mapped - actual

    assert not missing_from_map, (
        f"src package modules not listed in the AGENTS.md project map: {sorted(missing_from_map)}. "
        "Add a row for each new module."
    )
    assert not stale_in_map, (
        f"AGENTS.md project map references modules that no longer exist: {sorted(stale_in_map)}. "
        "Remove or rename their rows."
    )


# --- Spec frontmatter drift guards ------------------------------------------
#
# Every spec declares the code/tests it governs in a YAML frontmatter block:
#
#     ---
#     code:
#       - src/wica_interaction/example.py
#     tests:
#       - tests/test_example.py
#     ---
#
# The spec-drift checks read this to scope what they diff, so it must stay
# honest: listed paths must exist once the spec claims to be implemented, and
# every concept module must be governed by at least one spec. The mapping is
# many-to-many.
#
# Parsed with a tiny hand-rolled reader (not PyYAML) — the frontmatter format is
# authored in this repo and simple.

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_LIST_KEY = re.compile(r"^(code|tests):\s*$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.+?)\s*$")
# The `**Status:** <value>` line every spec carries under its title (AGENTS.md, "Keeping statuses
# current"); the value is one of the lifecycle states.
_STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def _spec_files() -> list[Path]:
    # Concept specs only — skip index/template/scratch files (`_index.md`, `_spec-template.md`, ...).
    return sorted(p for p in _SPECS_DIR.glob("*.md") if not p.name.startswith("_"))


def _parse_frontmatter(path: Path) -> dict[str, list[str]]:
    """Return {'code': [...], 'tests': [...]} from a spec's frontmatter block."""
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        return {}
    result: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key_match = _LIST_KEY.match(line)
        if key_match:
            current = result.setdefault(key_match.group(1), [])
            continue
        item_match = _LIST_ITEM.match(line)
        if item_match and current is not None:
            current.append(item_match.group(1))
    return result


def _status(path: Path) -> str | None:
    """The spec's `**Status:**` value, or None when the line is missing."""
    match = _STATUS.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match is not None else None


def test_every_spec_declares_the_code_it_governs():
    missing: list[str] = []
    for spec in _spec_files():
        front = _parse_frontmatter(spec)
        if not front.get("code"):
            missing.append(spec.name)
    assert not missing, (
        f"specs missing a non-empty `code:` frontmatter list: {missing}. "
        "Add a frontmatter block naming the files this spec governs so the "
        "spec-drift checks know what to check."
    )


def test_every_spec_carries_a_status_line():
    unlabelled = [spec.name for spec in _spec_files() if _status(spec) is None]
    assert not unlabelled, (
        f"specs without a `**Status:**` line: {unlabelled}. Every concept spec states its "
        "lifecycle status just under its title (see AGENTS.md, 'Keeping statuses current')."
    )


def test_implemented_spec_frontmatter_paths_all_exist():
    """`Implemented` is the one status that asserts design and code are in sync, so an
    `Implemented` spec must name only files that exist. A spec that is not yet implemented
    (`Stable`, `Updated`, `Draft`, `Not started`) legitimately names the files it *will* govern —
    the design gate precedes the code — so its paths are exempt until it flips to `Implemented`."""
    stale: list[str] = []
    for spec in _spec_files():
        if _status(spec) != "Implemented":
            continue
        front = _parse_frontmatter(spec)
        for rel in front.get("code", []) + front.get("tests", []):
            if not (_REPO_ROOT / rel).exists():
                stale.append(f"{spec.name} -> {rel}")
    assert not stale, (
        f"Implemented specs' frontmatter points at paths that do not exist: {sorted(stale)}. "
        "Update the `code:`/`tests:` lists when files are renamed or removed, or set the "
        "spec's status back to `Updated` while the code catches up."
    )


def test_every_concept_module_is_governed_by_a_spec():
    governed: set[str] = set()
    for spec in _spec_files():
        for rel in _parse_frontmatter(spec).get("code", []):
            if rel.startswith("src/wica_interaction/"):
                governed.add(rel.removeprefix("src/wica_interaction/"))

    ungoverned = _concept_modules() - governed
    assert not ungoverned, (
        f"src package modules not named in any spec's `code:` frontmatter: {sorted(ungoverned)}. "
        "Add each to the frontmatter of the spec that governs it "
        "(package glue — `__init__.py` — is exempt from this rule)."
    )
