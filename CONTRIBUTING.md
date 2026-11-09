# Contributing

Thanks for your interest in WICA Interaction. This page says how the project is run today, so you know what to expect before you spend time on it.

## How the project is run

The app is developed by one person, in the open, as a spec-driven project: every change starts as a design in [specs/](specs/_index.md), becomes an implementation plan in [plans/](plans/_index.md), and only then code. [AGENTS.md](AGENTS.md) describes that workflow and the checks every change passes. The app is at version 0.1 and is shaped as it is used, together with the five first-party packages it is built on: a config field, a persona rule or a module can change between commits, without a deprecation period. It has been run on macOS with the simulator and a Reachy Mini Lite; the other platforms and the wireless robot are expected to work from the dependencies and have not been tried ([README](README.md#project-status)).

## Pull requests: not for now

External pull requests are not accepted for now. This is not a judgement on contributions: while the design is settling, a change written against yesterday's specs usually costs more to reconcile than to redo, and reviewing it to the project's standard (a spec, a plan, the two test tiers, a run on the robot) takes the time that would otherwise go into the design work that would make it unnecessary. A pull request will be closed with thanks and, when the idea is a good one, turned into an issue so it is not lost.

This file will say so when that changes.

## Feedback: yes, please

Issues are the way in, at [github.com/funwithagents/wica-interaction/issues](https://github.com/funwithagents/wica-interaction/issues), and every kind of feedback is welcome:

- **Bug reports.** Say what you ran: the command, your config with the keys removed, the OS and Python version, the banner lines if any; what you expected, what happened, and the logs. For a robot, say which backend (`sim`, `real`, `fake`) and, on a robot, whether it is a Lite over USB.
- **Reports from a platform, a robot or a model the author does not test on.** Linux, Windows, a wireless Reachy Mini, a model that drives the personas well or badly (talks over its own emotion, never confirms an action, ignores a stop sign): each is valuable, with a benchmark row where you can ([docs/guides/benchmarking.md](docs/guides/benchmarking.md)).
- **Config and persona feedback.** What you would want the config file, the hand-sign vocabulary or the personas to do differently, and why. Pointing at the spec you are reacting to helps.
- **Questions.** If the README, a page under [docs/](docs/index.md) or a spec left you guessing, that is a documentation bug worth an issue too.

## Working with the code anyway

The code is MIT-licensed, so forking it and building on it is welcome. The commands to lint, type check and run the two test tiers are in [AGENTS.md](AGENTS.md), which also explains how the specs, plans and their statuses fit together; the user documentation starts at [docs/index.md](docs/index.md). If your fork grows something you think belongs upstream, open an issue describing it: that is the conversation that will lead to accepting pull requests later.
