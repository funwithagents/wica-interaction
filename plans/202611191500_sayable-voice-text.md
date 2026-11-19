# Sayable voice text

**Status:** Done

Implements the settled behavior in `specs/embodiment.md` ("What reaches a TTS is sayable"): `speakable` cleans a `<say>` tag's body into sayable text — Unicode, markup, whitespace, punctuation runs, the utterance's edges — as a stream, so neither voice ever hands a TTS ellipsis runs, tag fragments, Markdown or emoji. It leaves out saying symbols as words and rewriting URLs. `tts-engine`'s sentence splitter no longer sends a word-free piece on its own (fixed in that repo); this plan bumps its pin.

## Scope

- `src/wica_interaction/sayable.py` — new module: a pure `SayableText` cleaner (`feed`/`end`) and its patterns, and `speakable` (moved from `embodiment.py`), which streams through it and returns `None` when no letter or digit survives; a debug log when the text changed
- `src/wica_interaction/embodiment.py` — `speakable` moves out; the protocol module holds no text cleaning
- `src/wica_interaction/computer/embodiment.py`, `src/wica_interaction/reachy/embodiment.py` — import `speakable` from `sayable`
- `AGENTS.md` — the project map's row for `sayable.py`
- `tests/test_sayable.py` — functional tests of `speakable`: each case fed whole, per character and in pieces of four, same output
- `tests/computer/test_embodiment.py` — the leading-whitespace test now pins the cleaned pieces; punctuation-only tags join the empty-utterance cases
- `tests/reachy/test_embodiment.py` — punctuation-only tags join the empty-utterance cases
- `docs/guides/limits.md` — one sentence on what the voice drops
- `uv.lock` — `tts-engine` bumped to the commit whose `segment_sentences` never yields a piece without a letter or digit

## Steps

1. In `sayable.py`, `_clean(chunk, *, first)`: NFKC → drop `Cc` (but whitespace), `Cf`, `Co`, `Cs`, `So`/`Sk` (but `°`), variation selectors → Markdown links to their text → tags, then marker runs (`[/?!]*[<>][<>/?!]*`) → Markdown characters to spaces → whitespace runs to one space → punctuation groups separated by whitespace to the first, dot/ellipsis mixes inside a group to `…` → when `first`, strip leading whitespace and punctuation.
2. `SayableText.feed(piece)`: append to a raw buffer; the safe cut is just after the last letter or digit before any held construct (an unclosed `<` within 64 characters, an unclosed `[` within 700); clean and emit `raw[:cut]`, keep the rest. Because every emitted chunk ends on a letter or digit, no pattern spans two chunks. `end()` cleans the rest and strips trailing whitespace.
3. `speakable`: feed pieces until the first non-empty cleaned chunk (`None` at the end of the tag otherwise), then return a stream of that chunk and the rest; accumulate raw and cleaned text, log both at debug when they differ.
4. Tests, the docs sentence; spec back to `Implemented`, this plan `Done`.

## Verification

`uv run pytest tests/test_sayable.py tests/computer tests/reachy`, then `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`. Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
