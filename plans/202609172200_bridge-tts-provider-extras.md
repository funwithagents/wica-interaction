# Adopt the bridge's TTS provider extras, default to pocket-tts / voice george

**Status:** Done

Implements the settled dependency policy in `specs/project.md` ("Runtime dependencies") and the
default voice in `specs/configuration.md` after `reachy-mini-bridge` commits `4f5267e` /
`70fd03a`: the bridge's `[tts]` extra is gone, `tts-engine` is a **base** bridge dependency
(referenced by its GitHub URL upstream), and the providers are mirrored as `[tts-elevenlabs]` /
`[tts-pocket]`. This app switches to `reachy-mini-bridge[tts-pocket]`, drops its direct
`tts-engine[elevenlabs]` entry, and makes the committed default voice the **local pocket-tts
model with voice `george`** — so a fresh checkout has a talking robot with no TTS credential.

Deliberately left out: any change to the ASR side (`asr-engine[deepgram]` already selects the
extra, landed in `a402bbf`), to `vision-modules`, or to how `say` rides the api.

## Scope

- `pyproject.toml` — `reachy-mini-bridge[tts]` + `tts-engine[elevenlabs]` → `reachy-mini-bridge[tts-pocket]`; **drop** the `tts-engine` `[tool.uv.sources]` entry (see step 1 — it can no longer take effect); refresh the comments.
- `config.example.json` — `reachy.tts` becomes a `pocket` block with `voice: "george"`.
- `specs/project.md` — the runtime-dependency paragraph: the bridge's provider extras, `tts-engine` no longer a direct dependency, why there is deliberately no path source for it.
- `specs/configuration.md` — the two example `reachy.tts` blocks use `pocket` / `george`; note the extra that must match `module.type`.
- `specs/app.md` — the `TTS: …` degrade axis: a *voice build failure*, of which an unset key is one case (the default provider has no key).
- `specs/_index.md`, `specs/_overview.md`, `README.md` — drop the `[tts]`-extra wording; `tts-engine` arrives as a base dependency of the bridge.
- `tests/test_config.py` — the composed fixture's `reachy.tts` is a pocket block; assertions and the env-var list follow.
- `tests/test_app.py` — `_tts_degrade_config` no longer needs a provider SDK: an unknown `module.type` makes the bridge's synthesizer build fail deterministically with no extra installed.

## Steps

1. **`pyproject.toml`.** Replace the two TTS runtime entries with the single
   `"reachy-mini-bridge[tts-pocket]"`. Comment: the bridge owns `tts-engine` as a base dependency
   and maps one extra per provider onto tts-engine's own — the extra must match the
   `reachy.tts.module.type` in the config, and `tts-pocket` matches the committed
   `config.example.json` (`"type": "pocket"`). **Drop the `[tool.uv.sources]` `tts-engine` entry.**
   The intent was to keep it so a local `../tts-engine` checkout stays live here, but that is no
   longer reachable and was verified against uv rather than assumed:
   - As a *transitive* requirement, the entry is simply ignored — `uv sync` resolves tts-engine
     from `git+https://github.com/funwithagents/tts-engine@main`. uv sources apply only to a
     project's own declared dependencies.
   - Re-declaring `tts-engine` directly to force the source **fails the resolve**:
     `Requirements contain conflicting URLs for package 'tts-engine': file:///…/tts-engine
     (editable) vs git+https://github.com/funwithagents/tts-engine@main`. The bridge's GitHub
     reference is a PEP 508 direct reference baked into its published metadata, which a downstream
     source cannot redirect.
   - `[tool.uv] override-dependencies` *does* redirect it, but replaces the whole requirement — so
     it silently drops the `[pocket]` extra unless repeated — and needs an absolute `file:///`
     path. Fine as an uncommitted local convenience, not as committed config.

   So the entry becomes dead-or-breaking text: remove it, and leave a comment recording why there
   is deliberately no tts-engine source here and how to drive a local checkout when needed.
2. **`config.example.json`.** `reachy.tts.module` → `{"type": "pocket", "voice": "george",
   "device": "auto"}` — matching the bridge's own example config. No `api_key_env`.
3. **`tests/test_config.py`.** In `_composed()`, make the `reachy.tts` block the pocket one
   (keeping the `player` block, which proves it is accepted and ignored). The happy-path test
   asserts `module["type"] == "pocket"` and that the extra field carried through verbatim is
   `module["voice"] == "george"`. Drop `ELEVENLABS_API_KEY` from the env-var list the
   no-credentials test clears (the default voice needs none).
