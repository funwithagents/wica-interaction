# The page shows the body's camera and, on the robot, its users

**Status:** Done

Implements `specs/embodiment.md` (`view()`, "`view()` is display-ready"), `specs/app.md` ("What the user sees": the Camera panel), `specs/computer-embodiment.md` (`view()`: the webcam) and `specs/reachy-embodiment.md` ("The page view"): a Camera panel on the page showing what the body sees — the computer's webcam, the robot's camera with each detected face boxed and labelled `#<id>` — and, on the robot, whom the head follows and the users present.

## Scope

- `src/wica_interaction/embodiment.py` — `EmbodimentView`, `display_frame` (BGR → RGB, subsampled to `VIEW_MAX_WIDTH`), the protocol's `view()`
- `src/wica_interaction/computer/embodiment.py` — keeps the feed `camera()` built; `view()` its newest frame, mirrored
- `src/wica_interaction/reachy/view.py` — new: `robot_view`, `draw_faces` (squares, the followed one thicker, mirror, `#<id>` labels in a 3×5 bitmap font stamped after the flip), `head_line`, `users_markdown`
- `src/wica_interaction/reachy/embodiment.py` — `view()` over `robot_view`
- `src/wica_interaction/app_ui.py` — `tick_view`; the Camera panel (image + status) at the top of the right column, on a 0.2 s timer of its own
- `tests/fakes.py` — `FakeEmbodiment.view()`, scripted through `shown`
- `tests/test_app_ui.py`, `tests/computer/test_embodiment.py`, `tests/reachy/test_view.py` — the panel, the webcam's view, the robot's view
- `AGENTS.md` — the project map's `reachy/view.py` row; `docs/` — the Camera panel

## Steps

1. The protocol member and its value type; the shared `display_frame`.
2. The computer's view over its kept feed.
3. `reachy/view.py`, then the robot's `view()`.
4. The page: `tick_view`, the panel, its timer; the fake.
5. Tests, the map, the docs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`. A rendered overlay checked by eye: mirrored, labels upright, a label below its square at the top edge, the followed face thicker.
