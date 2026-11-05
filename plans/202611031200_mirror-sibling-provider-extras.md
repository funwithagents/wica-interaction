# Mirror the siblings' provider extras

**Status:** Done

Implements the settled behavior in `specs/project.md` ("Runtime dependencies — a base plus extras", "The `dev` group"): every agent and voice provider extra of `wica` and `tts-engine` gets a matching `wica-<name>` / `tts-<name>` extra here, and the `dev` group installs what the example configs and tests run instead of `all`. Leaves out the siblings' `mcp` and bundle extras, and asr-engine's Kyutai extras (unsolvable against `vision-modules`' numpy floor).

## Scope

- `pyproject.toml` — `wica-mistralai`; the ten missing `tts-*` extras; `all` widened to them; `dev` lists its extras explicitly; the stale `override-dependencies` comment on the TTS extras replaced
- `uv.lock` — relocked with the new extras (no sibling commit moved)
- `README.md` — the Extras table and what `uv sync --dev` installs
- `specs/project.md`, `specs/computer-embodiment.md`, `specs/reachy-embodiment.md` — the extras tables / provider lists

## Steps

1. Add the extras to `[project.optional-dependencies]` and to `all`, one per sibling provider extra, under the sibling's name.
2. Replace `wica-interaction[all]` in the `dev` group with the explicit extras: the embodiments and features, every `wica-*`, `asr-deepgram`, `tts-elevenlabs`, `tts-gradium`, `tts-pocket`.
3. Try `asr-kyutai-mlx` / `asr-kyutai-torch`: both fail to lock (`moshi-mlx` / `moshi` pin `numpy<2.3`, `vision-modules` needs `numpy>=2.5.3`), so they are left out with a comment saying why.
4. `uv lock`, check no `source = { git … }` line changed, `uv sync --dev`.
5. Update the README and the specs' extras tables.

## Verification

`uv lock` resolves; `uv sync --dev` succeeds; lint, format, type check and the full `tests/` tier pass (no test change: no provider SDK is imported by the code or the tests). Mark this plan `Done` (here and in [_index.md](_index.md)) and `specs/project.md` back to `Implemented` once all pass.
