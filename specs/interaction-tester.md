---
code:
  - src/wica_interaction/testing/__init__.py
  - src/wica_interaction/testing/pair.py
  - src/wica_interaction/testing/tester.py
  - src/wica_interaction/testing/room.py
  - src/wica_interaction/testing/stage.py
  - prompts/tester.md
tests:
  - tests/testing/test_pair.py
  - tests/testing/test_tester.py
  - tests-e2e/test_tester.py
---

# Interaction tester

**Status:** Draft

## Purpose

A harness for testing an interactive app **through the channels a person uses**: a second WICA Interaction instance, the **tester**, talks to the app under test with a voice, listens to it, and shows it hands. It is to interactive apps what the WicaTester is to a `Wica` (`wica-framework/specs/wica-tester.md`): the WicaTester acts on the World, this harness acts on the mic and the camera.

It exists because the two tools we have each stop short:

- **The WicaTester's tester** writes the app's Inputs straight into the World (`set_speech_input`), which skips the ASR, the echo canceller and the hand models: the parts an interactive app adds to a `Wica`.
- **The end-to-end tier's `Person`** ([e2e.md](e2e.md)) goes through those parts, but it is a fixed script. It cannot rephrase when misheard, follow up on what the app said, or interrupt at a moment nobody wrote down.

The tester is **not** the WicaTester with a voice. It is an ordinary app of this package, built by the same `build_system`, with a tester persona. The framework's `wica.testing` is used for what it already does well: recording and judging the timeline.

## Decided

### Vocabulary

| Term | Meaning |
|---|---|
| **SUT** | the app under test: a WICA Interaction system on either embodiment |
| **Tester** | a second WICA Interaction system that plays the person in front of the SUT |
| **Pair** | one SUT and one tester, connected and run together by the harness |
| **Room** | what stands between the two: the audio paths, and the echo ([e2e.md](e2e.md), "The room") |
| **Stage** | what the SUT's camera sees: the image file or the sim props ([e2e.md](e2e.md), "The person") |
| **Driver** | what decides the person's next move: a script, or the tester's LLM |

### The tester is an app

- **Built by `build_system`**, from its own config and its own embodiment, like any app. Its agent, its World entries (`speech_input`), its `say` Command and its lifecycle are the app's own, unchanged.
- **A tester persona** (`prompts/tester.md`): be a realistic person, do not reveal you are testing, follow the scenario's direction, and report what goes wrong with evidence.
- **Three more Commands**, added by the harness:

| Command | Does |
|---|---|
| `report_issue(severity, what, evidence)` | flags a problem; the evidence must quote something the tester heard or saw |
| `show_hand(sign)` / `hide_hand()` | puts a hand photo on the SUT's stage, or takes it away |
| `finish(verdict, summary)` | ends the run |

- **A budget ends the run** when the tester does not: a number of tester turns and a wall-clock limit, both set by the scenario. Two agents with voices do not stop on their own.

### How the tester speaks

The tester's `say` plays into the SUT's mic, through the room. The SUT's whole input path is therefore real: its ASR, its echo cancellation, its barge-in. This holds in both stages below.

### How the tester hears: two stages

| | Stage 1: the probe | Stage 2: the tester's ASR |
|---|---|---|
| Source | each `say` the SUT issues, read from the SUT's agent in the same process | the SUT's speaker, on a second cable, through the tester's own ASR |
| Delivered to the tester | its `speech_input` entry, through the app's typed-text funnel (`submit_speech`) | its `speech_input` entry, by its ASR bridge |
| Tests the SUT's voice | no: only that `say` was issued | yes: what was said is what a listener understood |
| Needs from the SUT | to be importable and in the tester's process | nothing: any app that speaks and listens |
| Cost per run | two LLMs, one ASR | two LLMs, two ASRs |

- **Both stages write the same entry.** The tester's agent reads `speech_input` either way, so moving from stage 1 to stage 2 changes the tester's config and the room's wiring, not the tester.
- **Stage 1 comes first.** It adds no second ASR's mishearings to a tier that is already non-deterministic.
- **Stage 2 makes the harness black-box.** The SUT can then be an app this package cannot import: another process, another language. What the harness can report about such a SUT is limited to what the tester perceived ("The report").

### The room

The room of the end-to-end tier, extended from one voice to two.

