"""Shot header strip: ID label + ShotGrid-code state dot + per-shot menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from Qt import QtCore, QtGui
from Qt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from . import _qt, cameras, style
from .state import PrevisShot

if TYPE_CHECKING:
    from .panel import PrevisPanel

HEADER_HEIGHT = 32

_STATE_EMPTY = "empty"
_STATE_MODIFIED = "modified"
_STATE_PUBLISHED = "published"


class ShotHeader(QFrame):
    def __init__(
        self,
        *,
        shot: PrevisShot,
        display_name: str,
        controller: PrevisPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shot = shot
        self._controller = controller
        self._display_name = display_name

        self.setFixedHeight(HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAutoFillBackground(True)
        self.setCursor(_qt.POINTING_HAND)  # clicking the header jumps the playhead here
        self.setStyleSheet(
            f"ShotHeader {{ background: {style.PANEL_BG_HEADER}; "
            f"border-right: 1px solid {style.PANEL_BORDER_SOFT}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(6)

        self._id_label = QLabel(display_name, self)
        self._id_label.setStyleSheet(
            f"color: {style.PANEL_TEXT}; font-size: 12px; "
            f"font-weight: 500; letter-spacing: 1px;"
        )
        layout.addWidget(self._id_label, 1)
        self._badge_dot, self._badge_label = self._build_badge_widgets()
        layout.addWidget(self._badge_dot)
        layout.addWidget(self._badge_label)
        self._menu_btn = self._menu_button()
        layout.addWidget(self._menu_btn)

        self.setToolTip(self._tooltip_text())

    # Both hints return width=1 so the column shrinks to its setColumnMinimumWidth.
    # See CamBlock.minimumSizeHint for the QGridLayout gotcha this avoids.
    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, HEADER_HEIGHT)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, HEADER_HEIGHT)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == _qt.LEFT_BUTTON:
            self._controller.jump_to_shot(self._shot.id)
        super().mousePressEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        w = self.width()
        full = w >= style.TIER_COMPACT
        compact = w >= style.TIER_NARROW
        self._id_label.setVisible(full)
        self._badge_label.setVisible(full)
        self._menu_btn.setVisible(compact)
        # Dot is the load-bearing state signal — always show it.
        self._badge_dot.setVisible(True)

    def _build_badge_widgets(self) -> tuple[QFrame, QLabel]:
        text, dot_qss, text_qss = self._badge_style()
        dot = QFrame(self)
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"QFrame {{ {dot_qss} }}")
        label = QLabel(text, self)
        label.setStyleSheet(text_qss)
        return dot, label

    def _tooltip_text(self) -> str:
        code = self._shot.shotgrid_code or "no code"
        return f"{self._display_name}\n{code}"

    def _badge_style(self) -> tuple[str, str, str]:
        """`(text, dot_qss, text_qss)` for the current shot state."""
        state = self._compute_state()
        if state == _STATE_EMPTY:
            color = style.CODE_EMPTY
            return (
                "no code",
                f"background: transparent; border: 1px dashed {color}; border-radius: 4px;",
                f"color: {color}; font-size: 11px; font-style: italic;",
            )
        color = (
            style.CODE_MODIFIED if state == _STATE_MODIFIED else style.CODE_PUBLISHED
        )
        return (
            self._shot.shotgrid_code or "",
            f"background: {color}; border-radius: 4px;",
            f"color: {color}; font-size: 11px;",
        )

    def _compute_state(self) -> str:
        if not self._shot.shotgrid_code:
            return _STATE_EMPTY
        if not self._shot.primary or not self._shot.cam_animation_hash:
            return _STATE_MODIFIED
        live = cameras.compute_animation_hash(self._shot.primary)
        return (
            _STATE_PUBLISHED
            if live == self._shot.cam_animation_hash
            else _STATE_MODIFIED
        )

    def _menu_button(self) -> QPushButton:
        btn = QPushButton("⋮", self)
        btn.setFixedWidth(18)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {style.PANEL_TEXT_DIM}; "
            f"border: 0; font-size: 14px; }} "
            f"QPushButton:hover {{ color: {style.PANEL_TEXT}; }}"
        )
        btn.clicked.connect(self._open_menu)
        return btn

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(
            "Assign code…", lambda: self._controller.assign_code(self._shot.id)
        )
        menu.addSeparator()
        menu.addAction(
            "Move left", lambda: self._controller.move_shot(self._shot.id, -1)
        )
        menu.addAction(
            "Move right", lambda: self._controller.move_shot(self._shot.id, 1)
        )
        menu.addSeparator()
        menu.addAction(
            "Publish this shot", lambda: self._controller.publish_shot(self._shot.id)
        )
        menu.addSeparator()
        menu.addAction(
            "Delete shot", lambda: self._controller.remove_shot(self._shot.id)
        )
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))
