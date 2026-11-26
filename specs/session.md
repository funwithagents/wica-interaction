---
code:
  - src/wica_interaction/session.py
  - src/wica_interaction/system.py
tests:
  - tests/test_session.py
---

# Session

**Status:** Stable

## Purpose

A **session** is one run of the app's runtime, from Start to Stop: the agent, speech in, vision in and the body, built from the saved config. The app (the process and its page) outlives it: a user stops a session, changes the config on the Setup tab, and starts another, without relaunching. This spec is the object that holds that cycle, with no Gradio in it: what Start checks and builds, what Stop tears down, what the page can read of it, and when the config is locked. What a session is made of, step by step, stays in [app.md](app.md) ("Wiring and lifecycle"); the page around it is in [app.md](app.md) ("What the user sees").

## Decided

### The object

```python
# src/wica_interaction/session.py
Status = Literal["stopped", "starting", "running", "stopping"]


@dataclass(frozen=True)
class Running:
    """A started session, and what the page shows of it."""

    system: AppSystem
    transcript: TranscriptLog
    reactions: ReactionLog
    close: Callable[[], None]  # system.close_session over this system


class Session:
    def __init__(
        self,
        open_session: Callable[[Mapping[str, Any], Path], Running],
        *,
        check: Callable[[Mapping[str, Any]], list[str]] | None = None,
    ) -> None: ...

    status: Status
    current: Running | None
    reasons: list[str]  # why the last Start did not start
    version: int  # bumped at every start and stop

    def blockers(self, model: AppModel) -> list[str]: ...
    def locked(self) -> bool: ...
    def start(self, model: AppModel) -> list[str]: ...
    def stop(self) -> None: ...
```

- **At most one session per process**, shared by every browser tab. `Session` is thread-safe: the page's handlers run on Gradio's worker threads.
- **`open_session(used_config, folder)`** is the embodiment's: `system.open_session` bound to the embodiment's loader and constructor ([app.md](app.md), "The entry point"). `Session` knows no embodiment.
- **`check(used_config)`** is the app's rule across sections, in words, empty when none fails: app-setup checks one section at a time, so a rule relating two is the app's ([configuration.md](configuration.md), "Optional sections"). The computer passes `config_problems` (hand signs need the camera); the robot none.
- **`blockers(model)`** is what Start refuses on: `model.blockers(saved)`, then `check(model.used_config(saved))`. The session bar reads it too, so the reason shows before Start is pressed ([app.md](app.md), "What the user sees").
- **`Running` carries its own `close`**, so `session.py` needs nothing of `system.py` at runtime (`system.py` builds the `Running`, and imports `Session`).
- **`Running` holds the presenters.** A `TranscriptLog` and a `ReactionLog` subscribe to the agent's Events when they are built, so they exist once per session, built after the system and before it starts, and the page reads them however many times it is drawn.

### Start

`start(model)` returns why the session cannot start, in words; empty once it runs. Nothing happens unless the status is `stopped`.

1. **Read the saved config**: `model.saved_config()`. A draft in the Setup tab is never run.
2. **Refuse on blockers**: `blockers(model)` — the config's problems, then every missing required item (an extra, a key, a file the config names), then the app's rule across sections (`check`). With any, nothing is built: they become `reasons` and are returned. A missing item that is not required (a model a library downloads itself) does not block.
3. **Build**: status `starting`, then `open_session(model.used_config(saved), model.path)`: load the config against the folder (or, from a file, the file's directory), construct the embodiment, `build_system`, the presenters, `wire`, start ([app.md](app.md), "Wiring and lifecycle").
4. **Running**: `current` is set, `version` bumped, `reasons` cleared.

- **A raise while building** (a document a loader refuses after all, a Command name the agent rejects) tears down what was built, sets `reasons` to one line (`The session could not start: …`) and leaves the status `stopped`. Nothing of the attempt survives.
- **What only a real start can tell still degrades per component**, inside a session that does start: the robot unreachable, a key the provider rejects, a voice that fails to build each land in `AppSystem.missing` and the session's banner, the rest up ([app.md](app.md), "Degraded start"). Blockers are what is known before building; the banner is what building found.
- **Not prepared at Start.** The missing items that are not required are left to the libraries, which fetch them as the session starts; the Setup tab's Prepare gets them ahead.

### Stop

`stop()` does nothing unless the status is `running`. It sets `stopping`, drops `current`, bumps `version`, runs the teardown ([app.md](app.md), "Serve": the agent, the inputs, vision, speech in, the body, the loop), then sets `stopped`. A teardown step that raises or hangs is logged and bounded as before; the status reaches `stopped` whatever happened.

**Nothing of a session survives it.** Each session builds its own event loop and loop thread; Stop stops the loop, joins its thread and closes it. A session's threads (the vision stages, the mic pump, the loop), its sockets, its devices and a daemon the bridge spawned are gone when `stop()` returns, so the next Start builds on a clean process. Once per process, and so outside the session: the OpenTelemetry SDK (each session's collector takes the forwarding over — [latency-view.md](latency-view.md), "Installing the SDK"), the MediaPipe and transformers preloads, logging.

