# Merge vocal-interaction-wica into this repo as `wica-interaction`, one app with two embodiments

**Status:** Todo

Implements [specs/embodiment.md](../specs/embodiment.md), [specs/computer-embodiment.md](../specs/computer-embodiment.md), [specs/reachy-embodiment.md](../specs/reachy-embodiment.md), [specs/audio-processing.md](../specs/audio-processing.md) (all `Stable`) and the `Updated` design of [app.md](../specs/app.md), [configuration.md](../specs/configuration.md), [asr-bridge.md](../specs/asr-bridge.md), [gesture-input.md](../specs/gesture-input.md), [project.md](../specs/project.md), [testing.md](../specs/testing.md). It delivers one repo, `wica-interaction`, whose one app runs in two **embodiments** — the computer it runs on, or a Reachy Mini robot — each launched by its own command. It leaves out anything the two apps do not do today (the webcam is the one addition the merge makes possible), a Nao embodiment, and publishing.

---

## 0. Read this first

### 0.1 What you are doing, in one paragraph

Two sibling repos are the same program with one difference: *where audio comes in and out, what the agent can act with, and what it sees through*. This repo (`reachy-mini-interaction-wica`, the Reachy Mini robot) is the newer one and **stays the base**. The other, `../vocal-interaction-wica` (a laptop voice assistant), is **ported** into it: its code is copied file by file into a new `computer/` subpackage — never merged by hand into an existing file — and the robot code moves into a `reachy/` subpackage. The shared lifecycle (`build_system`, `wire`, teardown, the UI) is written once against a small protocol, `Embodiment`, that both subpackages implement. The repo is renamed `wica-interaction`.

### 0.2 Where things are

| Path (absolute) | What |
|---|---|
| `/Users/thibault/code/funwithagents/reachy-mini-interaction-wica/` | **this repo** — the base. Package today: `src/reachy_mini_interaction_wica/`. |
| `/Users/thibault/code/funwithagents/vocal-interaction-wica/` | the repo being ported. Package: `src/vocal_interaction_wica/`. Read-only for you: copy from it, never edit it. No git history is carried over. |
| `../wica-framework`, `../asr-engine`, `../tts-engine`, `../reachy-mini-bridge`, `../vision-modules` | the sibling libraries, consumed as editable path dependencies. Do not edit them, except the one-line prerequisite in §1. |

### 0.3 Rules

1. **This repo wins every conflict.** Where the two apps' shared code differs, keep this repo's version. Concretely: the vocal repo's `app_ui.py` uses `PromptLog`/`prompt_panel`, which no longer exist in wica-framework — ignore it; the vocal `app.py`'s `_stop`/`_serve` are older than this repo's `wire`/`_shutdown` — ignore them; `--config` stays required (the vocal repo defaulted it).
2. **The vocal repo contributes only what it alone has:** `audio_processing.py`, the `TTSEngine`-backed `say`, its `tts`/`audio_processing` config parsing, its persona file, and the tests of those. Section 5 lists exactly which files and what to change in each.
3. **Never interleave the two embodiments in one file.** Robot code lives under `reachy/`, computer code under `computer/`. Shared modules must not import either subpackage.
4. **Do not design.** Every class, function, field and signature you need is given below or in the specs. If something is genuinely missing, stop and ask rather than invent.
5. **Keep the gate green after every numbered step:** `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`. Do not move to the next step with a red gate.
6. **Update statuses honestly** (AGENTS.md, "Keeping statuses current"): specs flip to `Implemented` only in §6, once everything passes.
7. Commit at the end of each numbered step with a message naming the step.

### 0.4 Glossary

- **Embodiment** — the body the agent has in a deployment. A Python object implementing the `Embodiment` protocol (§4.1): it can `enter`/`exit`, has a `voice` (the `say` Command), a `mic` (an ASR audio source), a `camera`, action `commands()`, and a `prompt_addendum`.
- **Lifecycle / `system.py`** — the shared code that builds everything around an embodiment: the event loop, WICA, the ASR bridge, the gesture input, the UI wiring, teardown.
- **Entry point** — one console script per embodiment (`wica-interaction-computer`, `wica-interaction-reachy`), each a `main()` of a few lines.
- **The gate** — the four verification commands in rule 5.

### 0.5 Target layout (what the tree looks like when you are done)

```
pyproject.toml                       name = "wica-interaction"; two scripts; extras (§2.2)
config.computer.example.json         template for the computer embodiment
config.reachy.example.json           template for the Reachy Mini embodiment
prompts/computer.md                  persona for the computer
prompts/reachy.md                    persona for the robot
src/wica_interaction/
  __init__.py
  config.py            shared sections: AppConfig, read_config_file, load_app_config, VisionConfig
  embodiment.py        the Embodiment protocol
  asr_bridge.py        AsrBridge (mic_error callable instead of a RobotMicSource)
  gesture_input.py     CameraSource protocol, GestureInput(source, ...), hand_signs_prompt_section(), SIGNS, sign_shown
  system.py            AppSystem, build_system, wire, _start, _shutdown, _serve, _degraded_system, run_app
  app_ui.py            display_entry, tick_asr, tick_vision, build_ui
  computer/
    __init__.py
    config.py          ComputerConfig, CameraConfig, AudioProcessingConfig & co, load_config
    audio_processing.py  (copied verbatim from the vocal repo) + build_coordinator
    embodiment.py      ComputerEmbodiment, WebcamSource
    app.py             main()
  reachy/
    __init__.py
    config.py          ReachyConfig, load_config
    embodiment.py      ReachyEmbodiment, RobotMicSource, RobotCameraSource
    robot_control.py   (moved, unchanged)
    app.py             main()
tests/
  conftest.py, fakes.py, test_config.py, test_asr_bridge.py, test_gesture_input.py,
  test_system.py, test_app_ui.py, test_project_map.py
  computer/  __init__.py, fakes.py, test_config.py, test_embodiment.py, test_audio_processing.py
  reachy/    __init__.py, fakes.py, test_config.py, test_embodiment.py, test_robot_control.py
tests-e2e/   conftest.py, support.py, test_gesture_input_live.py
```

