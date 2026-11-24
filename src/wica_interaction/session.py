"""A session that starts and stops again in one process (specs/session.md).

The app (the process and its page) outlives every session: a user stops one, changes the config on
the Setup tab, and starts another, without relaunching. ``Session`` holds that cycle: at most one
running session, built from the app's **saved** config. Start checks it first
(``AppModel.blockers``: its problems, what the machine misses) and says why instead of starting;
Stop tears the session down, leaving nothing of it in the process. The config is locked
(``locked()``) from a Start until its teardown has finished, so the Setup tab cannot save a config
out from under a running session.

The session is built by a function the app passes in (``system.open_session`` bound to an
embodiment), so this module knows no body. No Gradio here: the page reads ``status``, ``reasons``,
``version`` and ``current``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from wica.contrib.gradio import ReactionLog, TranscriptLog

if TYPE_CHECKING:
    from app_setup.app import AppModel

    from wica_interaction.system import AppSystem

__all__ = ["OpenSession", "Running", "Session", "Status"]

_log = logging.getLogger(__name__)

# What a session is doing: the page greys Start and Stop by it.
Status = Literal["stopped", "starting", "running", "stopping"]


@dataclass(frozen=True)
class Running:
    """A started session, and what the page shows of it: the presenters are built once per
    session, subscribed to the agent before it started, and read by every draw of the page."""

    system: AppSystem
    transcript: TranscriptLog
    reactions: ReactionLog
    # Tears the session down (system.close_session over this system).
    close: Callable[[], None]


# Builds one session from the used config and the app's folder: the embodiment's.
OpenSession = Callable[[Mapping[str, Any], Path], Running]


class Session:
    """At most one running session, started from the app's saved config. Thread-safe: the page's
    handlers run on Gradio's worker threads, from any browser tab."""

    def __init__(self, open_session: OpenSession) -> None:
        self._open_session = open_session
        self._lock = threading.Lock()
        self._current: Running | None = None
        self._status: Status = "stopped"
        self._reasons: list[str] = []
        self._version = 0

    @property
    def current(self) -> Running | None:
        """The running session; ``None`` unless the status is ``running``."""
        return self._current

    @property
    def status(self) -> Status:
        return self._status

    @property
    def reasons(self) -> list[str]:
        """Why the last Start did not start (its blockers, or what the build raised); cleared by a
        Start that runs, so every browser tab shows it until then."""
        return list(self._reasons)

    @property
    def version(self) -> int:
        """Bumped at every start and stop: the page draws the session's surfaces again."""
        return self._version

    def locked(self) -> bool:
        """Whether the saved config is in use: from a Start until the teardown has finished."""
        return self._status != "stopped"

    def start(self, model: AppModel) -> list[str]:
        """Start a session from the saved config. Returns why it cannot, in words — its blockers,
        or what the build raised — and an empty list once it runs. Nothing happens unless
        stopped."""
        with self._lock:
            if self._status != "stopped":
                return []
            saved = model.saved_config()
            blockers = model.blockers(saved)
            if blockers:
                self._reasons = blockers
                return list(blockers)
            self._status = "starting"
        running: Running | None = None
        reasons: list[str] = []
        try:
            running = self._open_session(model.used_config(saved), model.path)
        except Exception as exc:  # noqa: BLE001 - said on the page, the app stays up
            _log.warning("the session could not start", exc_info=True)
            reasons = [f"The session could not start: {exc}"]
        finally:
            with self._lock:
                self._reasons = reasons
                if running is not None:
                    self._current = running
                    self._status = "running"
                    self._version += 1
                else:
                    self._status = "stopped"
        return reasons

    def stop(self) -> None:
        """Stop the running session and tear it down; nothing when none runs. The status reaches
        ``stopped`` whatever the teardown met (each of its steps is isolated and bounded)."""
        with self._lock:
            running = self._current
            if running is None or self._status != "running":
                return
            self._current = None
            self._status = "stopping"
            self._version += 1
        try:
            running.close()
        finally:
            with self._lock:
                self._status = "stopped"