4. **`tests/test_app.py` — provider-SDK-free TTS degrade.** `_tts_degrade_config` currently
   builds an `elevenlabs` block with an unset `api_key_env`; without the `elevenlabs` extra
   installed, tts-engine raises on the *lazy import* before it ever reads the env var. Replace the
   block with an unknown module type (`{"module": {"type": "no_such_tts_module"}}`), which
   `tts_engine.modules.load_module` rejects with `ConfigError: Unknown TTS module type:
   'no_such_tts_module'` — a real synthesizer-build failure that needs no SDK, no env var, and no
   model load. Rename the test to `test_failed_voice_build_degrades_only_the_voice`, drop the
   `monkeypatch` fixture and the `delenv` call, and assert the banner entry starts with `TTS:` and
   carries the bridge's recorded error (substring `no_such_tts_module`), proving the app surfaces
   `api.synthesizer_error` rather than a canned string. Everything else the test asserts (robot
   up, `bridge` up, no `Robot:` entry, `say` → `"TTS error: …"`, the agent still reasoning over
   typed input) is unchanged — that is the contract being pinned.
5. **Specs and docs.**
   - `specs/project.md`: rewrite the TTS half of the runtime-dependency paragraph — `reachy-mini-bridge`
     is pulled with `[tts-pocket]`; `tts-engine` is a **base** dependency of the bridge (a direct
     GitHub reference in its metadata, so it resolves with no source declaration here) and is no
     longer listed as a direct dependency of this app; the bridge mirrors one extra per tts-engine
     provider (`tts-pocket` / `tts-elevenlabs`), so switching the config's `reachy.tts.module.type`
     means swapping that extra; `pocket` is the committed default because it is local — no key, no
     network at synthesis time — at the cost of `torch` plus a first-run weight download, which the
     already-present `vision-modules[hand]` makes free. Replace the note on why the `tts-engine`
     `[tool.uv.sources]` entry stays with why there is deliberately none (step 1), including the
     `override-dependencies` escape hatch for local tts-engine work.
   - `specs/configuration.md`: both `reachy.tts` examples become the pocket block; in the `tts`
     bullet, add that the provider named by `module.type` needs its bridge extra installed
     (`reachy-mini-bridge[tts-pocket]` / `[tts-elevenlabs]`) and that a missing one is a build
     failure that degrades to no voice, like an unset key.
   - `specs/app.md`: in the `TTS: …` axis, generalize "a build failure (typically the key unset)"
     to a build failure — an unset key, a provider whose extra is not installed, or a malformed
     `tts` block — noting the committed default (`pocket`) needs no key at all.
   - `specs/_index.md`, `specs/_overview.md`, `README.md`: replace the "`tts-engine` arrives
     through the bridge's `[tts]` extra" / "`reachy-mini-bridge[tts]` pulls `tts-engine`" wording
     with the base-dependency + provider-extra story; in the README's run paragraph, note that the
     committed `reachy.tts` is the local pocket model, so no TTS key is needed (first run
     downloads the weights).
6. **Statuses.** `specs/project.md`, `specs/configuration.md` and `specs/app.md` are edited in
   ways that require code/config changes, so each goes `Implemented → Updated` while this plan is
   `In progress`, then back to `Implemented` once it is `Done` — both in the file and in
   `specs/_index.md`. Add this plan's row to `plans/_index.md`.
7. **Resolve.** `uv sync --dev` and confirm the lock picks up `pocket-tts`, drops `elevenlabs` /
   `miniaudio`, and resolves `tts-engine` from the bridge's git reference.

## Verification

- `uv sync --dev` succeeds; `.venv` has `pocket_tts` importable and no `elevenlabs` requirement in
  the dependency graph.
- The committed default voice really is a voice, with no credential set:
  `TTSEngineSynthesizer(json.load(open("config.example.json"))["reachy"]["tts"])` constructs and
  reports `sample_rate == 24000`.
- `uv run pytest` — in particular the reworked `test_failed_voice_build_degrades_only_the_voice`,
  `test_absent_tts_block_is_reported_on_the_tts_axis`, and
  `test_happy_path_builds_both_engine_configs_and_a_bridge_config`.
- `uv run ruff check .`, `uv run ruff format .`, `uv run pyright` all clean.
- Mark this plan `Done` (here and in [_index.md](_index.md)) and flip the three specs back to
  `Implemented` only once all of the above pass.
