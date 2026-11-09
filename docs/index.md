# Documentation

The documentation of WICA Interaction, by what you want to do. The [README](../README.md) is the landing page: what the app is, its status, the install recipes and a first run; [getting-started.md](getting-started.md) continues from there.

## By task

| I want to… | Read |
|---|---|
| run the app for the first time | [getting-started.md](getting-started.md): install, a template, the keys, the computer app, then the robot in the simulator and on a Reachy Mini |
| install only what I run, or know what my platform supports | [reference/install.md](reference/install.md): the install recipes, every extra, the platforms, the system packages, the first-run downloads |
| understand or change a field of the config file | [reference/configuration.md](reference/configuration.md): the whole file section by section, the templates, what a loader rejects |
| choose the LLM, the speech recognizer or the voice | [reference/configuration.md](reference/configuration.md): each section names its providers and links to the engine's own pages |
| run it on my computer: devices, echo, noise, the webcam | [guides/computer.md](guides/computer.md) |
| run it on a Reachy Mini: the simulator, a Lite over USB, the voice, face tracking | [guides/reachy-mini.md](guides/reachy-mini.md) |
| show the agent hand signs | [guides/hand-signs.md](guides/hand-signs.md): the vocabulary, the hold, the Vision line, the snapshots |
| dictate a longer passage | [guides/dictation.md](guides/dictation.md): the two modes, how a passage ends, the stop button |
| change what the agent is, says or does | [guides/personas.md](guides/personas.md): the prompt files, what is appended at startup, the robot's action rules |
| know what the model sees and can do | [reference/agent.md](reference/agent.md): the World entries, the Commands per body, the persona sections, the transcript's reading of each |
| compare models on this app | [guides/benchmarking.md](guides/benchmarking.md): the bench embodiment, the sessions, the persona checks, the runner |
| know what v1 does not do yet | [guides/limits.md](guides/limits.md): no barge-in, no streamed speech, no persistence, one step at a time |
| find out why nothing happens, or what a banner line means | [guides/troubleshooting.md](guides/troubleshooting.md): the checks for each symptom, routed to the page that explains it |

## The references

- [reference/configuration.md](reference/configuration.md): the one JSON file, section by section, with each engine's fields summarized and linked to the engine's own reference.
- [reference/install.md](reference/install.md): the extras table, the platforms and what each needs installed.
- [reference/agent.md](reference/agent.md): the agent's surface, for whoever edits a persona or reads a transcript.

## Beyond the user documentation

- [specs/](../specs/_index.md) is the normative design: every guarantee with its rationale, one spec per concept, each with a status; [specs/_overview.md](../specs/_overview.md) is the architecture in one page.
- [plans/](../plans/_index.md) records what was built, and what is being built.
- [AGENTS.md](../AGENTS.md) is the operating manual for working in the repository.