Files that must **not** exist at the end: `src/wica_interaction/tts_output.py`, `src/wica_interaction/robot_control.py`, `prompts/assistant.md`, `config.example.json`, `specs/tts-output.md`, `specs/robot-control.md`, `tests/test_app.py`, `tests/test_tts_output.py`, `tests/test_robot_control.py`, and the old `src/reachy_mini_interaction_wica/` directory.

---

## 1. Prerequisite: one line in `../reachy-mini-bridge`

**Why.** This repo will consume `tts-engine` as a path dependency (`../tts-engine`). The bridge's `pyproject.toml` names `tts-engine` by a git URL (`tts-engine @ git+https://github.com/funwithagents/tts-engine@main`), and uv refuses two different URLs for one package ("conflicting URLs for package `tts-engine`"). Verified 2026-09-17: once the bridge names it by plain name, a path source here resolves the whole graph from `../tts-engine`, and the provider extras compose.

**Do.** In `../reachy-mini-bridge/pyproject.toml`:

1. In `[project] dependencies`, replace the line `"tts-engine @ git+https://github.com/funwithagents/tts-engine@main",` with `"tts-engine",` (keep the comment above it, edit it to say the package is a sibling path dependency during development).
2. In `[tool.uv.sources]`, add `tts-engine = { path = "../tts-engine", editable = true }`.
3. Run `uv lock` in the bridge and check its gate (`uv run pytest` there) still passes. Commit in the bridge.

