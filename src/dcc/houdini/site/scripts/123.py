"""Force the pipeline's OCIO display+view on every Houdini startup.

Houdini persists the last-used viewport view in user prefs, which
otherwise overrides our active_views ordering and lands artists on
Un-tone-mapped instead of the ACES Output Transform.
"""

from __future__ import annotations

import hdefereval
import hou

from core.color import DEFAULT_VIEW, DISPLAY


def _apply_ocio_defaults() -> None:
    for tab in hou.ui.paneTabs():
        if isinstance(tab, hou.SceneViewer):
            tab.setUsingOCIO(True)
            tab.setOCIODisplayView(DISPLAY, DEFAULT_VIEW)


hdefereval.executeDeferred(_apply_ocio_defaults)
