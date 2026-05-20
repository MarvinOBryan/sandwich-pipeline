"""Per-shot and per-sequence publish for previs cameras."""

from __future__ import annotations

import logging
from pathlib import Path

import maya.cmds as mc

from core.shotgrid import Shot, ShotGrid
from core.util.paths import get_production_path
from core.util.time_samples import offset_layer

from dcc.maya.publish.usdchaser import ExportChaser, ExportChaserMode
from dcc.maya.util.selection import maintain_selection

from . import cameras
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
