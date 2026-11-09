# Benchmarking models on the interaction

> **Status (2026-11-09).** The benchmark is designed, not built. Its design is a Draft spec, `specs/benchmark.md`, and its implementation plan, `plans/202611091105_model-benchmark.md`, is Todo; both land in the repository with the benchmark work. The framework side it builds on, `wica.benchmark` and `wica.testing`, is implemented and pinned. This page describes the benchmark as designed, says what exists, and grows as the plan lands.

## What it measures

Not a text task: **a model under the WICA runtime primer, one of this app's personas and its Command set**. The question is which model, behind the persona, replies through `say`, never collides on the robot's one body, confirms its actions out loud, cancels when told to stop, stays in the person's language, and how fast and at what cost.

The runner reads the result as one row per model: the pass rate over *k* repetitions (pass^k), compliance with the checks, the turns answered, the reaction and model latencies, the tokens and words per turn. There are no thresholds; the summary is for reading. The speech engines are **not** measured: speech in and out are bypassed, so the latency a person feels, end-of-utterance detection and synthesis start, is out of scope here.

## How it works

The framework provides the machinery ([WICA's benchmarking guide](https://github.com/funwithagents/wica-framework/blob/main/docs/guides/benchmarking.md)): a matrix of models, a directory of scenarios, a `Target` the application implements to stand itself up, a runner over matrix × scenarios × *k*, a summary and a CLI. The framework's own benchmark, the conversation demo over a matrix of models, is the worked example ([its results](https://github.com/funwithagents/wica-framework/blob/main/benchmarks/README.md)).

This app provides what is specific to it:

- **The system under test is the real app**, stood up through the same build and wire steps the entry points use: the real persona with its seeded sections, the real `start_dictation` and `stop_dictation` Commands, the real transient inputs ([../reference/agent.md](../reference/agent.md)).
- **A bench embodiment**, a third body next to the computer and the robot. Its `say` records the text and **sleeps for the time the words would take to speak** (2.5 words per second by default), so a `say` stays `running` as long as a real one and sequencing, collision and interruption have something to measure. For the robot body it registers the real `play_emotion`, `get_motors_state` and `set_motors_state` over a bench robot that takes the time an emotion takes and honours the motors-off rule; for the computer body it registers nothing.
- **Speech and vision bypassed through the lifecycle's seams.** The speech recognizer is a double that captures nothing, so the dictation Commands exist with their real replies while the tester writes `speech_input` directly, the way the typed-text box does; the hand pipeline runs idle over a camera that publishes no frame, and a scenario shows a sign by writing `hand_gesture_1` directly.
- **Two config files** per run: a body file (`asr`, `vision` and a `bench` section naming the body and its persona, no `wica` section) and the framework-format matrix, where the agent block of each row is what differs between models.

## The sessions

Sessions rather than one-rule probes: each is a paced conversation, the tester waiting for the reply to be issued and spoken before the next turn, and the report names the clause a model broke. A session skips on a body that lacks what it requires.

| Scenario | Session | Requires |
|---|---|---|
| `conversation` | The framework's generic session, copied verbatim: a greeting, "What can you do?", a long story interrupted with "Stop!" as soon as the reply is issued, two utterances half a second apart, a goodbye, then silence. Seven turns | `speech_input`, output: both bodies |
| `robot` | The embodied session, one persona rule per turn: a greeting; "show me you are happy" (an emotion, then the spoken confirmation); "What time is it?" (digits spelled out); the long story interrupted by a `stop` hand sign while `say` runs; "turn your motors off"; "show me you are curious" with the motors off (recover, then retry, in order); "I want to dictate a note" then an empty passage; a goodbye. Nine turns | `speech_input`, `hand_gesture_1`; `play_emotion`, `set_motors_state`, `start_dictation`; output: the reachy body only |
| `conversation-fr` | A short French session: a greeting, "Qu'est-ce que tu sais faire ?", a goodbye, then silence. Three turns, `params.language = "fr"` | `speech_input`, output: both bodies |

## The persona checks

The framework's built-in checks cover the primer: every input answered, no `noop` on a request, no failed reaction, no colliding cancel. This app adds one hard check per rule its personas state ([personas.md](personas.md#the-action-rules)); a model that breaks one does not follow the persona, and the finding enters the summary's compliance column.

| Check | Persona line | Fails when |
|---|---|---|
| `no_collision` | "never issue, in the same turn, two commands that would collide" | one reaction holds `say` with `play_emotion`, two `play_emotion`, or `play_emotion` with `set_motors_state` |
| `spoken_reply_after_action` | "Acting is not answering" | an action Command is issued in a turn and `say` is not issued later in that turn |
| `emotion_in_vocabulary` | "use only those" | a `play_emotion` names an emotion outside the body file's list |
| `no_motor_check` | "never check their state first" | `get_motors_state` is issued before any move has reported the motors off |
| `no_dictation` | "Use it only when they clearly want to dictate" | `start_dictation` is issued in a session that never asks for it |
| `spoken_style` | "no bullet points, headings, code blocks, or emoji" | a `say` text holds markdown markers, an emoji, or more than one newline |
| `no_digits` | "Spell things out" | a `say` text holds a digit |
| `language` | the person's language | fewer than half of a `say` text's stop-word hits belong to the session's language; approximate by design, for the gross case only |

## Running it

As designed, a third entry point, `wica-interaction-bench`, wraps the framework's CLI: `--config` picks the body file (the reachy one by default), and the rest are the framework's flags:

```
wica-interaction-bench [--config benchmarks/config.reachy.json]
                       [--matrix FILE] [--only GLOB ...] [--exclude GLOB ...] [--tags TAG ...] [--exclude-tags TAG ...] [--include-disabled]
                       [--scenarios DIR|FILE] [--scenario NAME ...] [--out DIR] [--k N] [--result NAME]
```

Run from the repo root, `uv run wica-interaction-bench --k 5` runs every enabled row of `benchmarks/matrix.json` whose key is set (the rest skip) over every session, repeats each five times, and prints the summary. A batch lands in `benchmarks/runs/<stamp>/`, git-ignored working data; `--result <name>` keeps it under `benchmarks/results/<stamp>_<name>/` with a `description.md` and a `findings.md` to finish by hand, and `benchmarks/README.md` lists every kept result. The key-less path is the matrix's `fake` fixture, scripted to play the whole `conversation` session through this app:

```
uv run wica-interaction-bench --only fake --include-disabled --scenario conversation
```

**None of this exists in the repository yet**: no `bench` subpackage, no `benchmarks/` folder, no entry point. The commands above are the plan's, written here so the page is ready to be checked against them.

## Status and what is deferred

Built: the framework's scenario format and loader, its checks and measures over a finished timeline, and `wica.benchmark`'s matrix, `Target`, runner, summary and CLI, all pinned by this repo's lock. To build, in the plan: the bench embodiment, the body-file loader, the target and entry point, the persona checks, the three sessions and the matrix, the fast-tier chain tests and a live smoke test.

Deferred by the spec:

- **A live-LLM tester**: an impatient, interrupting or off-topic tester persona over the same bench embodiment, once the framework ships one.
- **A judge over the trace**: relevance, naturalness, tone, by a separate model with a rubric lifted from the persona; the `language` check's stop-word heuristic is the only soft grading in v1.
- **Embodiment-level latency**: what a person feels, through real speech in and out, belongs to the instrumentation's spans, not to this benchmark.
- **History-cut sessions**: a session long enough to trip the history bounds is costly and not in the first set.
