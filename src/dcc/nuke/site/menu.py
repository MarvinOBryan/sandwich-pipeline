"""Nuke menu.py hook — viewer-process default for the pipeline OCIO config.

`nuke.knobDefault` for `Root.viewerProcess` must run in menu.py (not
init.py) per Foundry's documented evaluation order — viewer processes
aren't registered until the OCIO config has been consulted, which
happens between init.py and menu.py.

See context/color.md for the architectural overview.
"""

import nuke

from core.color import DEFAULT_VIEW, DISPLAY

nuke.knobDefault("Root.viewerProcess", f"{DEFAULT_VIEW} ({DISPLAY})")
