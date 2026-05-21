"""Modal pickers, inline menus, and warning popups for the previs panel."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from Qt.QtGui import QCursor
from Qt.QtWidgets import QInputDialog, QMenu, QMessageBox, QWidget

from core.ui import FilteredListDialog
from core.shotgrid import ShotGrid


def pick_shotgrid_code_for_sequence(
    parent: QWidget,
    conn: ShotGrid,
    sequence_letter: str,
) -> str | None:
    """Prompt the user to pick a real shot code (e.g. `A_010`) for the given sequence letter."""
    pattern = re.compile(rf"^{re.escape(sequence_letter)}_\d+$")
    codes = sorted(
        s.code for s in conn.find_shots() if s.code and pattern.match(s.code)
    )
    if not codes:
        return None
    dialog = FilteredListDialog(
        parent,
        codes,
        title="Assign ShotGrid Code",
        list_label=f"Select the shot to pair with this previs shot (sequence {sequence_letter}):",
        accept_button_name="Assign",
    )
    if not dialog.exec_():
        return None
    return dialog.get_selected_item()


def pick_scene_camera(parent: QWidget, candidates: Sequence[str]) -> str | None:
    """Prompt the user to pick an existing camera namespace from `candidates`."""
    if not candidates:
        return None
    dialog = FilteredListDialog(
        parent,
        list(candidates),
        title="Pick Existing Camera",
        list_label="Select a camera namespace to add to this shot:",
        accept_button_name="Add",
    )
    if not dialog.exec_():
        return None
    return dialog.get_selected_item()


def prompt_rename(parent: QWidget, current: str) -> str | None:
    """Prompt for a new namespace name; return None on cancel or unchanged."""
    new_name, ok = QInputDialog.getText(
        parent, "Rename camera", "New namespace:", text=current
    )
    if not ok:
        return None
    new_name = new_name.strip()
    if not new_name or new_name == current:
        return None
    return new_name


def show_add_alternate_menu(
    anchor: QWidget,
    *,
    on_new_rig: Callable[[], None],
    on_duplicate: Callable[[], None],
    on_existing: Callable[[], None],
) -> None:
    menu = QMenu(anchor)
    menu.addAction("New rig reference", on_new_rig)
    menu.addAction("Duplicate from primary", on_duplicate)
    menu.addAction("Pick existing camera…", on_existing)
    menu.exec_(QCursor.pos())


def show_orphan_warning(parent: QWidget, orphans: Iterable[tuple[str, str]]) -> None:
    """Non-blocking warning listing every (shot_id, namespace) gone missing."""
    items = list(orphans)
    if not items:
        return
    lines = [f"  • {ns}  (shot {shot_id})" for shot_id, ns in items]
    QMessageBox.warning(
        parent,
        "Missing cameras",
        "These tracked cameras are missing from the scene:\n\n"
        + "\n".join(lines)
        + "\n\nThey were probably renamed or removed externally. "
        "Re-add them through the panel to fix tracking.",
    )