**Fallback** if you may not change the bridge: in §2.2 write the two TTS extras as `tts-elevenlabs = ["tts-engine[elevenlabs] @ git+https://github.com/funwithagents/tts-engine@main"]` and `tts-pocket = ["tts-engine[pocket] @ git+https://github.com/funwithagents/tts-engine@main"]` (**exactly** that URL string, it must match the bridge's), set `computer = ["tts-engine @ git+https://github.com/funwithagents/tts-engine@main"]`, and do **not** add a `tts-engine` entry to `[tool.uv.sources]`. This is the only other shape that locks.

---

## 2. Step 1 — Rename the project and declare the dependency shape

Nothing about behavior changes in this step; the app still runs the robot only.

### 2.1 Rename the package

```
git mv src/reachy_mini_interaction_wica src/wica_interaction
```

Then replace the string `reachy_mini_interaction_wica` with `wica_interaction` **everywhere** it appears (source, tests, `pyproject.toml`, `AGENTS.md`, `README.md`, every `specs/*.md` frontmatter and body, `plans/*.md` may keep their historical text). Use a project-wide search to confirm zero occurrences remain, except inside `plans/` files older than this one.

### 2.2 `pyproject.toml`

Replace the `[project]`, `[project.optional-dependencies]`, `[project.scripts]`, `[dependency-groups]` and `[tool.uv.sources]` tables with the following. Keep `[build-system]`, `[tool.uv] dependency-metadata`, `[tool.ruff]`, `[tool.pytest.ini_options]`, `[tool.pyright]` as they are.

```toml
[project]
name = "wica-interaction"
version = "0.1.0"
description = "A WICA interaction application: a person speaks to a WICA agent, which speaks (and acts) back through the embodiment it is launched with — the computer it runs on, or a Reachy Mini robot."
readme = "README.md"
authors = [{ name = "Thibault Hervier", email = "thibault@fr2g.io" }]
requires-python = ">=3.12"
# What every embodiment needs. `vision-modules` core is numpy + opencv only (its heavy models are
# the `vision` extra below); `numpy` is imported directly by gesture_input.py.
dependencies = [
    "wica[gradio]",
    "asr-engine",
    "vision-modules",
    "gradio>=6",
    "numpy",
]

[project.optional-dependencies]
# One extra per embodiment, per optional feature, per provider (specs/project.md, "Extras").
computer = ["tts-engine"]
audio-processing = ["aec-audio-processing>=1.0.1", "samplerate>=0.2.4"]
reachy = ["reachy-mini-bridge"]
vision = ["vision-modules[hand]"]
anthropic = ["wica[anthropic]"]
deepgram = ["asr-engine[deepgram]"]
tts-elevenlabs = ["tts-engine[elevenlabs]"]
tts-pocket = ["tts-engine[pocket]"]
all = [
    "wica-interaction[computer,audio-processing,reachy,vision,anthropic,deepgram,tts-elevenlabs,tts-pocket]",
]

[project.scripts]
wica-interaction-computer = "wica_interaction.computer.app:main"
wica-interaction-reachy = "wica_interaction.reachy.app:main"

[dependency-groups]
dev = [
    "pyright>=1.1.411",
    "pytest>=9.1.1",
    "ruff>=0.16.6",
    # Every extra, so the default `uv sync` runs both entry points and the whole test tier.
    "wica-interaction[all]",
    # The sim robot backend (MuJoCo mockup) for manual/e2e testing; a real-robot deployment needs none of it.
    "reachy-mini-bridge[sim]",
]

[tool.uv.sources]
wica-interaction = { workspace = true }
wica = { path = "../wica-framework", editable = true }
asr-engine = { path = "../asr-engine", editable = true }
tts-engine = { path = "../tts-engine", editable = true }
reachy-mini-bridge = { path = "../reachy-mini-bridge", editable = true }
vision-modules = { path = "../vision-modules", editable = true }
```

The scripts point at modules that do not exist until §4 and §5. That is fine for `uv lock`/`uv sync` (scripts are resolved at run time), but **until §4.6 the old `main` is still `src/wica_interaction/app.py:main`** — keep a temporary third script `wica-interaction = "wica_interaction.app:main"` in this step and delete it in §4.6.

Run `uv lock` then `uv sync`. Both must succeed.

### 2.3 `tests/test_project_map.py`

Make the guard understand the new package and its subpackages. Edit:

```python
_PKG = _REPO_ROOT / "src" / "wica_interaction"

# Matches link targets like `src/wica_interaction/foo.py` or `src/wica_interaction/reachy/foo.py`.
_MODULE_LINK = re.compile(
    r"src/wica_interaction/((?:[a-z_]+/)*[A-Za-z_][A-Za-z0-9_]*\.py)"
)


def _actual_modules() -> set[str]:
    # Package-relative paths: "config.py", "reachy/embodiment.py", ...
    return {str(path.relative_to(_PKG)) for path in _PKG.rglob("*.py")}


def _concept_modules() -> set[str]:
    # Package glue (`__init__.py` at any depth) is exempt.
    return {m for m in _actual_modules() if Path(m).name != "__init__.py"}
```

and in `test_every_concept_module_is_governed_by_a_spec` replace `governed.add(Path(rel).name)` with `governed.add(rel.removeprefix("src/wica_interaction/"))` and the `startswith` string with `"src/wica_interaction/"`. Delete the now-unused `_NON_CONCEPT_MODULES` constant (and its mention in the assertion message).

### 2.4 `AGENTS.md`

Rewrite the "Project map" section's paths to `src/wica_interaction/`. Keep one row per module that exists **right now** (the guard checks the map against the tree at every step): after this step that is the same seven modules as today under the new path. Add rows as later steps create modules; remove rows as they delete them. The final map is in §6.

### 2.5 Spec frontmatters

In every `specs/*.md`, change `src/reachy_mini_interaction_wica/` to `src/wica_interaction/` in the `code:` lists. Leave the file names as they are for now (e.g. `app.py`, `tts_output.py`); §4–§5 update them when files move.

**Done when:** the gate is green and `uv run wica-interaction --config config.example.json` still opens the robot app.

---

## 3. Step 2 — Shared config

Split `src/wica_interaction/config.py` so the shared part is reusable by both embodiments. `VisionConfig` and `_is_number` stay exactly as they are.

### 3.1 New shared API in `config.py`

```python
_SHARED_SECTIONS = frozenset({"wica", "asr", "vision"})
_REQUIRED_SECTIONS = ("wica", "asr")


@dataclass
class AppConfig:
    """The shared sections — what the lifecycle (system.py) is built from."""

    wica: WicaConfig
    asr_engine: ASREngineConfig
    vision: VisionConfig | None = None


def read_config_file(path: str) -> dict[str, Any]:
    """Read + JSON-parse the file; ValueError if missing/unreadable/invalid JSON/not an object.
    (Move the existing open/json.loads/isinstance-dict code of load_config here, unchanged.)"""


def load_app_config(
    data: dict[str, Any], *, path: str, sections: frozenset[str]
) -> AppConfig:
    """Validate the shared part of an already-parsed config object.

    `sections` is every top-level key the calling embodiment accepts (its own plus the shared
    ones). Order of checks: (1) unknown keys → ValueError
    f"config file {path!r}: unknown section(s): {', '.join(repr(k) for k in sorted(unknown))}";
    (2) missing required sections → the existing "missing required section(s)" ValueError;
    (3) delegate: wica → WicaConfig.from_dict(data["wica"], base_dir=os.path.dirname(path));
    asr → ASREngineConfig.from_dict(_engine_block(data["asr"], name="asr", path=path));
    vision → VisionConfig.from_dict(data["vision"], path=path) if data.get("vision") is not None else None.
    """
```

Delete the old `load_config`, `_reachy_config` and the `ReachyMiniConfig` import from this module (they move to `reachy/config.py` in §4.3). Keep `_engine_block` (make it public as `engine_block` since both embodiment loaders call it — the computer one for `tts`).

### 3.2 Temporary bridge for the still-monolithic app

Until §4 exists, `app.py` needs the old `AppConfig(wica, asr_engine, reachy, vision)`. To keep the gate green in this step, create `src/wica_interaction/reachy/__init__.py` (empty) and `src/wica_interaction/reachy/config.py` now (its content is in §4.3), and change `app.py`'s `from wica_interaction.config import AppConfig, load_config` to import `ReachyConfig, load_config` from `wica_interaction.reachy.config`, then replace `config.wica` → `config.app.wica`, `config.asr_engine` → `config.app.asr_engine`, `config.vision` → `config.app.vision` inside `app.py` (`config.reachy` stays). Add the `reachy/config.py` row to the AGENTS.md map and `src/wica_interaction/reachy/config.py` to `specs/reachy-embodiment.md`'s frontmatter (already there) — the guard needs every module governed.

### 3.3 Tests

`tests/test_config.py` keeps the shared cases (file errors, missing section, engine block, `system_prompt_file` resolution, credentials unset, all `test_vision_*`) rewritten over `read_config_file` + `load_app_config(data, path=..., sections=_SHARED_SECTIONS | {"reachy"})` — and gains two: an unknown top-level key raises `ValueError` naming it; a key inside `sections` is accepted. Move every `test_reachy_*`, `test_daemon_*`, `test_invalid_reachy_backend_raises` and `test_reachy_section_is_optional_but_required_sections_stay` to `tests/reachy/test_config.py` (create `tests/reachy/__init__.py`, empty), over `wica_interaction.reachy.config.load_config`.

**Done when:** the gate is green and the robot app still opens.

---

## 4. Step 3 — The `Embodiment` protocol, the `reachy/` subpackage, the lifecycle over it

This is the refactor of the existing robot code. No computer code yet.

### 4.1 `src/wica_interaction/embodiment.py` (new, complete)

```python
"""The Embodiment protocol: what the shared lifecycle (system.py) needs from a body.

See specs/embodiment.md for who calls each member and when. A `typing.Protocol`, so each real
embodiment is checked structurally by pyright at the `build_system(config, embodiment)` call and
the lifecycle tests run over a `FakeEmbodiment` that imports no robot SDK or audio engine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from asr_engine.audio import AudioSource
from wica import Command

from wica_interaction.gesture_input import CameraSource

# The `say` output Command: non-raising, "OK" on success, "TTS error: …" otherwise.
Voice = Callable[[str], Awaitable[str]]


class Embodiment(Protocol):
    display_name: str
    missing: list[str]

    async def enter(self) -> None: ...
    async def exit(self) -> None: ...

    @property
    def voice(self) -> Voice | None: ...

    @property
    def mic(self) -> AudioSource | None: ...

    @property
    def mic_error(self) -> str | None: ...

    def camera(self, *, fps: float) -> CameraSource: ...

    def commands(self) -> Sequence[Command | Callable[..., Any]]: ...

    @property
    def prompt_addendum(self) -> str: ...
```

### 4.2 `gesture_input.py` — inject the camera, own the hand-signs text

1. Add, above `RobotCameraSource`:

   ```python
   class CameraSource(FrameSource, Protocol):
       """A FrameSource that paces itself to the configured fps and reports its own health."""

       @property
       def capture_error(self) -> str | None: ...
   ```

   (`from typing import Protocol`; `FrameSource` from `vision_modules`.)
2. Change `GestureInput.__init__` to take `source: CameraSource` as its first and only positional parameter — remove the `api` and `loop` parameters — and set `self._source = source` where it currently builds `RobotCameraSource(api, loop, fps=fps)`. Everything else in the class is unchanged (`self._provider = StreamProvider(self._source, mirror=False)`, `status()["capture_error"] = self._source.capture_error`).
3. **Cut** the whole `RobotCameraSource` class (and the `_POLL_TIMEOUT_S` constant, the `TYPE_CHECKING` import of `ReachyMiniApi`, and `asyncio` if now unused) — paste it into `reachy/embodiment.py` (§4.4). `gesture_input.py` must no longer mention `reachy_mini_bridge`.
4. Add:

   ```python
   def hand_signs_prompt_section() -> str:
       """The persona's "Hand signs" section, appended by the app at startup when vision is on
       (specs/gesture-input.md, "What the agent does with it")."""
       return "\n\n## Hand signs\n\n" + _HAND_SIGNS_TEXT
   ```

   where `_HAND_SIGNS_TEXT` is a module constant holding **verbatim** the two paragraphs at lines 30 and 32 of today's `prompts/assistant.md` (the paragraph starting "You can see the person's hands." and the one starting "One sign always means the same thing"), joined by a blank line, with one edit: in the second paragraph replace `` `say`, `play_emotion`, or anything else `` with `` `say` or anything else ``.

### 4.3 `src/wica_interaction/reachy/config.py` (new, complete)

```python
"""The Reachy Mini embodiment's config: the shared sections plus a verbatim bridge config.
See specs/reachy-embodiment.md, "Configuration"."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachy_mini_bridge.config import ReachyMiniConfig

from wica_interaction.config import (
    _SHARED_SECTIONS,
    AppConfig,
    load_app_config,
    read_config_file,
)

SECTIONS = _SHARED_SECTIONS | {"reachy"}


@dataclass
class ReachyConfig:
    app: AppConfig
    reachy: ReachyMiniConfig


def load_config(path: str) -> ReachyConfig:
    data = read_config_file(path)
    app = load_app_config(data, path=path, sections=SECTIONS)
    return ReachyConfig(app=app, reachy=_reachy_config(data.get("reachy"), path=path))


def _reachy_config(section: Any, *, path: str) -> ReachyMiniConfig:
    # Moved unchanged from the old config.py.
    if section is None:
        return ReachyMiniConfig()
    if not isinstance(section, dict):
        raise ValueError(f"config file {path!r}: section 'reachy' must be an object")  # noqa: TRY004
    return ReachyMiniConfig.from_dict(section)
```

(Rename `_SHARED_SECTIONS` to `SHARED_SECTIONS` in `config.py` if you prefer a public name; use one name consistently.)

### 4.4 `src/wica_interaction/reachy/embodiment.py` (new)

Assemble it from existing code; write only the glue:

- **Paste** `RobotMicSource` (with `_MIC_QUEUE_MAXSIZE`, `_STREAM_ENDED`, its `logger`) from `asr_bridge.py`, unchanged.
- **Paste** `RobotCameraSource` (with `_POLL_TIMEOUT_S`) from `gesture_input.py`, unchanged.
- **Import** `RobotControl, emotions_prompt_section` from `wica_interaction.reachy.robot_control` (§4.5).
- Write `ReachyEmbodiment`:

```python
class ReachyEmbodiment:
    """The agent embodied in a Reachy Mini (specs/reachy-embodiment.md)."""

    display_name = "Reachy Mini"

    def __init__(
        self, config: ReachyConfig, *, api: ReachyMiniApi | None = None
    ) -> None:
        self._config = config
        self._api: ReachyMiniApi | None = api
        self._injected = api is not None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mic: RobotMicSource | None = None
        self._emotions: tuple[str, ...] | None = None
        self.missing: list[str] = []

    async def enter(self) -> None:
        self._loop = asyncio.get_running_loop()
        api = self._api if self._api is not None else ReachyMiniApi(self._config.reachy)
        await (
            api.__aenter__()
        )  # any raise propagates: "no embodiment" (system.py degrades)
        self._api = api
        if not self._injected:
            if self._config.reachy.tts is None:
                self.missing.append(
                    "TTS: no reachy.tts block configured (say is unavailable)"
                )
            elif api.synthesizer_error is not None:
                self.missing.append(f"TTS: {api.synthesizer_error}")
        try:
            self._emotions = tuple(await api.list_emotions())
        except Exception as exc:  # noqa: BLE001 - a library load failure degrades, never crashes
            self.missing.append(f"Robot: emotions unavailable: {exc}")
        self._mic = RobotMicSource(api)

    async def exit(self) -> None:
        if self._api is not None:
            await self._api.__aexit__(None, None, None)

    @property
    def voice(self) -> Voice | None:
        return self.say

    async def say(self, text: str) -> str:
        # Body and docstring: move VoiceOutput.say from tts_output.py here, verbatim
        # (self._api instead of self._api of VoiceOutput; assert self._api is not None first).
        ...

    @property
    def mic(self) -> AudioSource | None:
        return self._mic

    @property
    def mic_error(self) -> str | None:
        return self._mic.capture_error if self._mic is not None else None

    def camera(self, *, fps: float) -> CameraSource:
        assert self._api is not None and self._loop is not None
        return RobotCameraSource(self._api, self._loop, fps=fps)

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        assert self._api is not None
        return RobotControl(self._api, emotions=self._emotions).commands()

    @property
    def prompt_addendum(self) -> str:
        return emotions_prompt_section(self._emotions) if self._emotions else ""
```

The `assert`s are legitimate: every member except `enter` is only called after a successful `enter` (specs/embodiment.md, "Member by member").

### 4.5 `reachy/robot_control.py` and `reachy/app.py`

- `git mv src/wica_interaction/robot_control.py src/wica_interaction/reachy/robot_control.py`. No content change.
- `git rm src/wica_interaction/tts_output.py` once its `say` body is in §4.4.
- `src/wica_interaction/reachy/app.py` (new, complete):

```python
"""The Reachy Mini entry point: `wica-interaction-reachy --config <file>`."""

from __future__ import annotations

from wica_interaction.config import AppConfig
from wica_interaction.embodiment import Embodiment
from wica_interaction.reachy.config import load_config
from wica_interaction.reachy.embodiment import ReachyEmbodiment
from wica_interaction.system import run_app


def _load(path: str) -> tuple[AppConfig, Embodiment]:
    config = load_config(path)
    return config.app, ReachyEmbodiment(config)


def main() -> None:
    run_app(description="Talk to a WICA-driven Reachy Mini.", load=_load)


if __name__ == "__main__":
    main()
```

### 4.6 `system.py` — from `app.py`

`git mv src/wica_interaction/app.py src/wica_interaction/system.py`, then make these changes and only these:

| Where in today's `app.py` | Change |
|---|---|
| module docstring | say it is the embodiment-agnostic lifecycle; drop robot wording |
| imports | remove `ReachyMiniApi`, `RobotMicSource`, `RobotControl`, `emotions_prompt_section`, `VoiceOutput`, `AppConfig, load_config`; add `from wica_interaction.config import AppConfig`, `from wica_interaction.embodiment import Embodiment`, `from wica_interaction.gesture_input import GestureInput, GestureModels, hand_signs_prompt_section, sign_shown` |
| `AppSystem` fields | `loop`, `embodiment: Embodiment | None`, `wica`, `bridge`, `gesture`, `missing`, `submit_speech`. Remove `api`, `voice`, `emotions`. |
| `build_system` signature | `build_system(config: AppConfig, embodiment: Embodiment, *, asr_engine: ASREngine | None = None, gesture_models: GestureModels | None = None) -> AppSystem` |
| the "robot" block (`robot = api if ...` through the `synthesizer_error` checks) | replace with: `body: Embodiment | None = embodiment` / `try: asyncio.run_coroutine_threadsafe(body.enter(), loop).result()` / `except Exception as exc: missing.append(f"{body.display_name}: {exc}"); body = None` / `else: missing.extend(body.missing)` |
| the "emotion names" block | delete |
| `voice = VoiceOutput(robot) ...` | delete |
| `Wica.init(_seeded_wica_config(config.wica, emotions), loop=loop)` | `Wica.init(_seeded_wica_config(config.wica, addenda), loop=loop)` where, just above, `addenda = [body.prompt_addendum if body is not None else ""]` plus, when `config.vision is not None and config.vision.enabled and body is not None`, `hand_signs_prompt_section()` |
| `_seeded_wica_config(config, emotions)` | becomes `_seeded_wica_config(config: WicaConfig, addenda: Sequence[str]) -> WicaConfig`: `persona = resolve_system_prompt(config.agent) + "".join(a for a in addenda if a)` |
| the ASR block | `if asr_engine is not None: bridge = AsrBridge(asr_engine, on_final_segment=_deliver_speech, mic_error=(lambda: body.mic_error) if body is not None else None)` / `elif body is not None: try: asr = ASREngine(config.asr_engine, audio_source=body.mic); bridge = AsrBridge(asr, on_final_segment=_deliver_speech, mic_error=lambda: body.mic_error) except (ValueError, ImportError) as exc: missing.append(f"ASR: {exc}")` |
| the vision block condition | `if vision is not None and vision.enabled and body is not None and wica is not None:` |
| `GestureInput(robot, loop, on_gesture=..., ...)` | `GestureInput(body.camera(fps=vision.fps), on_gesture=..., threshold=..., fps=..., max_hands=..., device=..., models=gesture_models)` |
| `AppSystem(...)` construction | pass `embodiment=body`; drop `api`, `voice`, `emotions` |
| `wire` | `if system.embodiment is not None and system.embodiment.voice is not None: wica.set_output_command(system.embodiment.voice)`; replace the `RobotControl(...)` loop with `if system.embodiment is not None: for command in system.embodiment.commands(): wica.register_command(command)` |
| `_degraded_system` | new field set |
| `_shutdown` | replace the `system.api.__aexit__` hop with `if system.embodiment is not None and loop is not None: _run_on_loop(loop, system.embodiment.exit(), step="embodiment.exit")` |
| `main()` | rename to `run_app(*, description: str, load: Callable[[str], tuple[AppConfig, Embodiment]]) -> None`; the argparse description comes from the parameter; `config = load_config(args.config)` becomes `app_config, embodiment = load(args.config)` and `build_system(app_config, embodiment)`; delete the `if __name__ == "__main__"` block |

Also in `asr_bridge.py`: change the `mic: RobotMicSource | None = None` parameter to `mic_error: Callable[[], str | None] | None = None`, store it, and in `status()` use `"capture_error": self._mic_error() if self._mic_error is not None else None`. Remove the `RobotMicSource` class and `TYPE_CHECKING` import (moved in §4.4). Add `MicError = Callable[[], str | None]` next to `OnFinalSegment`.

In `app_ui.py`: replace the hard-coded `gr.Blocks(title="Reachy Mini Interaction WICA")` / `gr.Markdown("# Talk to a WICA-driven Reachy Mini")` with a title built from `system.embodiment.display_name if system.embodiment is not None else "WICA"` (`title=f"WICA Interaction — {name}"`, heading `f"# Talk to a WICA agent embodied in a {name}"`); in the banner text replace the robot-specific sentence ("With no robot, the `say` voice … `\"fake\"` or `\"sim\"`.") with: "With no embodiment, the `say` voice and its action commands are not wired (the agent reasons but cannot speak or act); with no voice, `say` reports a TTS error." Replace `_Vision off (`vision.enabled` is false, or no robot)._` with `_Vision off (`vision.enabled` is false, or no camera)._`.

Delete the temporary `wica-interaction` script from `pyproject.toml`.

### 4.7 Prompts and example config

- `prompts/reachy.md` = today's `prompts/assistant.md` **minus** lines 28–33 (the `## Hand signs` heading and its two paragraphs and blank lines), **plus** this sentence appended to the end of the last paragraph (line 40): ` If a dictated passage arrives empty (they said `""`), they paused without saying anything — tell them briefly that nothing came through and offer to try again.`
- `git mv config.example.json config.reachy.example.json`; set `"system_prompt_file": "prompts/reachy.md"`.
- `git rm prompts/assistant.md`. Update `specs/gesture-input.md` and `specs/app.md` frontmatters: `prompts/assistant.md` → `prompts/reachy.md` (app.md also gains `prompts/computer.md`, created in §5).

### 4.8 Tests for this step

1. `tests/fakes.py` (new): move `FakeASREngine` and `_scripted_config` out of `tests/test_app.py` (rename `_scripted_config` → `scripted_wica_config`; make `FakeASREngine.__init__` also accept and record `audio_source=None`, so `build_system` can be asserted to have passed the embodiment's mic when the test lets it build the engine — note `build_system` builds a real `ASREngine` unless `asr_engine=` is injected, so keep injecting and assert `mic` on the embodiment side instead). Add:

   ```python
   class FakeEmbodiment:
       display_name = "fake body"

       def __init__(
           self,
           *,
           voice: bool = True,
           mic=None,
           mic_error=None,
           frames=None,
           commands=(),
           prompt_addendum="",
           missing=(),
           raise_on_enter=None,
           log=None,
       ): ...

       # Records: entered, exited, said (list[str]), the loop it was entered on.
       # enter(): raise RuntimeError(raise_on_enter) if set; else entered=True; missing = list(missing).
       # exit(): exited=True; log.append("embodiment.exit") if log is not None.
       # voice: an async `say(text) -> "OK"` appending to `said`, or None when voice=False.
       # mic / mic_error / prompt_addendum: what was passed. commands(): the passed tuple.
       # camera(fps): a ScriptedCameraSource over `frames` (see 2 below).
   ```

2. `tests/test_gesture_input.py`: replace `FakeCameraApi` + `RobotCameraSource` with a `ScriptedCameraSource` (put it in `tests/fakes.py`): `open()`/`close()`, `read()` returns the current `frame` (settable; `None` returned only after `close()`), sleeps `1/fps`, `fps()` returns the rate, `capture_error` settable. Every `GestureInput(cast(ReachyMiniApi, api), loop, ...)` call becomes `GestureInput(source, ...)`. Move the `# --- RobotCameraSource` tests (lines ~594–650) to `tests/reachy/test_embodiment.py`. Add one test that `hand_signs_prompt_section()` contains every value of `SIGNS` and the word `stop`.
3. `tests-e2e/test_gesture_input_live.py`: same replacement of `FakeCameraApi` by `ScriptedCameraSource`.
4. `tests/test_asr_bridge.py`: the `RobotMicSource` tests (`FakeMicApi` and everything under it) move to `tests/reachy/test_embodiment.py`; add a test that `status()["capture_error"]` returns what the injected `mic_error` callable returns, and `None` without one.
5. `tests/reachy/fakes.py`: move `FakeReachyMiniApi` from `tests/test_app.py` there (keep its `emotions`, `frame`, `say`, `play_emotion`, `list_emotions`, `__aenter__`/`__aexit__`, `raise_on_enter`, `raise_on_list_emotions`, `log`). `tests/reachy/test_embodiment.py`: the cases listed under "Testing" in specs/reachy-embodiment.md (enter/members, `say` — move the three tests of `tests/test_tts_output.py` here — `RobotMicSource`, `RobotCameraSource`). `git mv tests/test_robot_control.py tests/reachy/test_robot_control.py` (imports only). `git rm tests/test_tts_output.py`.
6. `git mv tests/test_app.py tests/test_system.py`, then re-target every test at `build_system(config, FakeEmbodiment(...), asr_engine=FakeASREngine(...))`: an assertion on `fake_api.said` becomes `embodiment.said`; on `fake_api.played` becomes an assertion that the registered fake command was called (give `FakeEmbodiment` a command `async def wave() -> str` recording calls and script the fake provider to call `wave`); "no robot" tests become `raise_on_enter="boom"` tests asserting the banner line starts with `fake body:`; the emotions-in-prompt test becomes `prompt_addendum="\n\n## Test section\n\nhello"` asserted in the first prompt; the vision tests pass `frames=` and assert the hand-signs section is in the prompt when vision is on and absent when off; the teardown-order test asserts `["asr.stop", "embodiment.exit"]` order via the shared `log`. Delete tests that only made sense for the robot (they now live in `tests/reachy/`).
7. `tests/test_app_ui.py`: `_system_with_mic` / `_system_with_gesture` build an `AppSystem` with `embodiment=FakeEmbodiment()`; add a test that the page title names `display_name`.

### 4.9 Bookkeeping for this step

- AGENTS.md map: rows for `embodiment.py`, `system.py` (replacing `app.py`), `reachy/config.py`, `reachy/embodiment.py`, `reachy/robot_control.py`, `reachy/app.py`; remove `tts_output.py`, `robot_control.py`.
- Frontmatters: `specs/app.md` code → `system.py`, `app_ui.py`, `prompts/reachy.md` (+ `prompts/computer.md` after §5), tests → `tests/test_system.py`, `tests/test_app_ui.py`; `specs/asr-bridge.md`, `specs/gesture-input.md` per their new file names; `git rm specs/tts-output.md specs/robot-control.md` and delete their rows in `specs/_index.md`.

**Done when:** the gate is green; `uv run wica-interaction-reachy --config config.reachy.example.json` opens the app, speaks, plays an emotion, and — with vision on — shows the Vision line running; the same with `reachy.backend` set to `"fake"` (and no `daemon` block).

---

## 5. Step 4 — Port the computer embodiment

Source of truth for every file in this step: `/Users/thibault/code/funwithagents/vocal-interaction-wica/`. Create `src/wica_interaction/computer/__init__.py` (empty) and `tests/computer/__init__.py` (empty).

### 5.1 File mapping

| Copy from (vocal repo) | To (this repo) | Change |
|---|---|---|
| `src/vocal_interaction_wica/audio_processing.py` | `src/wica_interaction/computer/audio_processing.py` | imports only; then **append** `build_coordinator` (§5.2) |
| `src/vocal_interaction_wica/config.py` — the classes `EchoCancellationConfig`, `NoiseSuppressionConfig`, `AudioProcessingConfig` and the functions `_typed`, `_audio_processing_config` | `src/wica_interaction/computer/config.py` | verbatim, plus `CameraConfig`, `ComputerConfig`, `load_config` (§5.3) |
| `src/vocal_interaction_wica/tts_output.py` — the body and docstring of `VoiceOutput.say` and the `TTSTools` wrapping | `src/wica_interaction/computer/embodiment.py` | into `ComputerEmbodiment.say` (§5.4) |
| `src/vocal_interaction_wica/app.py` — the function `_build_audio_processing_coordinator` | `computer/audio_processing.py` as `build_coordinator(config: ComputerConfig)` | reads `config.audio_processing`, `config.app.asr_engine.audio`, `config.tts_engine.player.device` |
| `prompts/assistant.md` | `prompts/computer.md` | verbatim |
| `config.example.json` | `config.computer.example.json` | set `"system_prompt_file": "prompts/computer.md"`; add `"vision": {"enabled": false, "threshold": 0.55, "fps": 5, "max_hands": 2, "device": null}` and `"camera": {"device": 0}` |
| `tests/test_audio_processing.py` | `tests/computer/test_audio_processing.py` | imports only (`from wica_interaction.computer.audio_processing import …`, config classes from `wica_interaction.computer.config`) |
| `tests/test_config.py` — the `tts` and `audio_processing` cases | `tests/computer/test_config.py` | over `wica_interaction.computer.config.load_config`; add `camera` cases and "a `reachy` section is rejected" |
| `tests/test_tts_output.py` — its three tests and `FakeTTSEngine` | `tests/computer/test_embodiment.py` + `tests/computer/fakes.py` | against `ComputerEmbodiment(config, tts_engine=FakeTTSEngine())` after `enter()` |
| `tests/test_app.py` — the audio-processing wiring/degrade tests (the ones asserting sink/source/`bind_far_rate` and the `ImportError` degrade) | `tests/computer/test_embodiment.py` | as assertions on `enter()` with a fake `coordinator_factory` |

Nothing else from the vocal repo is used. In particular do **not** copy its `app.py`, `app_ui.py`, `asr_bridge.py`, `test_app_ui.py`, `test_asr_bridge.py`, `AGENTS.md`, `specs/`, `plans/`.

### 5.2 `build_coordinator` (append to `computer/audio_processing.py`)

Take the vocal `_build_audio_processing_coordinator(config)` verbatim and rename it `build_coordinator(config: ComputerConfig) -> AudioProcessingCoordinator`; replace `config.asr_engine.audio` with `config.app.asr_engine.audio`. It keeps raising `RuntimeError` on a bad ASR format and `ImportError` on a missing dependency (the `samplerate` import when echo cancellation is on).

### 5.3 `computer/config.py` — the loader

```python
SECTIONS = SHARED_SECTIONS | {"tts", "audio_processing", "camera"}
_CAMERA_KEYS = frozenset({"device"})


@dataclass(frozen=True)
class CameraConfig:
    device: int | str = 0

    @classmethod
    def from_dict(cls, data: Any, *, path: str) -> CameraConfig:
        # not a dict → ValueError "section 'camera' must be an object" (noqa: TRY004);
        # unknown keys → ValueError naming them; device must be int (not bool) or str.
        ...


@dataclass
class ComputerConfig:
    app: AppConfig
    tts_engine: TTSEngineConfig
    audio_processing: AudioProcessingConfig
    camera: CameraConfig


def load_config(path: str) -> ComputerConfig:
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
        camera=CameraConfig.from_dict(data["camera"], path=path)
        if data.get("camera") is not None
        else CameraConfig(),
    )
```

### 5.4 `computer/embodiment.py` (new)

```python
class WebcamSource:
    """CameraSource over vision_modules.OpenCVSource (specs/computer-embodiment.md, "WebcamSource")."""

    def __init__(self, device: int | str, *, fps: float, opener: Callable[[int | str], FrameSource] = OpenCVSource) -> None:
        # fps <= 0 → ValueError. Keep self._interval = 1/fps, self._inner = opener(device) (not opened yet),
        # self._opened = False, self._closed = False, self._error: str | None = None, a threading.Lock.

    def open(self) -> None:
        # try self._inner.open(); self._opened = True; self._error = None
        # except Exception as exc: self._error = f"camera open failed: {exc}" (log once); never raise.

    def read(self) -> np.ndarray | None:
        # if closed → None. If not opened → open() again (retry); if still not opened → sleep interval, return
        # the last frame? NO: return… the provider ends on None. So: loop `while not closed`: if not opened,
        # open(); if opened, try frame = inner.read(); on exception set error, frame None; if frame is not
        # None: clear error, pace (sleep remainder of the interval), return frame; else sleep interval.
        # Only `close()` makes this return None.

    def close(self) -> None:
        # closed = True; inner.close() if opened; opened = False.

    def fps(self) -> float: return 1 / self._interval

    @property
    def capture_error(self) -> str | None: return self._error


class ComputerEmbodiment:
    display_name = "computer"

    def __init__(self, config: ComputerConfig, *, tts_engine: TTSEngine | None = None,
                 coordinator_factory: Callable[[ComputerConfig], AudioProcessingCoordinator] = build_coordinator) -> None:
        self._config, self._injected_engine, self._factory = config, tts_engine, coordinator_factory
        self._coordinator: AudioProcessingCoordinator | None = None
        self._tools: TTSTools | None = None
        self.missing: list[str] = []

    async def enter(self) -> None:
        ap = self._config.audio_processing
        if ap.any_enabled:
            try:
                self._coordinator = self._factory(self._config)
            except (ImportError, RuntimeError, ValueError) as exc:
                self.missing.append(f"Audio processing: {exc}")
        sink = self._coordinator.sink if self._coordinator is not None and ap.echo_cancellation.enabled else None
        try:
            engine = self._injected_engine if self._injected_engine is not None else TTSEngine(self._config.tts_engine, sink=sink)
            if sink is not None:
                assert self._coordinator is not None
                self._coordinator.bind_far_rate(engine.sample_rate)
            self._tools = TTSTools(engine)
        except (TTSConfigError, ImportError) as exc:
            self.missing.append(f"TTS: {exc}")

    async def exit(self) -> None: ...  # nothing to release (specs/audio-processing.md, "Teardown")

    @property
    def voice(self) -> Voice | None: return self.say if self._tools is not None else None
    async def say(self, text: str) -> str: ...  # docstring + body from the vocal VoiceOutput.say (self._tools.say)
    @property
    def mic(self) -> AudioSource | None: return self._coordinator.source if self._coordinator is not None else None
    @property
    def mic_error(self) -> str | None: return None
    def camera(self, *, fps: float) -> CameraSource: return WebcamSource(self._config.camera.device, fps=fps)
    def commands(self) -> Sequence[Command | Callable[..., Any]]: return ()
    @property
    def prompt_addendum(self) -> str: return ""
```

Write real code from this skeleton (the comments describe the behavior; `WebcamSource.read` must never return `None` before `close()`).

### 5.5 `computer/app.py` (new, complete)

Identical to `reachy/app.py` (§4.5) with `computer` in place of `reachy`, `ComputerEmbodiment` in place of `ReachyEmbodiment`, and `description="Talk to a WICA voice assistant."`.

### 5.6 Tests

- `tests/computer/fakes.py`: `FakeTTSEngine` (from the vocal `test_app.py`/`test_tts_output.py`: records `said`, `sample_rate = 44100`), `FakeCoordinator` (has `sink`, `source`, records `bind_far_rate(rate)` calls), `FakeOpenCVSource` (settable `frame`, `raise_on_open`, records open/close).
- `tests/computer/test_config.py`, `tests/computer/test_embodiment.py`, `tests/computer/test_audio_processing.py` per §5.1 and the "Testing" sections of specs/computer-embodiment.md and specs/audio-processing.md.

### 5.7 Bookkeeping for this step

AGENTS.md map rows for the four `computer/` modules; frontmatters already name them (specs/computer-embodiment.md, specs/audio-processing.md); `specs/app.md` frontmatter adds `prompts/computer.md`.

**Done when:** the gate is green; `uv run wica-interaction-computer --config config.computer.example.json` (with `WICA_ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY` set) replies through the speakers; with `vision.enabled: true` and a webcam the Vision line shows hands.

---

## 6. Step 5 — Docs and statuses

1. `README.md`: rewrite for `wica-interaction` — what it is, the two commands, the extras table from specs/project.md, the sibling checkouts (all five), the bridge prerequisite (§1), the two example configs, the develop commands.
2. `AGENTS.md`: the final project map —

   | Module | Spec |
   |---|---|
   | `config.py` | configuration.md |
   | `embodiment.py` | embodiment.md |
   | `asr_bridge.py` | asr-bridge.md |
   | `gesture_input.py` | gesture-input.md |
   | `system.py`, `app_ui.py` | app.md |
   | `computer/config.py`, `computer/embodiment.py`, `computer/app.py` | computer-embodiment.md |
   | `computer/audio_processing.py` | audio-processing.md |
   | `reachy/config.py`, `reachy/embodiment.py`, `reachy/robot_control.py`, `reachy/app.py` | reachy-embodiment.md |

   and the layout table's `tests/` row mentioning the `computer/` and `reachy/` subfolders.
3. `specs/_index.md`: delete the "In transition" note; every row's status per step 4 below.
4. Statuses, in both the spec file and the index: `embodiment.md`, `computer-embodiment.md`, `reachy-embodiment.md`, `audio-processing.md` → `Implemented`; `app.md`, `configuration.md`, `asr-bridge.md`, `gesture-input.md`, `project.md`, `testing.md` → `Implemented` and delete their "> **Updated (2026-09-17)** …" notes. `tests/test_project_map.py` now checks every frontmatter path exists — fix any that does not.
5. This plan → `Done` here and in `plans/_index.md`.

---

## 7. Verification (the whole thing)

- `uv sync` then `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all green.
- `uv run pytest tests/test_project_map.py -v` — every module mapped and governed, every `Implemented` frontmatter path exists.
- `grep -rn "reachy_mini_interaction_wica\|vocal_interaction_wica" src tests pyproject.toml AGENTS.md README.md specs` prints nothing.
- `grep -rn "reachy_mini_bridge\|vision_modules.hand\|tts_engine" src/wica_interaction/*.py` prints nothing except the `vision_modules` core imports in `gesture_input.py` (`FrameSource`, `StreamProvider`, `HandStage`, …): no shared module imports an embodiment's SDK.
- A computer-only environment proves the split: in a scratch copy, `uv sync --no-dev --extra computer --extra anthropic --extra deepgram --extra tts-elevenlabs` then `uv run wica-interaction-computer --config config.computer.example.json` opens (with keys unset the banner names them) and the Vision line reads `_Vision off_`.
- Manual, robot: `uv run wica-interaction-reachy --config config.reachy.example.json` (headful `sim`) speaks, plays an emotion after enabling the motors, reads a hand sign when `vision.enabled`.
- Manual, computer: with echo cancellation on, the agent does not reply to its own voice; with a webcam and `vision.enabled`, a `stop` sign makes it stop and confirm.
- `uv run pytest tests-e2e` skips cleanly without the models and passes with them.
