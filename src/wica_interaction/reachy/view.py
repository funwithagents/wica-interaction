"""What the page shows of the Reachy Mini: its camera with the detected faces, and its users.

The robot's ``view()`` (specs/reachy-embodiment.md, "The page view"; specs/embodiment.md, "``view()``
is display-ready"). Everything is read from the bridge's published reports — ``camera``,
``faces``, ``users``, ``head_tracking`` — so a tick is a few attribute reads and some numpy, no
round trip to the robot.

- The frame: a green square on each detected face, the one the head follows drawn thicker, the
  picture mirrored like a selfie, and each face's ``#<track_id>`` stamped after the flip so it reads
  the right way round.
- The status: whom the head follows, then one row per user present — who is *present*, which
  outlasts who is in the picture: through an emotion that turns the head away, a user stays listed,
  out of view, under the same number.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
from reachy_mini_bridge import HeadTrackingReport, ReachyMiniBridge, UsersReport

from wica_interaction.embodiment import EmbodimentView, display_frame
from wica_interaction.reachy.users import place, rounded, side_of

# A face's square: its side as a share of the frame's height, and its colour (RGB).
FACE_MARKER_SIZE = 0.15
FACE_MARKER_RGB = (0, 255, 0)
# The label under its glyphs, and the frame height per pixel of glyph (the font's scale).
FACE_LABEL_PLATE_RGB = (0, 0, 0)
FACE_LABEL_SCALE_DIV = 80

# A 3×5 bitmap font: what a label needs (`#` and the digits), no font file, no OpenCV.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "#": ("#.#", "###", "#.#", "###", "#.#"),
}


def robot_view(bridge: ReachyMiniBridge, *, users: bool) -> EmbodimentView:
    """The page's view of the robot: the camera's newest frame with the faces of the newest face
    report, mirrored; and, when the robot sees people (*users*: a detector configured), the head line
    and the users table. Nothing to show before the first frame or after the session."""
    tracking = bridge.head_tracking.value
    status = users_markdown(bridge.users.value, tracking) if users else ""
    camera = bridge.camera.latest()
    if camera is None:
        return EmbodimentView(status=status)
    frame = display_frame(camera.image)
    faces = bridge.faces.value
    shown = faces.faces if faces.active else ()
    followed = tracking.track_id if tracking.active else None
    target = next((i for i, f in enumerate(shown) if f.track_id == followed), None)
    image = draw_faces(
        frame,
        [(f.x, f.y) for f in shown],
        target,
        labels=[f"#{f.track_id}" for f in shown],
        mirror=True,
    )
    return EmbodimentView(frame=image, status=status)


def draw_faces(
    frame: npt.NDArray[np.uint8],
    positions: Sequence[tuple[float, float]],
    target: int | None = None,
    *,
    labels: Sequence[str] = (),
    mirror: bool = False,
) -> npt.NDArray[np.uint8]:
    """A copy of the RGB *frame* with a square outline on each face.

    *positions* are normalised image coordinates (``[-1, 1]``, x right, y down), as ``bridge.faces``
    reports them; the face at index *target* — the one the head follows — is drawn thicker. With
    *mirror* the copy is flipped left to right, and *labels* (per face, ``#`` and digits) are
    stamped above each square after the flip, so they read the right way round."""
    out = frame.copy()
    height, width = out.shape[:2]
    half = max(2, round(FACE_MARKER_SIZE * height / 2))
    centres: list[tuple[int, int]] = []
    for i, (x, y) in enumerate(positions):
        cx = round((x + 1.0) * 0.5 * (width - 1))
        cy = round((y + 1.0) * 0.5 * (height - 1))
        centres.append((cx, cy))
        thick = max(1, height // (120 if i == target else 240))
        x0, x1 = max(cx - half, 0), min(cx + half, width - 1)
        y0, y1 = max(cy - half, 0), min(cy + half, height - 1)
        if x0 > x1 or y0 > y1:
            continue  # off the frame
        ys, xs = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
        edge = np.maximum(np.abs(xs - cx), np.abs(ys - cy))
        mask = (edge <= half) & (edge > half - thick)
        out[y0 : y1 + 1, x0 : x1 + 1][mask] = FACE_MARKER_RGB
    if mirror:
        out = np.ascontiguousarray(out[:, ::-1])
    for i, (cx, cy) in enumerate(centres):
        if i < len(labels) and labels[i]:
            _stamp_label(out, labels[i], width - 1 - cx if mirror else cx, cy, half)
    return out


def _stamp_label(
    out: npt.NDArray[np.uint8], text: str, cx: int, cy: int, half: int
) -> None:
    """Stamp *text* centred on *cx*, above the square of half-side *half* at *cy* (below it when
    there is no room above), on a plate; clipped to the frame."""
    height, width = out.shape[:2]
    scale = max(1, height // FACE_LABEL_SCALE_DIV)
    glyphs = [_GLYPHS[c] for c in text if c in _GLYPHS]
    if not glyphs:
        return
    # The label as a boolean bitmap: 3-pixel glyphs, one blank column between them.
    rows = [".".join(glyph[r] for glyph in glyphs) for r in range(5)]
    bitmap = np.array([[c == "#" for c in row] for row in rows], dtype=bool)
    bitmap = bitmap.repeat(scale, axis=0).repeat(scale, axis=1)
    pad = scale
    plate_h, plate_w = bitmap.shape[0] + 2 * pad, bitmap.shape[1] + 2 * pad
    top = cy - half - 1 - plate_h
    if top < 0:
        top = cy + half + 2  # no room above: below the square
    left = cx - plate_w // 2
    # The plate's rectangle clipped to the frame, and the bitmap's matching part.
    y0, y1 = max(top, 0), min(top + plate_h, height)
    x0, x1 = max(left, 0), min(left + plate_w, width)
    if y0 >= y1 or x0 >= x1:
        return
    out[y0:y1, x0:x1] = FACE_LABEL_PLATE_RGB
    by0, bx0 = top + pad, left + pad
    gy0, gy1 = max(by0, 0), min(by0 + bitmap.shape[0], height)
    gx0, gx1 = max(bx0, 0), min(bx0 + bitmap.shape[1], width)
    if gy0 >= gy1 or gx0 >= gx1:
        return
    mask = bitmap[gy0 - by0 : gy1 - by0, gx0 - bx0 : gx1 - bx0]
    out[gy0:gy1, gx0:gx1][mask] = FACE_MARKER_RGB


def head_line(tracking: HeadTrackingReport) -> str:
    """Whom the head follows: ``follows #4`` (pinned or not), ``watching`` (waiting for a pinned
    user it does not follow), or ``tracking off``."""
    if not tracking.active:
        return "**Head:** tracking off"
    pinned = tracking.pinned_track_id
    if tracking.track_id is not None:
        mark = ", pinned" if pinned == tracking.track_id else ""
        return f"**Head:** follows #{tracking.track_id}{mark}"
    waiting = f", waiting for #{pinned}" if pinned is not None else ""
    return f"**Head:** watching{waiting}"


def users_markdown(users: UsersReport, tracking: HeadTrackingReport) -> str:
    """Under a "Users" subheading, the head line, then one row per user present: ``●`` on the one the head follows, whether
    they were seen on the last observation and the camera frames where they are, how long they have
    been missing in view, and where they are — the side and distance as the agent is told them (the
    camera's own left and right, not the mirrored picture's)."""
    head = f"#### Users\n\n{head_line(tracking)}"
    if not users.active:
        return f"{head}\n\n_No detector is looking._"
    if not users.users:
        return f"{head}\n\n_Nobody present._"
    followed = tracking.track_id if tracking.active else None
    rows = [
        "| user | seen | in view | missing | where |",
        "|---|---|---|---|---|",
    ]
    for user in users.users:
        mark = "● " if user.track_id == followed else ""
        where = place(side_of(user.face), rounded(user.face.distance_m))
        rows.append(
            f"| {mark}#{user.track_id} | {_yes(user.seen)} | {_yes(user.in_view)} | "
            f"{user.missing_s:.1f} s | {where} |"
        )
    return f"{head}\n\n" + "\n".join(rows)


def _yes(value: bool) -> str:
    return "yes" if value else "no"