### The lock

`locked()` is true whenever the status is not `stopped`: from the moment a Start begins building until the teardown has finished. The app passes it to `AppModel(…, locked=session.locked)`, so the Setup tab refuses Save and Reset while a session uses the config, and says so. Changing the config is: Stop, edit, Save, Start. The launch choice (below) and the keys are not locked.

### Autostart

- Both apps declare `App(autostart=True)` ([setup.md](setup.md)): by default the session starts at launch, as it always did.
- The user's choice is the Setup tab's "Start the session when the app launches" checkbox, kept in the folder's `launch.json`; `--start` / `--no-start` on the command line override it for one launch (`app_setup.autostart`).
- **From a file** (`--config FILE`), the session starts at launch unless `--no-start`: running a given file is asking for it to run, and a file has no `launch.json` (the checkbox is not drawn) ([setup.md](setup.md), "Three launches"). Its config is never written, so it is read-only whether a session runs or not.
- **At launch**, when `starts_at_launch(model, options.start)` says so, the entry point calls `session.start(model)` before the page is served. With blockers (a new folder with no key yet), the session does not start, the reasons are logged and shown on the Interaction tab, and the page stays up: a service that starts the app at boot never crash-loops on a config that is not ready.

### At exit

The entry point serves the page inside a `try` whose `finally` calls `session.stop()`, so a session running when the process is interrupted is torn down like a stopped one.

## Testing

`tests/test_session.py`, over the fake embodiment, the fake ASR engine and WICA's scripted provider ([testing.md](testing.md)), with an `AppModel` over a temporary folder:

- Start with a blocker (a key unset, an extra reported missing by a stand-in contract) builds nothing and returns it; `reasons` holds it until a Start that runs.
- A `check` that names a problem is in `blockers(model)`, computed over the used config, and Start refuses on it, building nothing; a `check` with nothing to say lets the session start. (`tests/computer/test_app.py`: the computer's config without its camera section, hand signs on, is refused so.)
- Start then Stop: the statuses in order, `locked()` true from the first to the last, `version` bumped twice, the embodiment entered then exited, `current` set then `None`.
- Save is refused while running and accepted once stopped; a config saved between two sessions is the one the second runs with (a changed persona reaches the second session's prompt).
- A raise while building leaves the status `stopped`, the embodiment exited, one reason.
- A second `start` while running, and a `stop` while stopped, do nothing.
- **Ten cycles**: start, one scripted reaction (a typed line answered through the fake voice), stop; after the last, the process has the threads it had before the first (the loop threads joined, the fake's streams closed) and each session answered.

## Open questions

1. **Preparing at Start.** `AppModel.prepare(config, model.deferred(config), on_progress)` before building would move a first start's downloads under a progress line of the Interaction tab. Deferred: the libraries fetch them, and the Setup tab offers them.
2. **Restart on Save.** A session is not restarted when the config changes: the config is locked while it runs.
3. **A real robot across cycles.** The soak test runs over fakes; the bridge's own close and rebuild against the simulator and the robot is the bridge's to guarantee, and is exercised here by hand before each release.
