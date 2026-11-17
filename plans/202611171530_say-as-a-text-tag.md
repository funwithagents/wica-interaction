# The voice as a tag in the model's text

**Status:** Done

Implements [specs/app.md](../specs/app.md) ("Speech out", "World, Commands, persona", "Wiring and lifecycle"), [specs/embodiment.md](../specs/embodiment.md) (`voice`), [specs/reachy-embodiment.md](../specs/reachy-embodiment.md) ("Persona") and [specs/gesture-input.md](../specs/gesture-input.md) (the `stop` rule) after WICA's breaking change `c50d017` ("The model speaks through a tag in its text"): the model speaks by writing `<say>…</say>`, not by calling a tool; the output sink, the forced tool call and the `noop` tool are gone. Leaves out streaming the voice (`OutputStream`), tag attributes (an emotion on `<say>`) and `OutputStream.heard` after a cut: the voice keeps its `str` signature, called once per tag with the whole text.

## Scope

- `uv.lock` — `wica` from `81dac6e` to `9805817` (the response guards, the streamed model call, the text output, and the output entry that does not read as a tag).
- `src/wica_interaction/system.py` — `wire(system)`: no `set_output_sink`, no transcript parameter; `_serve` launches the page `build_ui` returns.
- `src/wica_interaction/app_ui.py` — `build_ui` returns the `gr.Blocks` (no `AppUi` handle); the page's legend names 💭 private text.
- `src/wica_interaction/embodiment.py`, `computer/embodiment.py`, `reachy/embodiment.py` — the `say` docstring describes the voice only (the primer quotes it; the model never calls it).
- `src/wica_interaction/gesture_input.py`, `reachy/users.py` — persona sections speak of speaking, not of calling `say`.
- `prompts/computer.md`, `prompts/reachy.md` — speak inside a `<say>` tag; the robot's ordering rule uses a `<next>` note for "speak, then the emotion".
- `tests/test_system.py`, `tests/test_app_ui.py`, `tests-e2e/conftest.py`, `tests-e2e/support.py` — scripted steps as `<say>…</say>` / `<noop/>` text; a spoken reply is one reaction (its end wakes no step); the 💭 item is the text outside the tag.
- Specs above, `specs/_overview.md`, and the docs that named the sink, the `say` call or the `noop` tool.

## Steps

1. Upgrade the lock (`uv lock --upgrade-package wica`), `uv sync --dev`.
2. Drop `set_output_sink` from `wire` and the transcript parameter with it; `build_ui` returns the page.
3. Rewrite the scripted model steps; pin that a spoken reply is one reaction and that only the tag's body reaches the voice.
4. Rewrite the `say` docstrings and the personas for the tag.
5. Specs and docs; statuses.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`; then the live tier, `uv run pytest tests-e2e -rs`, for what has its keys and devices.
