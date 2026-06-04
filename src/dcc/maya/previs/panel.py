"""Dockable previs panel. Acts as the controller for all interactive operations."""

from __future__ import annotations

import logging
from typing import cast

import maya.cmds as mc
from env_sg import DB_Config
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin  # type: ignore
from maya.OpenMayaUI import MQtUtil
from Qt.QtCompat import wrapInstance
from Qt.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.shotgrid import ShotGrid
from core.ui import MessageDialog

from dcc.maya.runtime import get_main_qt_window

from . import cameras, dialogs, monitor, playback, publish, state, style
from .file_manager import SEQUENCE_PROXY_RE
from .state import PrevisShot, PrevisState
from .timeline import PrevisTimeline

log = logging.getLogger(__name__)

PANEL_OBJECT_NAME = "previsPanel"
WORKSPACE_CONTROL_NAME = PANEL_OBJECT_NAME + "WorkspaceControl"

_panel_instance: PrevisPanel | None = None


class PrevisPanel(MayaQWidgetDockableMixin, QWidget):  # type: ignore[misc]
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent=parent)
        self.setObjectName(PANEL_OBJECT_NAME)
        self.setWindowTitle("Previs Sequencer")
        self.setStyleSheet(f"#{PANEL_OBJECT_NAME} {{ background: {style.PANEL_BG}; }}")

        self._state = state.read_state() or PrevisState.empty()
        self._sg_conn: ShotGrid | None = None

        self._build_ui()
        self.refresh()

    # ---------- UI scaffolding ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        self._timeline = PrevisTimeline(self, parent=self)
        root.addWidget(self._timeline, 1)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("topBar")
        bar.setFixedHeight(36)
        bar.setStyleSheet(style.TOP_BAR)

        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(10)

        dot = QFrame(bar)
        dot.setObjectName("topBarDot")
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(style.TOP_BAR_DOT)
        row.addWidget(dot)

        title = QLabel("Pre-vis Sequencer", bar)
        title.setObjectName("title")
        row.addWidget(title)

        self._info = QLabel("", bar)
        self._info.setObjectName("info")
        row.addWidget(self._info)

        row.addStretch(1)

        self._monitor_label = QLabel("", bar)
        self._monitor_label.setObjectName("info")
        row.addWidget(self._monitor_label)

        monitor_btn = QPushButton("set monitor", bar)
        monitor_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        monitor_btn.clicked.connect(self.pick_monitor)
        row.addWidget(monitor_btn)

        clean_check = QCheckBox("clean", bar)
        clean_check.setStyleSheet(style.TOOLBAR_CHECKBOX)
        clean_check.setChecked(monitor.clean_view())
        clean_check.setToolTip("Hide grid, cameras, and rig controls in the monitor")
        clean_check.toggled.connect(monitor.set_clean_view)
        row.addWidget(clean_check)

        add_btn = QPushButton("+ shot", bar)
        add_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        add_btn.clicked.connect(self.add_shot)
        row.addWidget(add_btn)

        publish_btn = QPushButton("publish all", bar)
        publish_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        publish_btn.clicked.connect(self.publish_all)
        row.addWidget(publish_btn)
        return bar

    def refresh(self) -> None:
        self._timeline.set_state(self._state)
        self._update_status_text()
        self._update_monitor_label()
        self._warn_orphans()

    def install_playhead_callback(self) -> None:
        """Resync the playhead on every scene time change, for the panel's lifetime.

        Parented to the workspaceControl so Maya kills the job when the panel closes —
        deliberately separate from playback.py's file-scoped monitor job.
        """
        mc.scriptJob(
            event=("timeChanged", self._timeline.sync_playhead),
            parent=WORKSPACE_CONTROL_NAME,
        )

    def _persist(self) -> None:
        state.write_state(self._state)
        self.refresh()

    def _update_status_text(self) -> None:
        if not self._is_previs_file():
            self._info.setText("no previs file open")
            return
        self._info.setText(self._compose_info_line())

    def _compose_info_line(self) -> str:
        """`<seq_code>  ·  N shots  ·  Mf  ·  X.Xs @ 24fps` — matches the brief's top bar."""
        seq = self._sequence_code() or "—"
        shots = self._state.shots
        if not shots:
            return f"{seq}  ·  no shots"
        total_frames = sum(s.primary_duration for s in shots)
        plural = "s" if len(shots) != 1 else ""
        return (
            f"{seq}  ·  {len(shots)} shot{plural}"
            f"  ·  {total_frames}f  ·  {total_frames / 24.0:.1f}s @ 24fps"
        )

    def _sequence_code(self) -> str | None:
        """Sequence-proxy code from `fileInfo` (e.g. `A_previs`), or None if absent/invalid."""
        raw = mc.fileInfo("code", query=True)
        code = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if not isinstance(code, str):
            return None
        return code if SEQUENCE_PROXY_RE.match(code) else None

    def pick_monitor(self) -> None:
        monitor.pick_monitor(on_bound=self._on_monitor_bound)

    def _on_monitor_bound(self, panel: str) -> None:
        self._update_monitor_label()
        playback.sync_monitor()  # show the current shot's camera immediately

    def _update_monitor_label(self) -> None:
        panel = monitor.get_monitor()
        self._monitor_label.setText(f"monitor: {panel}" if panel else "")

    def _warn_orphans(self) -> None:
        orphans = cameras.find_orphan_cameras(self._state)
        if orphans:
            dialogs.show_orphan_warning(self, orphans)

    # ---------- controller methods (called by child widgets) ----------

    def scrub_to_frame(self, frame: int) -> None:
        """Set scene time, then resync the playhead directly.

        The monitor follows via the `timeChanged` job; the playhead we move here
        instead, since that job is coalesced and lags an interactive scrub.
        """
        mc.currentTime(frame)
        self._timeline.sync_playhead()

    def jump_to_shot(self, shot_id: str) -> None:
        ranges = playback.compute_shot_ranges(self._state)
        shot_range = ranges.get(shot_id)
        if shot_range is not None:
            self.scrub_to_frame(shot_range[0])

    def add_shot(self) -> None:
        if not self._guard_previs_file():
            return
        ns = cameras.add_new_rig_reference()
        new_shot = PrevisShot(
            id=state.next_shot_id(),
            primary=ns,
            durations={ns: state.DEFAULT_SHOT_DURATION},
        )
        self._state.shots.append(new_shot)
        self._persist()

    def remove_shot(self, shot_id: str) -> None:
        self._state.shots = [s for s in self._state.shots if s.id != shot_id]
        self._persist()

    def add_alternate_new_rig(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        ns = cameras.add_new_rig_reference()
        shot.alternates.append(ns)
        shot.durations[ns] = state.DEFAULT_SHOT_DURATION
        self._persist()

    def add_alternate_duplicate_primary(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        new_ns = cameras.duplicate_primary(shot)
        if new_ns is None:
            return
        shot.alternates.append(new_ns)
        # Inherit the primary's duration — the duplicate IS the primary, at this moment.
        shot.durations[new_ns] = shot.primary_duration
        self._persist()

    def add_alternate_existing_camera(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        candidates = cameras.find_scene_cameras_outside_state(self._state)
        chosen = dialogs.pick_scene_camera(self, candidates)
        if not chosen:
            return
        shot.alternates.append(chosen)
        shot.durations[chosen] = state.DEFAULT_SHOT_DURATION
        self._persist()

    def promote_to_primary(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or shot.primary == namespace:
            return
        if namespace not in shot.alternates:
            return
        shot.alternates.remove(namespace)
        if shot.primary:
            shot.alternates.insert(0, shot.primary)
        shot.primary = namespace
        self._persist()

    def resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or new_length_frames <= 0:
            return
        if shot.duration_of(namespace) == new_length_frames:
            return
        shot.durations[namespace] = new_length_frames
        self._persist()

    def preview_resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        """Live column-width preview during a resize drag; no state mutation."""
        self._timeline.preview_column_width(shot_id, namespace, new_length_frames)

    def remove_camera(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        cameras.remove_camera_from_shot(shot, namespace)
        self._persist()

    def rename_camera(self, shot_id: str, namespace: str) -> None:
        new_name = dialogs.prompt_rename(self, namespace)
        if not new_name:
            return
        if not cameras.rename_camera(namespace, new_name):
            MessageDialog(
                self,
                f"Could not rename {namespace} to {new_name} (name in use or namespace missing).",
                "Rename Failed",
            ).exec_()
            return
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        if shot.primary == namespace:
            shot.primary = new_name
        shot.alternates = [new_name if a == namespace else a for a in shot.alternates]
        if namespace in shot.durations:
            shot.durations[new_name] = shot.durations.pop(namespace)
        self._persist()

    def assign_code(self, shot_id: str) -> None:
        code = self._sequence_code()
        if code is None:
            MessageDialog(
                self,
                "Could not determine the sequence letter for this file.",
                "Assign Code",
            ).exec_()
            return
        chosen = dialogs.pick_shotgrid_code_for_sequence(self, self._conn(), code[0])
        if not chosen:
            return
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        shot.shotgrid_code = chosen
        self._persist()

    def move_shot(self, shot_id: str, delta: int) -> None:
        shots = self._state.shots
        index = next((i for i, s in enumerate(shots) if s.id == shot_id), -1)
        if index < 0:
            return
        new_index = max(0, min(len(shots) - 1, index + delta))
        if new_index == index:
            return
        shots[index], shots[new_index] = shots[new_index], shots[index]
        self._persist()

    def publish_shot(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or not shot.shotgrid_code:
            MessageDialog(
                self,
                "Assign a ShotGrid code to this shot before publishing.",
                "Publish",
            ).exec_()
            return
        try:
            sg_shot = self._conn().get_shot(code=shot.shotgrid_code)
            publish.publish_shot(shot, sg_shot)
        except Exception as exc:
            log.exception("publish_shot failed")
            MessageDialog(self, str(exc), "Publish Failed").exec_()
            return
        self._persist()

    def publish_all(self) -> None:
        if not self._state.shots:
            return
        try:
            paths = publish.publish_sequence(self._state, self._conn())
        except Exception as exc:
            log.exception("publish_sequence failed")
            MessageDialog(self, str(exc), "Publish Failed").exec_()
            return
        QMessageBox.information(self, "Publish", f"Published {len(paths)} shot(s).")
        self._persist()

    # ---------- helpers ----------

    def _conn(self) -> ShotGrid:
        if self._sg_conn is None:
            self._sg_conn = ShotGrid.connect(DB_Config)
        return self._sg_conn

    def _is_previs_file(self) -> bool:
        return self._sequence_code() is not None

    def _guard_previs_file(self) -> bool:
        if self._is_previs_file():
            return True
        MessageDialog(
            self,
            "Open a previs file first (Open Previs in the shelf).",
            "No Previs File",
        ).exec_()
        return False


# ---------- workspaceControl boilerplate ----------


def _restore() -> None:
    """Called by Maya's workspaceControl restore mechanism."""
    global _panel_instance
    _panel_instance = PrevisPanel(parent=_maya_main_window())
    workspace_ptr = MQtUtil.findControl(WORKSPACE_CONTROL_NAME)
    widget_ptr = MQtUtil.findControl(_panel_instance.objectName())
    if workspace_ptr and widget_ptr:
        MQtUtil.addWidgetToMayaLayout(int(widget_ptr), int(workspace_ptr))
    _panel_instance.install_playhead_callback()


# Generated from __name__ so an IDE module-rename stays consistent without manual edits.
UI_SCRIPT = f"""
import {__name__}
{__name__}.{_restore.__name__}()
"""


def _maya_main_window() -> QMainWindow:
    ptr = MQtUtil.mainWindow()
    return cast(QMainWindow, wrapInstance(int(ptr), QMainWindow))


def _delete_workspace_control() -> None:
    if mc.workspaceControl(WORKSPACE_CONTROL_NAME, query=True, exists=True):
        mc.workspaceControl(WORKSPACE_CONTROL_NAME, edit=True, close=True)
        mc.deleteUI(WORKSPACE_CONTROL_NAME, control=True)


def launch() -> None:
    global _panel_instance
    _delete_workspace_control()
    _panel_instance = PrevisPanel(parent=get_main_qt_window())
    _panel_instance.show(  # type: ignore[attr-defined]
        dockable=True,
        uiScript=UI_SCRIPT,
        workspaceControlName=WORKSPACE_CONTROL_NAME,
    )
    _panel_instance.install_playhead_callback()


def close() -> None:
    global _panel_instance
    if _panel_instance is not None:
        _panel_instance.close()
