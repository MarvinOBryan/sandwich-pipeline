"""Nuke menu.py hook — point Nuke at the pipeline OCIO config.

Nuke 16 needs both `Root.OCIO_config = custom` *and*
`Root.customOCIOConfigPath` set to the config file — `$OCIO` alone
flips Nuke into a state where it reverts to a previously-loaded
config with a noisy error. Set the path knob first, then flip the
mode, so the reload reads our config in one step.

See context/color.md for the architectural overview.
"""

import os

import nuke

from core.color import DEFAULT_VIEW, DISPLAY

_VIEWER_PROCESS = f"{DEFAULT_VIEW} ({DISPLAY})"
_OCIO_PATH = os.environ["OCIO"]

# Defaults for any newly-created Root / Viewer.
nuke.knobDefault("Root.customOCIOConfigPath", _OCIO_PATH)
nuke.knobDefault("Root.OCIO_config", "custom")
nuke.knobDefault("Viewer.viewerProcess", _VIEWER_PROCESS)

# Patch the Root that already exists when menu.py runs — path first,
# then mode flip, so Nuke reloads from our path in one go.
_root = nuke.root()
if _root["customOCIOConfigPath"].value() != _OCIO_PATH:
    _root["customOCIOConfigPath"].setValue(_OCIO_PATH)
if _root["OCIO_config"].value() != "custom":
    _root["OCIO_config"].setValue("custom")
