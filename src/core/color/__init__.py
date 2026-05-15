"""Runtime entry point for the sandwich OCIO config.

Single source of truth for the active config version and the env vars
that point every DCC launcher at it. Version bumps happen in one place
(`CONFIG_VERSION` below) and all six launchers + viewport-default code
follow.

This module lives in `__init__.py` (rather than a submodule like
`env.py`) to avoid shadowing the top-level `env` module that
`core.util.paths` imports.

See `context/color.md` for the architectural overview.
"""

from __future__ import annotations

from pathlib import Path

from core.util.paths import get_production_path

CONFIG_VERSION = "sandwich-v01"

# Display and view selected as the pipeline default, matching the
# `active_displays` / `active_views` set by `build.py`.
DISPLAY = "sRGB - Display"
DEFAULT_VIEW = "ACES 1.0 - SDR Video"
# First item is the default; the latter two are kept for inspection only.
ACTIVE_VIEWS = "ACES 1.0 - SDR Video, Un-tone-mapped, Raw"


def config_dir() -> Path:
    """Production folder containing config.ocio and the RenderMan JSON."""
    return get_production_path() / "color_configuration" / CONFIG_VERSION


def config_path() -> Path:
    """The config.ocio artifact path."""
    return config_dir() / "config.ocio"


def ocio_env_vars(*, include_renderman: bool = False) -> dict[str, str]:
    """OCIO-related env vars every DCC launcher should merge into its env.

    `include_renderman=True` also sets `RMAN_COLOR_CONFIG_DIR`, which
    RenderMan-for-Maya and RenderMan-for-Houdini read to locate the
    `rman_color_config_<dirname>.json` file alongside the config.
    """
    base: dict[str, str] = {
        "OCIO": str(config_path()),
        "OCIO_ACTIVE_DISPLAYS": DISPLAY,
        "OCIO_ACTIVE_VIEWS": ACTIVE_VIEWS,
    }
    if include_renderman:
        base["RMAN_COLOR_CONFIG_DIR"] = str(config_dir())
    return base


__all__ = [
    "ACTIVE_VIEWS",
    "CONFIG_VERSION",
    "DEFAULT_VIEW",
    "DISPLAY",
    "config_dir",
    "config_path",
    "ocio_env_vars",
]
