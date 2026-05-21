"""Sequence-wide grid: a single QGridLayout shared by ruler, headers, primary and alt tracks.

All tracks use the same column stretches, so shots line up vertically across rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from Qt import QtCore, QtGui
from Qt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import dialogs, playback, style
from .cam_block import BLOCK_HEIGHT, CamBlock
from .ruler import RULER_HEIGHT, Ruler
from .shot_header import HEADER_HEIGHT, ShotHeader
from .state import PrevisShot, PrevisState, display_name

if TYPE_CHECKING:
    from .panel import PrevisPanel

# Qt.py shim doesn't expose enum members through stubs; alias once.
_ALIGN_CENTER = QtCore.Qt.AlignCenter  # type: ignore[attr-defined]
_CONTROL = QtCore.Qt.ControlModifier  # type: ignore[attr-defined]
_SHIFT = QtCore.Qt.ShiftModifier  # type: ignore[attr-defined]
_POINTING_HAND = QtCore.Qt.PointingHandCursor  # type: ignore[attr-defined]
_SCROLL_AS_NEEDED = QtCore.Qt.ScrollBarAsNeeded  # type: ignore[attr-defined]

TRACK_LABEL_WIDTH = 76
ROW_HEIGHT_DEFAULT = 44
ROW_HEIGHT_MIN = 32
ROW_HEIGHT_MAX = 96
PX_PER_FRAME_DEFAULT = 4
PX_PER_FRAME_MIN = 2
PX_PER_FRAME_MAX = 16
MIN_COLUMN_WIDTH = 90

_ROW_RULER = 0
_ROW_HEADERS = 1
_ROW_PRIMARY = 2  # alt rows follow: _ROW_PRIMARY + 1 + alt_index


class PrevisTimeline(QWidget):
    def __init__(
        self,
        controller: PrevisPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setStyleSheet(f"background: {style.PANEL_BG};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(_SCROLL_AS_NEEDED)
        self._scroll.setVerticalScrollBarPolicy(_SCROLL_AS_NEEDED)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {style.PANEL_BG}; border: 0; }}"
        )
        outer.addWidget(self._scroll)

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background: {style.PANEL_BG};")
        self._scroll.setWidget(self._inner)

        self._grid = QGridLayout(self._inner)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(0)
        self._grid.setColumnMinimumWidth(0, TRACK_LABEL_WIDTH)

        # Remember last build's extent so the next rebuild can clear its
        # min-widths and trailing stretch on shrinks.
        self._prior_shot_count = 0
        self._prior_deepest_row = _ROW_PRIMARY
        self._ruler: Ruler | None = None  # held so resize previews can reflow ticks

        # Zoom state (Ctrl+wheel = vertical, Shift+wheel = horizontal).
        self._row_height = ROW_HEIGHT_DEFAULT
        self._px_per_frame = PX_PER_FRAME_DEFAULT
        self._last_state: PrevisState | None = None

        self.setToolTip("Ctrl+Wheel: zoom vertically · Shift+Wheel: zoom horizontally")

    # ----- public ----------------------------------------------------------

    def set_state(self, state: PrevisState) -> None:
        self._last_state = state
        self._clear()
        self._reset_layout_overrides()
        if not state.shots:
            empty = QLabel("No shots yet.  Click  + shot  to create one.", self._inner)
            empty.setStyleSheet(
                f"color: {style.PANEL_TEXT_DIM}; padding: 40px; font-size: 12px;"
            )
            empty.setAlignment(_ALIGN_CENTER)
            self._grid.addWidget(empty, 0, 0, 1, 2)
            return

        shots = state.shots
        shot_lengths = [s.duration_frames for s in shots]
        first_frame = playback.FRAME_START
        last_frame = first_frame + sum(shot_lengths) - 1

        self._apply_column_widths(shot_lengths)
        self._add_ruler_row(first_frame, last_frame, num_shots=len(shots))
        self._add_header_row(shots)
        deepest_row = self._add_track_rows(shots, shot_lengths)
        self._apply_trailing_stretches(len(shots), deepest_row)

    # ----- private layout helpers -----------------------------------------

    def _reset_layout_overrides(self) -> None:
        """Undo min-widths and trailing stretches from the previous build."""
        for col in range(1, self._prior_shot_count + 2):
            self._grid.setColumnMinimumWidth(col, 0)
            self._grid.setColumnStretch(col, 0)
        self._grid.setRowStretch(self._prior_deepest_row + 1, 0)

    def _apply_column_widths(self, shot_lengths: list[int]) -> None:
        for index, length in enumerate(shot_lengths):
            width = max(MIN_COLUMN_WIDTH, length * self._px_per_frame)
            self._grid.setColumnMinimumWidth(index + 1, width)

    def _apply_trailing_stretches(self, num_shots: int, deepest_row: int) -> None:
        """Trailing col + row absorb slack; otherwise QGridLayout stretches content."""
        self._grid.setColumnStretch(num_shots + 1, 1)
        self._grid.setRowStretch(deepest_row + 1, 1)
        self._prior_shot_count = num_shots
        self._prior_deepest_row = deepest_row

    def _add_ruler_row(
        self, first_frame: int, last_frame: int, *, num_shots: int
    ) -> None:
        spacer = QFrame(self._inner)
        spacer.setFixedSize(TRACK_LABEL_WIDTH, RULER_HEIGHT)
        spacer.setStyleSheet(
            f"background: {style.PANEL_BG_DEEP}; "
            f"border-right: 1px solid {style.PANEL_BORDER};"
        )
        self._grid.addWidget(spacer, _ROW_RULER, 0)

        ruler = Ruler(self._inner)
        ruler.set_range(first_frame, last_frame)
        self._grid.addWidget(ruler, _ROW_RULER, 1, 1, num_shots)
        self._ruler = ruler

    def _add_header_row(self, shots: list[PrevisShot]) -> None:
        spacer = QFrame(self._inner)
        spacer.setFixedSize(TRACK_LABEL_WIDTH, HEADER_HEIGHT)
        spacer.setStyleSheet(
            f"background: {style.PANEL_BG_DEEP}; "
            f"border-right: 1px solid {style.PANEL_BORDER};"
        )
        self._grid.addWidget(spacer, _ROW_HEADERS, 0)
        for index, shot in enumerate(shots):
            label = shot.shotgrid_code or display_name(index)
            self._grid.addWidget(
                ShotHeader(
                    shot=shot,
                    display_name=label,
                    controller=self._controller,
                    parent=self._inner,
                ),
                _ROW_HEADERS,
                index + 1,
            )

    def _add_track_rows(self, shots: list[PrevisShot], shot_lengths: list[int]) -> int:
        """Build the primary row, alt rows, and per-shot add-alt cell. Returns the deepest row index used."""
        max_alts = max((len(s.alternates) for s in shots), default=0)

        # Primary row
        self._grid.addWidget(_track_label("Primary", is_primary=True), _ROW_PRIMARY, 0)
        self._grid.setRowMinimumHeight(_ROW_PRIMARY, self._row_height)
        for index, shot in enumerate(shots):
            self._grid.addLayout(
                self._cell_for_camera(
                    shot, shot.primary, is_primary=True, shot_length=shot_lengths[index]
                ),
                _ROW_PRIMARY,
                index + 1,
            )

        # Alt rows
        for alt_index in range(max_alts):
            grid_row = _ROW_PRIMARY + 1 + alt_index
            self._grid.addWidget(_track_label("", is_primary=False), grid_row, 0)
            self._grid.setRowMinimumHeight(grid_row, self._row_height)
            for index, shot in enumerate(shots):
                if alt_index < len(shot.alternates):
                    namespace = shot.alternates[alt_index]
                    self._grid.addLayout(
                        self._cell_for_camera(
                            shot,
                            namespace,
                            is_primary=False,
                            shot_length=shot_lengths[index],
                        ),
                        grid_row,
                        index + 1,
                    )

        # Add-alt row: one cell per shot, positioned right under that shot's last alt.
        for index, shot in enumerate(shots):
            grid_row = _ROW_PRIMARY + 1 + len(shot.alternates)
            self._grid.setRowMinimumHeight(grid_row, self._row_height)
            if not self._grid.itemAtPosition(grid_row, 0):
                self._grid.addWidget(_track_label("", is_primary=False), grid_row, 0)
            self._grid.addLayout(
                self._add_alt_cell(shot.id, enabled=bool(shot.primary)),
                grid_row,
                index + 1,
            )

        return max(_ROW_PRIMARY + 1 + len(s.alternates) for s in shots)

    def _cell_for_camera(
        self,
        shot: PrevisShot,
        namespace: str,
        *,
        is_primary: bool,
        shot_length: int,
    ) -> QHBoxLayout:
        cell = QHBoxLayout()
        cell.setContentsMargins(1, 4, 1, 4)
        cell.setSpacing(0)
        if not namespace:
            cell.addStretch(1)
            return cell
        cell.addWidget(
            CamBlock(
                namespace=namespace,
                is_primary=is_primary,
                length_frames=shot_length,
                shot_id=shot.id,
                controller=self._controller,
                height=self._block_height(),
                px_per_frame=self._px_per_frame,
                parent=self._inner,
            ),
            1,
        )
        return cell

    def _add_alt_cell(self, shot_id: str, *, enabled: bool) -> QHBoxLayout:
        cell = QHBoxLayout()
        cell.setContentsMargins(1, 4, 1, 4)
        cell.setSpacing(0)
        button = QPushButton("+  add alternate", self._inner)
        button.setObjectName("addAlt")
        button.setEnabled(enabled)
        button.setStyleSheet(style.ADD_ALT_CELL)
        button.setFixedHeight(self._block_height())
        button.setCursor(_POINTING_HAND)
        button.clicked.connect(lambda: self._open_add_alt(shot_id))
        cell.addWidget(button)
        return cell

    def _block_height(self) -> int:
        """Track-block height tracks row height with a small breathing margin."""
        return max(BLOCK_HEIGHT, self._row_height - 8)

    def _open_add_alt(self, shot_id: str) -> None:
        dialogs.show_add_alternate_menu(
            self,
            on_new_rig=lambda: self._controller.add_alternate_new_rig(shot_id),
            on_duplicate=lambda: self._controller.add_alternate_duplicate_primary(
                shot_id
            ),
            on_existing=lambda: self._controller.add_alternate_existing_camera(shot_id),
        )

    # ----- live preview (driven by CamBlock during drag) -------------------

    def preview_column_width(self, shot_id: str, new_length: int) -> None:
        """Update one column's min width and reflow the ruler so the drag feels live.

        Without the ruler update, the ruler stretches its existing tick layout
        to fit the new column width, which looks wrong until the rebuild fires.
        """
        if self._last_state is None:
            return
        index = next(
            (i for i, s in enumerate(self._last_state.shots) if s.id == shot_id), -1
        )
        if index < 0:
            return
        width = max(MIN_COLUMN_WIDTH, new_length * self._px_per_frame)
        self._grid.setColumnMinimumWidth(index + 1, width)
        if self._ruler is not None:
            total_frames = sum(
                new_length if i == index else shot.duration_frames
                for i, shot in enumerate(self._last_state.shots)
            )
            self._ruler.set_range(
                playback.FRAME_START, playback.FRAME_START + total_frames - 1
            )
            self._ruler.update()

    # ----- wheel zoom ------------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        mods = event.modifiers()
        ctrl = bool(mods & _CONTROL)
        shift = bool(mods & _SHIFT)
        if not (ctrl or shift):
            super().wheelEvent(event)
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        if ctrl:
            new_h = self._row_height + step * 6
            self._row_height = max(ROW_HEIGHT_MIN, min(ROW_HEIGHT_MAX, new_h))
        else:  # shift
            new_p = self._px_per_frame + step
            self._px_per_frame = max(PX_PER_FRAME_MIN, min(PX_PER_FRAME_MAX, new_p))
        if self._last_state is not None:
            self.set_state(self._last_state)
        event.accept()

    # ----- teardown --------------------------------------------------------

    def _clear(self) -> None:
        _drain(self._grid)


def _drain(layout: QLayout) -> None:
    """Recursively delete every widget under `layout`; nested QLayouts are GC'd."""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            _drain(sub)


def _track_label(text: str, *, is_primary: bool) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(TRACK_LABEL_WIDTH)
    label.setStyleSheet(
        style.TRACK_LABEL_PRIMARY if is_primary else style.TRACK_LABEL_ALT
    )
    return label
