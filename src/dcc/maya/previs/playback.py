"""Install/remove the `timeChanged` scriptJob that swaps cameras at shot boundaries."""

from __future__ import annotations

import logging
from typing import cast

import maya.cmds as mc

from . import cameras
from .state import PrevisState, read_state

log = logging.getLogger(__name__)

FRAME_START = 1001
DEFAULT_SHOT_LENGTH = 24  # frames; placeholder length for shots with no keys yet.

_SCRIPT_JOB_ID: int | None = None


def install_camera_callback() -> None:
    """(Re)install the `timeChanged` callback. Safe to call multiple times."""
    global _SCRIPT_JOB_ID
    remove_camera_callback()
    _SCRIPT_JOB_ID = cast(int, mc.scriptJob(event=("timeChanged", _on_time_changed)))


def remove_camera_callback() -> None:
    global _SCRIPT_JOB_ID
    if _SCRIPT_JOB_ID is not None and mc.scriptJob(exists=_SCRIPT_JOB_ID):
        mc.scriptJob(kill=_SCRIPT_JOB_ID, force=True)
    _SCRIPT_JOB_ID = None


def compute_shot_ranges(state: PrevisState) -> dict[str, tuple[int, int]]:
    """For each shot, derive `(start, end)` from its primary's keyframe span.

    Shots stack in order starting at `FRAME_START`; an unkeyed primary falls
    back to `DEFAULT_SHOT_LENGTH` so the timeline still places it somewhere.
    """
    ranges: dict[str, tuple[int, int]] = {}
    cursor = FRAME_START
    for shot in state.shots:
        length = _primary_length(shot.primary)
        end = cursor + max(length - 1, 0)
        ranges[shot.id] = (cursor, end)
        cursor = end + 1
    return ranges


def resolve_camera_for_frame(
    state: PrevisState,
    ranges: dict[str, tuple[int, int]],
    frame: int,
) -> str | None:
    for shot in state.shots:
        start, end = ranges.get(shot.id, (0, -1))
        if start <= frame <= end:
            return shot.primary or None
    return None


def _primary_length(namespace: str) -> int:
    if not namespace or not mc.namespace(exists=f":{namespace}"):
        return DEFAULT_SHOT_LENGTH
    span = cameras.camera_animation_range(namespace)
    if span is None:
        return DEFAULT_SHOT_LENGTH
    start, end = span
    return int(end - start) + 1


def _on_time_changed() -> None:
    state = read_state()
    if state is None or not state.shots:
        return
    ranges = compute_shot_ranges(state)
    frame = int(mc.currentTime(query=True))
    ns = resolve_camera_for_frame(state, ranges, frame)
    if not ns:
        return
    shape = cameras.camera_shape_for_namespace(ns)
    if not shape:
        return
    current = mc.lookThru(query=True) or ""
    if current != shape:
        mc.lookThru(shape)