- **Stage 1** keeps today's layout: the person's voice (now the tester's) and the SUT's echo into the SUT's mic cable.
- **Stage 2** crosses two cables: each side's speaker is the other side's mic. Each side then hears the other, and its own echo when the room adds it.
- **It stays a process of its own**, for the reason measured in [e2e.md](e2e.md) ("The room"): on macOS a process that plays into a cable stalls its own capture of that cable.

### The stage

Unchanged from the end-to-end tier: the image file the computer's webcam reads, or the hand props of the robot's sim scene. The tester has no hands of its own (a Reachy Mini has none either), so `show_hand` drives the same stage the scripted person does.

### Two drivers

| Driver | The person is | Use |
|---|---|---|
| **Scripted** | today's `Person`: fixed lines, fixed photos, plain pytest | regression: the end-to-end tier's scenarios, repeatable |
| **Live tester** | the tester app, its LLM deciding each move from a scenario's direction | exploration: finding what no script asked |

Both act through the same room and stage. The end-to-end tier is the harness's scripted mode; its scenarios do not change.

### The scenario

For the live tester, a scenario is data: a **direction** (who the person is and what they want from the app), the **budget**, and the **checks** to judge afterwards. The direction is appended to the tester persona. Its file format is open (question 2).

### The report

- **The SUT's timeline** comes from `wica.testing.TestRun` on the SUT, as in the end-to-end tier: reactions, Commands, latencies, dropped triggers. The framework's checks (`no_failed_reactions`, `no_colliding_cancel`, …) are judged over it.
- **The tester's timeline** comes from a second `TestRun`, on the tester.
- **One report per run** holds both timelines, what each side's inputs delivered, the tester's issues and its verdict.
- **Hard rules are checks, never the tester's opinion.** The tester's `report_issue` calls are flags with evidence, listed in the report; they do not fail a run on their own.
- **A black-box SUT has no timeline.** Its report holds the tester's side only.

### Placement

- **`src/wica_interaction/testing/`**, a subpackage imported explicitly, as `wica.testing` is in the framework. The room and the stages move there from `tests-e2e/`, since both drivers need them.
- **Not a tier of its own.** Live-tester runs live in `tests-e2e/`, opt-in, with the same skip rules ([testing.md](testing.md)). They are never a required check: a second model's variance is stacked on the first's.

### The Reachy pair

Two Reachy Minis facing each other in one sim, the SUT in one and the tester in the other. It is the harness's demonstration, and the ground for a future robot detector as a vision input: each robot's camera sees the other.

- **One shared physics**, each robot behind its own `reachy-mini-bridge`.
- **It depends on reachy-mini-bridge** serving two robots from one MuJoCo model, which upstream does not do. A spike there (branch `spike/two-robot-sim`) decides whether and how.
- **It adds nothing to what is tested by sound.** The first pair is computer against computer.

### Phases

Each is its own plan.

1. **The subpackage and the scripted driver.** Move the room, the stages and `Person` into `src/wica_interaction/testing/`; the end-to-end tier runs on them unchanged.
2. **The live tester, stage 1.** The tester app, its persona and Commands, the probe, the budget, the merged report. Computer against computer.
3. **Stage 2.** The crossed cables and the tester's ASR.
4. **The Reachy pair**, once the bridge serves two robots from one sim.

## Open questions

1. **When the probe delivers a reply.** A person hears a reply as it is spoken. The probe can deliver the text when `say` is issued (too early: the tester answers before the SUT has finished), or when it completes (the tester can never interrupt, since it hears nothing until the end). A cancelled `say` has no "text heard so far". One option: deliver on completion, and give the tester a `partner_speaking` entry so it can choose to talk over the SUT. To decide before phase 2.
2. **The scenario's file format.** The framework leaves the live driver's format open too (`wica-tester.md`, open question 1). Reusing its scenario files for the `checks` list would let the same check names serve both.
3. **Talking while the SUT reacts.** WICA drops a trigger that arrives during a reaction ([e2e.md](e2e.md), "The conversation"). The scripted person waits for a settled agent; a live tester, like a person, does not know to. The dropped inputs will show in the report, and may dominate the first runs. Whether the tester persona is told to wait, or the drops are left as findings, is to decide.
4. **One process or two.** Stage 1 needs the SUT and the tester in one process, each on its own loop. Both then load their own models and audio streams; whether two `build_system`s coexist in one process (the MediaPipe import order, the audio devices) is to try.
5. **From a live run to a script.** The WicaTester exports a live run as a scenario. Here the export would be `Person.say` and `Person.show` lines. Worth doing only once live runs find things worth pinning.
6. **The tester's own embodiment on the computer.** It needs a voice and no camera or hand models. Whether it is the computer embodiment with vision off, or a lighter embodiment of its own, is to decide in phase 2.
