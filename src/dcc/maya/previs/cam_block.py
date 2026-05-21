"""Single camera block. Drag an alternate onto its column's primary to promote;
right-click for menu; drag the right-edge handle on primaries to resize the shot.

The resize handle is a dedicated child widget rather than edge-detection inside
the QFrame so mouse events land unambiguously.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from Qt import QtCore, QtGui
from Qt.QtCore import QMimeData
from Qt.QtGui import QCursor, QDrag
from Qt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import style

if TYPE_CHECKING:
    from .panel import PrevisPanel

# Qt.py shim doesn't expose enum members through stubs; alias once.
_LEFT_BUTTON = QtCore.Qt.LeftButton  # type: ignore[attr-defined]
_SIZE_HOR = QtCore.Qt.SizeHorCursor  # type: ignore[attr-defined]
_TRANSPARENT_FOR_MOUSE = QtCore.Qt.WA_TransparentForMouseEvents  # type: ignore[attr-defined]
_STYLED_BACKGROUND = QtCore.Qt.WA_StyledBackground  # type: ignore[attr-defined]
_MOVE_ACTION = QtCore.Qt.MoveAction  # type: ignore[attr-defined]

BLOCK_HEIGHT = 32
_HANDLE_WIDTH = 10
_MIME_TYPE = "application/x-previs-camera"  # payload: f"{shot_id}|{namespace}"


class _ResizeHandle(QFrame):
    """Right-edge grabber for primary blocks. Owns its own mouse events."""

    def __init__(self, block: CamBlock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = block
        self.setObjectName("resizeHandle")
        self.setFixedWidth(_HANDLE_WIDTH)
        self.setCursor(QCursor(_SIZE_HOR))
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        self._drag_active = False
        self._drag_start_global_x = 0

    def enterEvent(self, event: QtCore.QEvent) -> None:
        if not self._drag_active:
            self.setStyleSheet(style.RESIZE_HANDLE_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if not self._drag_active:
            self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != _LEFT_BUTTON:
            super().mousePressEvent(event)
            return
        self._drag_active = True
        self._drag_start_global_x = event.globalPos().x()
        self.setStyleSheet(style.RESIZE_HANDLE_ACTIVE)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseMoveEvent(event)
            return
        self._block.preview_resize(event.globalPos().x() - self._drag_start_global_x)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseReleaseEvent(event)
            return
        self._drag_active = False
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        self._block.commit_resize(event.globalPos().x() - self._drag_start_global_x)
        event.accept()


class CamBlock(QFrame):
    def __init__(
        self,
        *,
        namespace: str,
        is_primary: bool,
        length_frames: int,
        start_frame: int,
        shot_id: str,
        controller: PrevisPanel,
        height: int = BLOCK_HEIGHT,
        px_per_frame: int = 4,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("camBlock")
        self._namespace = namespace
        self._is_primary = is_primary
        self._length_frames = length_frames
        self._start_frame = start_frame
        # Taken verbatim from the timeline so drag math stays stable —
        # deriving px-per-frame from self.width() drifts because the block is
        # being live-resized during the drag.
        self._px_per_frame = max(1, px_per_frame)
        self._shot_id = shot_id
        self._controller = controller
        self._press_pos: QtCore.QPoint | None = None

        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Without WA_StyledBackground, Qt's native style ignores stylesheet
        # background-color and shows outline-only.
        self.setAttribute(_STYLED_BACKGROUND, True)
        if is_primary:
            self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
            self.setAcceptDrops(True)
        else:
            self.setStyleSheet(style.CAM_BLOCK_ALT)

        outer = QHBoxLayout(self)
        # Right margin 0 on primary: the resize handle owns that strip.
        # Left margin reduced on primary: the thick border-left already eats ~2px.
        outer.setContentsMargins(8 if is_primary else 10, 0, 0 if is_primary else 10, 0)
        outer.setSpacing(0)
        outer.addLayout(self._build_content(), 1)
        if is_primary:
            outer.addWidget(_ResizeHandle(self, self))

    def _build_content(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(2)

        # Decorative labels — `WA_TransparentForMouseEvents` lets presses fall
        # through to the QFrame so the block's own mousePressEvent fires.
        name = QLabel(self._namespace, self)
        name.setObjectName("name")
        name.setAttribute(_TRANSPARENT_FOR_MOUSE, True)
        col.addWidget(name)

        col.addLayout(self._build_frame_row())
        return col

    def _build_frame_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._start_label = _frame_label("startFrame", str(self._start_frame), self)
        self._length_label = _frame_label(
            "lengthBadge", f"{self._length_frames}f", self
        )
        self._end_label = _frame_label("endFrame", str(self._end_frame()), self)

        row.addWidget(self._start_label)
        row.addStretch(1)
        row.addWidget(self._length_label)
        row.addStretch(1)
        row.addWidget(self._end_label)
        return row

    def _end_frame(self) -> int:
        return self._start_frame + max(self._length_frames - 1, 0)

    # --- resize hooks (called by _ResizeHandle) -----------------------------

    def preview_resize(self, delta_px: int) -> None:
        new_length = self._compute_new_length(delta_px)
        self._length_label.setText(f"{new_length}f")
        self._end_label.setText(str(self._start_frame + max(new_length - 1, 0)))
        self._controller.preview_resize_camera(
            self._shot_id, self._namespace, new_length
        )

    def commit_resize(self, delta_px: int) -> None:
        new_length = self._compute_new_length(delta_px)
        if new_length != self._length_frames:
            self._controller.resize_camera(self._shot_id, self._namespace, new_length)

    def _compute_new_length(self, delta_px: int) -> int:
        delta_frames = int(round(delta_px / self._px_per_frame))
        return max(1, self._length_frames + delta_frames)

    # --- drag source (alts only) --------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == _LEFT_BUTTON and not self._is_primary:
            self._press_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            self._press_pos is not None
            and not self._is_primary
            and event.buttons() & _LEFT_BUTTON
        ):
            travel = (event.pos() - self._press_pos).manhattanLength()
            if travel >= QApplication.startDragDistance():
                self._start_drag()
                self._press_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_MIME_TYPE, f"{self._shot_id}|{self._namespace}".encode())
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QtCore.QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec_(_MOVE_ACTION)

    # --- drop target (primaries only) ---------------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        payload = self._payload_for_same_shot(event)
        if payload is None:
            event.ignore()
            return
        self.setStyleSheet(style.CAM_BLOCK_PRIMARY_DROP)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        if self._is_primary:
            self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        payload = self._payload_for_same_shot(event)
        if payload is None:
            event.ignore()
            return
        _shot_id, namespace = payload
        self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
        event.acceptProposedAction()
        self._controller.promote_to_primary(self._shot_id, namespace)

    def _payload_for_same_shot(
        self, event: QtGui.QDragEnterEvent | QtGui.QDropEvent
    ) -> tuple[str, str] | None:
        """Returns (shot_id, namespace) iff the drag is a valid same-shot promote."""
        if not self._is_primary:
            return None
        mime = event.mimeData()
        if not mime.hasFormat(_MIME_TYPE):
            return None
        raw = bytes(mime.data(_MIME_TYPE)).decode()
        if "|" not in raw:
            return None
        shot_id, namespace = raw.split("|", 1)
        if shot_id != self._shot_id:
            return None
        return shot_id, namespace

    # --- context menu -------------------------------------------------------

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = QMenu(self)
        if not self._is_primary:
            menu.addAction(
                "Promote to primary",
                lambda: self._controller.promote_to_primary(
                    self._shot_id, self._namespace
                ),
            )
        menu.addAction(
            "Rename…",
            lambda: self._controller.rename_camera(self._shot_id, self._namespace),
        )
        menu.addAction(
            "Remove from shot",
            lambda: self._controller.remove_camera(self._shot_id, self._namespace),
        )
        menu.exec_(event.globalPos())


def _frame_label(object_name: str, text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(object_name)
    label.setAttribute(_TRANSPARENT_FOR_MOUSE, True)
    return label
