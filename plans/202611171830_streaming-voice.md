# The voice streams on both bodies

**Status:** Done

Implements [specs/embodiment.md](../specs/embodiment.md) (`voice`), [specs/computer-embodiment.md](../specs/computer-embodiment.md) ("`say`") and [specs/reachy-embodiment.md](../specs/reachy-embodiment.md) ("`say`"): each body's `say` takes WICA's `OutputStream` and speaks a `<say>` tag from its first words, the computer through `TTSEngine.say_stream`, the robot through the bridge's new `say_stream`. Leaves out `OutputStream.heard` (the history after a cut) and a word-by-word reveal on the page.

## Scope

- `uv.lock` — `reachy-mini-bridge` from `d2516aa` to `93cba9e` (`say_stream`).
- `src/wica_interaction/embodiment.py` — `Voice` takes an `OutputStream`; `speakable(text)` waits for the first words and keeps every piece; `EMPTY_UTTERANCE`.
- `src/wica_interaction/computer/embodiment.py` — `say(text: OutputStream)` into `engine.say_stream`, `TTSError` mapped; the engine kept instead of `TTSTools`.
- `src/wica_interaction/reachy/embodiment.py` — `say(text: OutputStream)` into `bridge.say_stream`.
- `tests/fakes.py` — `utterance(*pieces, close=)`; `FakeEmbodiment.say` reads a stream.
- `tests/computer/`, `tests/reachy/` — the fakes' `say_stream`; the voice read as streaming by WICA, the first words before the tag closes, empty and whitespace tags, blank lead pieces kept, errors mapped.
- Specs above, `specs/app.md`, `specs/_overview.md`, `docs/guides/limits.md`, `docs/reference/agent.md`, `docs/index.md`.

## Steps

1. Upgrade the bridge pin.
2. `speakable` and the `Voice` type; both `say` on it.
3. Fakes and tests.
4. Specs and docs; statuses.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`; then `uv run pytest tests-e2e -rs` (computer and the Reachy sim).
