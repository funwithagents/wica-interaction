# Agent instructions

Start at [specs/_index.md](specs/_index.md) for an overview of the specs and their status before making design decisions or writing code — it lists each spec and whether it's still open ("Draft"/"Not started"), design-validated ("Stable"), or built ("Implemented"). For what's been (or is being) built, see [plans/_index.md](plans/_index.md), which lists each implementation plan and its status ("Todo"/"In progress"/"Done").

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/wica_interaction/` | The library itself — one module per shared concept, one subpackage per embodiment (see below) |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/wica_interaction/` module structure, embodiment subpackages included (`tests/computer/`, `tests/reachy/`) |
| `tests-e2e/` | Opt-in live tests that call real external services (not collected by default `pytest`) |

### `src/wica_interaction/` modules

<!-- One row per concept module. Keep this in sync with the code (a test enforces it). -->

| Module | Role | Spec |
|---|---|---|
| [config.py](src/wica_interaction/config.py) | The shared config sections: one JSON's `wica`/`asr`/`vision` — each delegated to its owner's loader — plus the file and unknown-section rules every embodiment's loader reuses | [configuration.md](specs/configuration.md) |
| [embodiment.py](src/wica_interaction/embodiment.py) | The `Embodiment` protocol the lifecycle is written against: enter/exit, voice, mic, camera, commands, prompt addendum | [embodiment.md](specs/embodiment.md) |
| [asr_bridge.py](src/wica_interaction/asr_bridge.py) | Speech-in: owns the `ASREngine` (fed the embodiment's mic), maps segments to World `speech_input` (final only) + live partial view, hosts the dictation command | [asr-bridge.md](specs/asr-bridge.md) |
| [gesture_input.py](src/wica_interaction/gesture_input.py) | Vision-in: runs the embodiment's camera through `vision-modules`' hand + gesture pipeline, reduces each hand to one sign of the app's vocabulary (`stop`, `like`, `ok`, counts …), delivers changes to the World's per-hand `hand_gesture_<n>` entries (triggering the agent whenever a sign is shown); owns the "Hand signs" persona section | [gesture-input.md](specs/gesture-input.md) |
| [system.py](src/wica_interaction/system.py) | Wiring + lifecycle, embodiment-agnostic: builds the agent, ASR and vision around an `Embodiment` on one event loop, wires the voice and its commands, `run_app` | [app.md](specs/app.md) |
| [app_ui.py](src/wica_interaction/app_ui.py) | The Gradio presentation layer: the `display_entry` hook, the page layout (`build_ui` — WICA's `wica.contrib.gradio` observability panels next to the app's own ASR + Vision panel), the live views its timer polls | [app.md](specs/app.md) |
| [computer/config.py](src/wica_interaction/computer/config.py) | The computer embodiment's config: the shared sections plus `tts` (its voice), `audio_processing` and `camera` | [computer-embodiment.md](specs/computer-embodiment.md) |
| [computer/embodiment.py](src/wica_interaction/computer/embodiment.py) | `ComputerEmbodiment`: a `TTSEngine` voice through the speakers, the local (optionally processed) mic, a `WebcamSource`, no actions | [computer-embodiment.md](specs/computer-embodiment.md) |
| [computer/audio_processing.py](src/wica_interaction/computer/audio_processing.py) | The computer's mic processing: one WebRTC coordinator doing echo cancellation (TTS far-end reference) and noise suppression, bridging the TTS sink to the ASR source | [audio-processing.md](specs/audio-processing.md) |
| [computer/app.py](src/wica_interaction/computer/app.py) | The `wica-interaction-computer` entry point: load the config, construct the embodiment, `run_app` | [computer-embodiment.md](specs/computer-embodiment.md) |
| [reachy/config.py](src/wica_interaction/reachy/config.py) | The Reachy Mini embodiment's config: the shared sections plus `reachy` — a verbatim `reachy-mini-bridge` config, voice included | [reachy-embodiment.md](specs/reachy-embodiment.md) |
| [reachy/embodiment.py](src/wica_interaction/reachy/embodiment.py) | `ReachyEmbodiment`: one `ReachyMiniApi` behind `say`, the mic source and the camera source, plus the emotion names seeded into the prompt | [reachy-embodiment.md](specs/reachy-embodiment.md) |
| [reachy/robot_control.py](src/wica_interaction/reachy/robot_control.py) | The robot's action commands: non-raising WICA commands wrapping `reachy-mini-bridge` verbs — play an emotion (its description naming the names), get/set motor state (head tracking is bridge-managed, not agent-facing) | [reachy-embodiment.md](specs/reachy-embodiment.md) |
| [reachy/app.py](src/wica_interaction/reachy/app.py) | The `wica-interaction-reachy` entry point: load the config, construct the embodiment, `run_app` | [reachy-embodiment.md](specs/reachy-embodiment.md) |

**Keep this map current:** when you add, rename, or remove a top-level `src/wica_interaction/` module or a root directory, update the map in the same change — same discipline as keeping spec/plan statuses honest (below). A test (`tests/test_project_map.py`) enforces that every `src/wica_interaction/*.py` module appears here and vice-versa — and that the spec frontmatter (see below) stays honest too.

## Keeping statuses current

Specs and plans both carry a status, and you are responsible for keeping it honest as work progresses — update it in the same change that does the work, not as an afterthought:

- **Spec status** (`**Status:**` line near the top of each spec, and the Status column in [specs/_index.md](specs/_index.md)) tracks *design maturity* and *whether the code reflects the spec*, as a lifecycle: `Not started` → `Draft` (open questions remain) → `Stable` (design settled, reviewed and validated — open questions are deferrals only — but **not necessarily implemented yet**) → `Implemented` (a `Done` plan has built it and the code matches the spec). Keep the `**Status:**` line and the index row in sync.
  - **`Stable` is the design-review gate, not an implementation claim.** Promote `Draft` → `Stable` once the core design is settled and its remaining open questions are genuine deferrals (not load-bearing unknowns) — this is where the design is validated *before* code is written. No implementation is required to be `Stable`.
  - **`Implemented` means code matches.** Promote `Stable` → `Implemented` only once a plan implementing it is `Done` (lint, type check, tests all pass — see Verification). This is the one transition that asserts design and code are in sync.
  - **When you edit an `Implemented` spec in a way that requires new code, set its status to `Updated` in the same change.** `Updated` means the design is settled but the existing implementation now lags it — a stronger warning than `Stable`, because there is stale code to fix, not just code to write. Then write a new implementation plan for the gap (see below) and, once that plan is `Done`, flip the spec back to `Implemented`. This `Implemented → Updated → Implemented` loop keeps a spec's status an honest signal of whether the code actually matches it — never leave a re-designed spec sitting at `Implemented`.
  - A purely editorial edit to a `Stable` or `Implemented` spec (typos, clarifications, reordering — nothing that changes what the code should do) keeps its status; it does **not** need `Updated`.
- **Plan status** (`**Status:**` line near the top of each plan, and the Status column in [plans/_index.md](plans/_index.md)) tracks *implementation progress*: `Todo` → `In progress` → `Done`. Mark a plan `Done` only once it's implemented and verified (lint, type check, tests all pass — see Verification). Keep the `**Status:**` line and the index row in sync.
- Whenever you add a spec or plan, add its row to the relevant `_index.md`; whenever you change a status, change it in both the file and the index.

## Spec frontmatter

Every spec opens with a YAML frontmatter block naming the code and tests it governs:

```
---
code:
  - src/wica_interaction/<module>.py
tests:
  - tests/test_<module>.py
---
```

This is the **spec → code/tests** mapping — the inverse of the module → spec column in the Project map above. Its job is to give the **spec-drift checks** an explicit, version-controlled scope: the exact files to diff a spec against, so a checker never has to guess which code implements a given spec. `code:` names the implementation the spec specifies; `tests:` names the tests that pin its behavior (may be empty/absent).

The mapping is **many-to-many**: a file can be governed by several specs, so the same path legitimately appears in more than one spec's frontmatter.

**Keep it current** (same discipline as statuses): when you move, rename, or delete a file a spec governs — or add a new `src/wica_interaction/` module — update the affected spec's `code:`/`tests:` in the same change. `tests/test_project_map.py` enforces three invariants: every listed path exists, every spec declares a non-empty `code:` list, and every concept module in `src/wica_interaction/` is named by at least one spec (`__init__.py` is exempt as package glue).

## Testing

- Write functional tests: exercise what a feature/function actually does (inputs → outputs, state changes, side effects), not just that it runs or matches its signature.
- Avoid trivial/tautological tests — e.g. asserting a constant, asserting an object is not `None`, asserting a mock was called. If a test would pass for a broken implementation, it's not worth writing.
- Prefer driving the public API the way a real caller would over asserting on internals.

### Live/e2e tests

Some tests call real external services over the network. They live in `tests-e2e/`, a directory separate from `tests/`, so the default `uv run pytest` never runs them — no network access or credentials are needed for the normal dev loop. Run them explicitly, and only when you actually want to verify against a live service. Tests that lack their required credentials should **skip**, not fail, so the tier is safe to run with only the keys you happen to have.

## Implementation plans

- Write implementation plans as files in the [plans](plans/) folder.
- Name each file `YYYYMMDDHHmm_plan-title.md`: a compact date-time prefix, then an underscore, then a kebab-case title (words separated by `-`).
  - Example: `202607201830_world-registry-refactor.md`
- Give each plan a `**Status:**` line just under its title (`Todo`/`In progress`/`Done`) and add a row for it to [plans/_index.md](plans/_index.md). Keep both current as work progresses (see "Keeping statuses current" above).
- Start from [plans/_plan-template.md](plans/_plan-template.md).

## Verification

After any code change, run linting, formatting, type checking, and tests (see Commands), and fix any failures before considering the work done. `ruff format .` rewrites files in place — its changes belong in the same change as the work.

## Commands

```
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest
```
