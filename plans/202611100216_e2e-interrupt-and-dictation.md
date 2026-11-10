# The end-to-end tier, part 3: interrupt and dictation

**Status:** In progress

Implements [specs/e2e.md](../specs/e2e.md) scenarios 5 (interrupt: a spoken stop, and a shown `stop`) and 6 (dictation) on both embodiments. Leaves out scenario 7 (echo), whose setup is still an open question.

## Scope

- `tests-e2e/scenarios.py` — `interrupt_spoken`, `interrupt_sign`, `dictation`.
- `tests-e2e/test_computer.py`, `tests-e2e/test_reachy.py` — the three tests each.
- `specs/e2e.md` — the scenarios' rows, the layout, open questions 3 and 4.
- `plans/_index.md` — this plan's row.

## What building it revealed

- **A bare "Stop." is not enough.** The first spoken stop delivered nothing; the scenario says "Stop, please. That's enough." That alone did not fix the computer, though (next).
- **The computer's echo cancellation swallows the person while the app speaks.** With it on, the spoken stop never reaches the agent, run after run; with it off, it passes; the robot, with no software echo cancellation, passes. Open question 4 of the spec; the computer's spoken-stop test is an expected failure (`xfail`, strict) until it is answered.
- **The model may meet a dictated note with `noop`.** The passage arrived as one input and dictation ended on "send", but Haiku answered with `noop` once on the robot, which the framework's `no_noop_on_input` check fails. Open question 3 of the spec, with the emotion overlap.

## Runs

On the Mac (2026-11-10):

- Computer: the shown stop and dictation pass; the spoken stop fails with echo cancellation on and passes with it off.
- Robot: the spoken and the shown stop pass; dictation delivers the passage as one input and ends on the trigger word, then failed once on the model's `noop`.

## Verification

- The scenarios pass on the Mac and in CI, apart from what the open questions decide.
- Lint, format, types, the fast tier.
