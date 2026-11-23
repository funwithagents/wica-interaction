"""Functional tests for the robot's page view (specs/reachy-embodiment.md, "The page view").

The drawing and the users table are pinned as pure functions; `robot_view` is driven over a real
`ReachyMiniBridge` on its `fake` backend, its face detector a `ScriptedFaces` (the bridge's `custom`
source), so the faces, users and head tracking it shows are what the bridge actually published.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from reachy_mini_bridge import (
    Face,
    HeadTrackingReport,
    ReachyMiniBridge,
    User,
    UsersReport,
)
from reachy_mini_bridge.config import ReachyMiniConfig

from tests.reachy.fakes import ScriptedFaces, seeing_config
from wica_interaction.config import AppConfig
from wica_interaction.embodiment import EmbodimentView
from wica_interaction.reachy.config import ReachyConfig
from wica_interaction.reachy.embodiment import ReachyEmbodiment
from wica_interaction.reachy.view import (
    FACE_MARKER_RGB,
    draw_faces,
    head_line,
    robot_view,
    users_markdown,
)

GREEN = np.array(FACE_MARKER_RGB, dtype=np.uint8)


def _green(image: np.ndarray) -> np.ndarray:
    return (image == GREEN).all(axis=2)


# --- Drawing ----------------------------------------------------------------------------------------


def test_a_face_is_a_square_where_it_is_and_the_followed_one_is_thicker() -> None:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    out = draw_faces(frame, [(-0.5, 0.0), (0.5, 0.0)], target=1)
    assert not _green(frame).any()  # a copy: the feed's frame is untouched
    green = _green(out)
    left, right = green[:, :160], green[:, 160:]
    assert left.any() and right.any()
    # The square is centred on the face: x -0.5 is a quarter of the way across.
    cols = np.nonzero(left.any(axis=0))[0]
    assert abs((cols.min() + cols.max()) / 2 - 80) <= 1
    assert right.sum() > left.sum()  # the face the head follows, drawn thicker


def test_mirrored_labels_read_the_right_way_round() -> None:
    """Drawing mirrored at x is drawing unmirrored at -x on a symmetric frame — the label included,
    which a label flipped with the picture would not be (`#12` reads differently backwards)."""
    frame = np.zeros(
        (241, 321, 3), dtype=np.uint8
    )  # odd sizes: x and -x land on exact pixels
    mirrored = draw_faces(frame, [(0.5, 0.25)], labels=["#12"], mirror=True)
    direct = draw_faces(frame, [(-0.5, 0.25)], labels=["#12"])
    assert np.array_equal(mirrored, direct)
    flipped_label = draw_faces(frame, [(0.5, 0.25)], labels=["#12"])[:, ::-1]
    assert not np.array_equal(mirrored, flipped_label)


def test_a_label_goes_below_the_square_at_the_top_of_the_frame() -> None:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    out = draw_faces(frame, [(0.0, -1.0)], labels=["#3"])
    green = _green(out)
    rows = np.nonzero(green.any(axis=1))[0]
    half = round(0.15 * 240 / 2)
    assert rows.max() > half + 2  # glyphs under the square's bottom edge


# --- The users table ---------------------------------------------------------------------------------


def _user(track_id: int, *, x: float = 0.0, in_view: bool = True) -> User:
    return User(
        track_id=track_id,
        seen=in_view,
        in_view=in_view,
        last_seen_ts=0.0,
        first_seen_ts=0.0,
        missing_s=0.0 if in_view else 1.5,
        face=Face(x=x, y=0.0, roll=None, size=0.2, track_id=track_id, distance_m=0.66),
    )


def _users(*users: User, active: bool = True) -> UsersReport:
    return UsersReport(users=users, arrived=(), left=(), ts=0.0, active=active)


def _tracking(**fields: Any) -> HeadTrackingReport:
    base: dict[str, Any] = {
        "active": True,
        "focus": False,
        "attention": "engaged",
        "track_id": None,
        "ts": 0.0,
    }
    return HeadTrackingReport(**{**base, **fields})


def test_the_head_line_says_whom_the_head_follows() -> None:
    assert head_line(_tracking(active=False)) == "**Head:** tracking off"
    assert head_line(_tracking(track_id=4)) == "**Head:** follows #4"
    assert (
        head_line(_tracking(track_id=4, pinned_track_id=4))
        == "**Head:** follows #4, pinned"
    )
    assert head_line(_tracking(attention="watching")) == "**Head:** watching"
    assert (
        head_line(_tracking(attention="watching", pinned_track_id=3))
        == "**Head:** watching, waiting for #3"
    )


def test_the_users_table_lists_who_is_present_and_marks_the_followed_one() -> None:
    text = users_markdown(
        _users(_user(3), _user(5, x=-0.8, in_view=False)), _tracking(track_id=3)
    )
    assert text.startswith("#### Users\n\n**Head:** follows #3\n\n| user |")
    assert "| ● #3 | yes | yes | 0.0 s | about 0.7 m away, in front of you |" in text
    # Out of view through a move of the robot's own: still present, under the same number.
    assert "| #5 | no | no | 1.5 s | about 0.7 m away, to your left |" in text
    assert users_markdown(_users(), _tracking(active=False)).endswith(
        "_Nobody present._"
    )
    assert users_markdown(_users(active=False), _tracking(active=False)).endswith(
        "_No detector is looking._"
    )


# --- Over the fake bridge -------------------------------------------------------------------------


async def _until(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_the_page_shows_the_robots_camera_its_faces_and_its_users() -> None:
    async def scenario() -> None:
        faces = ScriptedFaces()
        bridge = ReachyMiniBridge(seeing_config(faces))
        await bridge.start()
        try:
            assert await _until(
                lambda: robot_view(bridge, users=True).frame is not None
            )
            # The detector looks once it has seen its first frame: nobody, then.
            assert await _until(
                lambda: "_Nobody present._" in robot_view(bridge, users=True).status
            )
            view = robot_view(bridge, users=True)
            assert view.frame is not None and not _green(view.frame).any()

            faces.show((-0.6, 0.2))  # on the camera's left
            row = re.compile(r"\| #(\d+) \| yes \| yes \|")
            assert await _until(
                lambda: bool(row.search(robot_view(bridge, users=True).status))
            )
            view = robot_view(bridge, users=True)
            match = row.search(view.status)
            assert match is not None
            user = int(match.group(1))
            assert view.frame is not None
            green = _green(view.frame)
            width = green.shape[1]
            # Mirrored like a selfie: the camera's left shows on the right.
            assert green[:, width // 2 :].sum() > 0
            assert green[:, : width // 2].sum() == 0

            await bridge.start_head_tracking(track_id=user)
            assert await _until(
                lambda: (
                    f"follows #{user}, pinned" in robot_view(bridge, users=True).status
                )
            )
            assert f"| ● #{user} |" in robot_view(bridge, users=True).status
            # Without a detector configured the page shows no users.
            assert robot_view(bridge, users=False).status == ""
        finally:
            await bridge.stop()
        assert robot_view(bridge, users=True).frame is None  # the session is over

    asyncio.run(scenario())


def test_the_robot_shows_its_camera_and_its_users_once_it_came_up() -> None:
    """The real body's `view()`: nothing before `enter()`; then the camera, and the users table
    because a detector is configured — none without one."""

    async def scenario() -> None:
        seeing = ReachyEmbodiment(
            ReachyConfig(
                app=cast(AppConfig, None), reachy=seeing_config(ScriptedFaces())
            )
        )
        assert seeing.view() == EmbodimentView()
        await seeing.enter()
        try:
            assert await _until(lambda: seeing.view().frame is not None)
            assert seeing.view().status.startswith(
                "#### Users\n\n**Head:** tracking off"
            )
        finally:
            await seeing.exit()

        blind = ReachyEmbodiment(
            ReachyConfig(
                app=cast(AppConfig, None), reachy=ReachyMiniConfig(backend="fake")
            )
        )
        await blind.enter()
        try:
            assert await _until(lambda: blind.view().frame is not None)
            assert blind.view().status == ""
        finally:
            await blind.exit()

    asyncio.run(scenario())
