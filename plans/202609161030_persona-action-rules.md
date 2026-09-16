# Persona action rules: no colliding Commands, ordering, spoken feedback

**Status:** Done

Implements the "The persona's action rules" section added to [specs/app.md](../specs/app.md) (and the cross-reference in [specs/robot-control.md](../specs/robot-control.md)): the committed persona, `prompts/assistant.md`, now teaches the model three things WICA's generic runtime primer cannot — that this robot's Commands share one body and one speaker so two colliding ones (`say` + `play_emotion` being the motivating case, not the only one) must not be issued in the same turn, that dependent actions are issued in order across turns, and that every request gets a spoken reply after the action (cancel on request, then confirm out loud). Deliberately leaves out any enforcement in code — no lock around `say`/`play_emotion` (app.md open question 4) — the behavior stays as it was; only the prompt changes.

## Why

Observed: the model emitted `say` and `play_emotion` in one response; WICA dispatches both at once, and the emotion's own sound played over the speech. WICA's primer now states the concurrency generically (wica-framework plan `202609161000_primer-concurrent-commands.md`); which Commands collide is app knowledge, so it belongs in the persona. The feedback rule addresses a second observation: a request to stop something answered by a bare `cancel_command`, with nothing said.

## Scope

- `prompts/assistant.md` — two new sections ("One body: never collide, act in order", "Always answer out loud") before "Turns".
- `specs/app.md` — the Persona bullet gains "The persona's action rules"; `prompts/assistant.md` joins the spec's `code:` frontmatter; open question 4 records the deferred lock.
- `specs/robot-control.md` — one bullet noting `play_emotion` is not independent of `say` and pointing at the app spec.
- `plans/_index.md` — this row.

## Steps

1. Write the two persona sections.
2. Edit the two specs; add the frontmatter path.
3. Verify (below).

## Verification

Persona text is verified by reading (the tests drive the scripted `fake` provider, never a real model's compliance). `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all pass (the project-map test checks the new frontmatter path exists).
