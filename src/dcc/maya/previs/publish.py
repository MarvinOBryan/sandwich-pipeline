"""Per-shot and per-sequence publish for previs cameras."""

from __future__ import annotations

import logging
from pathlib import Path

import maya.cmds as mc
from env_sg import DB_Config

from core.shotgrid import Shot, ShotGrid, ShotGridError
from core.ui import MessageDialog
from core.util.paths import get_production_path
from core.util.time_samples import offset_layer

from dcc.maya.publish.usdchaser import ExportChaser, ExportChaserMode
from dcc.maya.runtime import get_main_qt_window
from dcc.maya.util.selection import maintain_selection

from . import cameras, state
from .state import PrevisShot, PrevisState, utcnow_iso

log = logging.getLogger(__name__)


def publish_shot(previs_shot: PrevisShot, sg_shot: Shot) -> Path:
    """Export the primary to `<shot>/cam/cam.usd`, retimed to start at the shot's `cut_in`."""
    if not previs_shot.primary:
        raise ValueError(f"Previs shot {previs_shot.id} has no primary camera.")
    if sg_shot.cut_in is None:
        raise ValueError(f"ShotGrid shot {sg_shot.code!r} is missing cut_in.")

    camera_shape = cameras.camera_shape_for_namespace(previs_shot.primary)
    if camera_shape is None:
        raise RuntimeError(
            f"Could not find a camera under namespace {previs_shot.primary}."
        )

    span = cameras.camera_animation_range(previs_shot.primary)
    if span is None:
        raise RuntimeError(
            f"Primary camera {previs_shot.primary} has no keyframes; cannot publish."
        )
    primary_start, primary_end = span

    publish_path = get_production_path() / sg_shot.shot_path / "cam" / "cam.usd"
    publish_path.parent.mkdir(parents=True, exist_ok=True)

    with maintain_selection():
        mc.select(camera_shape, replace=True)
        mc.mayaUSDExport(  # type: ignore
            file=str(publish_path),
            selection=True,
            stripNamespaces=True,
            chaser=[ExportChaser.ID],
            chaserArgs=[(ExportChaser.ID, "mode", ExportChaserMode.CAM)],
            frameRange=(primary_start, primary_end),
            frameStride=1.0,
        )

    # Shift exported time samples so cam.usd starts at the shot's canonical cut_in.
    offset_layer(publish_path, float(sg_shot.cut_in) - primary_start)

    previs_shot.published_primary = previs_shot.primary
    previs_shot.published_animation_hash = cameras.compute_animation_hash(
        previs_shot.primary
    )
    return publish_path


def publish_sequence(state: PrevisState, conn: ShotGrid) -> list[Path]:
    paths: list[Path] = []
    for shot in state.shots:
        if not shot.shotgrid_code:
            log.info("Skipping unpaired previs shot %s.", shot.id)
            continue
        sg_shot = conn.get_shot(code=shot.shotgrid_code)
        paths.append(publish_shot(shot, sg_shot))
    if paths:
        state.last_published_at = utcnow_iso()
    return paths


def publish_sequence_interactive() -> None:
    """Shelf-button entry: read state, connect to ShotGrid, publish, report.

    Wraps `publish_sequence` so the artist gets file-not-previs and ShotGrid-
    connection failures as readable dialogs instead of console tracebacks.
    """
    parent = get_main_qt_window()
    current_state = state.read_state()
    if current_state is None:
        MessageDialog(
            parent,
            "Not in a previs file. Open a previs file before publishing the sequence.",
            "Publish Sequence",
        ).exec_()
        return

    try:
        conn = ShotGrid.connect(DB_Config)
    except ShotGridError as exc:
        log.exception("Could not connect to ShotGrid for previs publish.")
        MessageDialog(
            parent,
            f"Could not connect to ShotGrid.\n\n{exc}",
            "Publish Sequence",
        ).exec_()
        return

    try:
        paths = publish_sequence(current_state, conn)
    except Exception as exc:
        log.exception("Previs sequence publish failed.")
        MessageDialog(
            parent,
            f"Publish failed.\n\n{exc}",
            "Publish Sequence",
        ).exec_()
        return

    # Persist the updated `last_published_at` + per-shot publish hashes.
    state.write_state(current_state)

    if not paths:
        MessageDialog(
            parent,
            "No shots were published. Assign ShotGrid codes to shots first.",
            "Publish Sequence",
        ).exec_()
        return

    lines = [f"Published {len(paths)} shot{'s' if len(paths) != 1 else ''}:", ""]
    lines.extend(str(p) for p in paths)
    MessageDialog(parent, "\n".join(lines), "Publish Sequence").exec_()
