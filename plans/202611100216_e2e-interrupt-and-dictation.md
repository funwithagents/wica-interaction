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

## In CI (Linux)

- The computer's spoken stop passed with echo cancellation on in one run, and failed in the next: the suppression is intermittent on Linux too (open question 4); the test is an expected failure everywhere.
- The computer's dictation closed empty before the person spoke: the dictation's 10 s initial-silence timeout ran out while the scenario waited for the agent to settle. The person now dictates a second after dictation starts. The ASR also misheard "Call the plumber" ("I'll look under"); the sentence is now "Call the doctor tomorrow morning".
- The robot's dictation met the model's `noop` again: an expected failure until open question 3 is decided.

## Back on the Mac

- The person now dictates once the app's "go ahead" has been said (on the Mac, speech over the app's is swallowed, open question 4), not after the agent has settled (on CI that ran past the 10 s initial-silence timeout).
- The passage still closed after its first sentence, about a second after the person started, in 2 runs of 3, and landed while the agent reacted to the completion of its own reply, so WICA dropped it. Open question 5 of the spec; the computer's dictation test is an expected failure until it is answered.

## Dictation ends on a trigger word on the computer too

Decided with the maintainer (open question 5): the computer template, and the computer's e2e config, end a dictated passage on a trigger word, as the robot's do; a pause inside a note cannot cut it short any more. The computer's dictation test says "Send." after the passage and is no longer an expected failure. `timeout` mode stays offered, not exercised by the tier; its early close is asr-engine's to look into.

On the Mac, with the ASR's finals logged: with a 1 s pause before "Send." the second sentence was lost every time (the final holding the trigger word is dropped with it, so a sentence Flux joins to it goes too); with 3 s the passage arrived whole in 4 runs of 5, Flux once hearing only "Morning." of the second sentence. Haiku then met the note with `noop` in every run, as on the robot, so the computer's dictation test is an expected failure on open question 3, like the robot's.

Then decided with the maintainer: nothing asks the agent to answer a note and the persona lets it stay silent, so the dictation scenario leaves `no_noop_on_input` out and neither dictation test is an expected failure. On the Mac the computer's then passed in 5 runs of 6; in the sixth, as once before, the second sentence never reached the passage (Flux's recognition of the synthetic voice; with 3 s before "Send." the second sentence was lost in 2 runs of 10 overall).

## Full sentences for the trigger word and the stop

The ASR does poorly on short sentences, so the passage now ends on "That is the end of my note, please send it." 1.5 s after the second sentence, and the spoken stop is "Please stop talking now, that is enough for me." On the Mac, with the finals logged: the computer's dictation passed 3 runs of 3 and the robot's 1 of 1, the closing sentence heard whole each time. The robot heard the stop sentence word for word and passed; the computer, over the same cable, heard only "now." or "talking", or nothing, while its voice played: its echo cancellation, open question 4, still an expected failure.

## Runs

On the Mac (2026-11-10):

- Computer: the shown stop and dictation pass; the spoken stop fails with echo cancellation on and passes with it off.
- Robot: the spoken and the shown stop pass; dictation delivers the passage as one input and ends on the trigger word, then failed once on the model's `noop`.

## Verification

- The scenarios pass on the Mac and in CI, apart from what the open questions decide.
- Lint, format, types, the fast tier.
