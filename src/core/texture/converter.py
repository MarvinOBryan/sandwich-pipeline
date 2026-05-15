from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from math import ceil, floor, sqrt
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import typing

    RT = typing.TypeVar("RT")  # return type

from env import Executables

from core import telemetry
from dcc.substance_painter.util.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from core.util import silent_startupinfo

log = logging.getLogger(__name__)

# Painter exports follow `<Asset>_<MapType>_<colorSpace>.<udim>.png`.
_COLORSPACE_SUFFIX_RE = re.compile(r"_([^_.]+)\.\d{4}\.png$")


def _parse_colorspace_suffix(img_path: str) -> str:
    """Extract the OCIO colorspace from a Painter filename suffix."""
    basename = os.path.basename(img_path)
    match = _COLORSPACE_SUFFIX_RE.search(basename)
    if match is None:
        log.warning(
            "Texture filename %s doesn't follow the `_<colorSpace>` "
            "suffix convention. Tagging as Raw; check the publisher.",
            basename,
        )
        return "Raw"
    return match.group(1)


def _process_qt_events() -> None:
    """Flush pending Qt events so progress dialogs can repaint.

    Safe to call when no QApplication exists (headless / batch mode).
    """
    try:
        from Qt import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def _nearest_pow2(n: int) -> int:
    return 1 << (n - 1).bit_length()


class TexConversionError(ChildProcessError):
    """Raised when one of the tex / preview-image conversion subprocesses fails."""

    error_code = "TEXTURE_CONVERSION_FAILED"


class TexConverter:
    tex_path: Path
    preview_path: Path
    imgs_by_tex_set: list[list[str]]
    asset_name: str | None
    geo_variant: str | None
    material_variant: str | None
    renderman_variant: str | None
    batch_size: int

    def __init__(
        self,
        tex_path: Path,
        preview_path: Path,
        imgs_by_tex_set: typing.Iterable[list[str]],
        *,
        asset_name: str | None = None,
        geo_variant: str | None = None,
        material_variant: str | None = None,
        renderman_variant: str | None = None,
        batch_size: int = 18,
        progress_callback: PublishProgressCallback | None = None,
    ) -> None:
        self.tex_path = tex_path
        self.preview_path = preview_path
        self.imgs_by_tex_set = [list(imgs) for imgs in imgs_by_tex_set]
        self.asset_name = asset_name
        self.geo_variant = geo_variant
        self.material_variant = material_variant
        self.renderman_variant = renderman_variant
        self.batch_size = max(1, int(batch_size))
        self.progress_callback = progress_callback

    def _source_count(self) -> int:
        return sum(len(imgs) for imgs in self.imgs_by_tex_set)

    def convert_all(self) -> tuple[list[Path], list[Path]]:
        payload: dict[str, object] = {
            "source_count": self._source_count(),
            "converted_tex_count": 0,
            "converted_preview_count": 0,
            "batch_size": self.batch_size,
        }
        if self.geo_variant:
            payload["geo_variant"] = str(self.geo_variant)
        if self.material_variant:
            payload["material_variant"] = str(self.material_variant)
        if self.renderman_variant:
            payload["renderman_variant"] = str(self.renderman_variant)

        # Tracked across both conversion steps; finally folds whatever
        # was reached into the event so a partial failure ("tex convert
        # succeeded, preview surface failed") shows up in the dashboard
        # rather than reporting zero.
        converted_tex: list[Path] = []
        converted_preview: list[Path] = []

        with telemetry.record(
            telemetry.EVENT_TEXTURE_CONVERT_TEX,
            payload=payload,
            asset=self.asset_name,
        ) as telemetry_event:
            try:
                converted_tex = self.convert_tex()
                converted_preview = self.convert_previewsurface()
            finally:
                telemetry_event.update(
                    converted_tex_count=len(converted_tex),
                    converted_preview_count=len(converted_preview),
                )

        return converted_tex, converted_preview

    def convert_tex(self) -> list[Path]:
        """Convert all .png textures in the most recent export to .tex"""

        assert self.tex_path is not None

        # Remove any corrupted tex files from a previous export
        for file in self.tex_path.iterdir():
            if file.name.endswith(".temp.tex"):
                file.unlink()

        @self._debug_out
        def tex_cmd(img: str, is_color: bool, colorspace: str) -> list[str]:
            # Color maps go through oiiotool — Substance exports are already
            # in sRGB-Texture (color) or Raw (data) per the per-channel
            # OCIO role. `--attrib oiio:ColorSpace` records that interpretation
            # into the TX metadata so RenderMan and any other OIIO consumer
            # reads the file as the right colorspace without relying on the
            # shader-side `inputs:sourceColorSpace` attribute.
            # fmt: off
            return [
                str(Executables.oiiotool),
                img,
                "--attrib", "oiio:ColorSpace", colorspace,
                *(
                    [
                        "-d", "uint8",
                        "--dither",
                    ] if is_color else []
                ),
                "--compression", "lzw" if is_color else "lossless",
                "--planarconfig", "separate",
                "-otex:fileformatname=tx:wrap=clamp:resize=1:prman_options=1",
                f"{str(self.tex_path / Path(img).stem)}.tex",
            ]
            # fmt: on

        cmdlines: list[list[str]] = []
        for imgs in self.imgs_by_tex_set:
            log.debug(imgs)
            for img in imgs:
                if img.endswith(".jpeg"):
                    continue
                log.debug(f"        {img}")
                cmdlines.append(
                    tex_cmd(
                        img,
                        is_color=("Color" in img or "Emissive" in img),
                        colorspace=_parse_colorspace_suffix(img),
                    )
                )

        total_tex = len(cmdlines)
        if total_tex <= 0:
            self._report_progress(
                PublishStage.CONVERTING_TEX,
                "No TEX conversions were required for this publish.",
                current=1,
                total=1,
            )
            return []

        self._report_progress(
            PublishStage.CONVERTING_TEX,
            f"Converting source textures to TEX ({total_tex} file(s)).",
            current=0,
            total=total_tex,
        )

        finished_imgs = self._wait_and_check_cmds(
            cmdlines,
            batch_size=self.batch_size,
            stage=PublishStage.CONVERTING_TEX,
            message="Converting source textures to TEX.",
        )

        if len(finished_imgs) != len(cmdlines):
            raise TexConversionError("Not all png textures were converted")

        return finished_imgs

    def convert_previewsurface(self) -> list[Path]:
        """Compile all .jpeg textures in the most recent export to UDIM-less tiles"""
        MAX_OUTPUT_SIZE = 4096
        DOWNSCALE_RATIO = 2
        assert self.preview_path is not None

        @self._debug_out
        def jpeg_cmd(root: Path, imgs: typing.Sequence[str]) -> list[str]:
            img_name = re.search(r"^(.*_)(.+)$", root.name)
            assert img_name is not None
            name_base, _color_space = img_name.group(1, 2)

            count = len(imgs)
            grid_height = int(floor(sqrt(count)))
            grid_base = int(grid_height + ceil(count / grid_height - grid_height))

            native_x, native_y = self._img_dims(imgs[0])
            total_pixels = native_x * native_y * count
            mosaic_side = _nearest_pow2(int(sqrt(total_pixels) / DOWNSCALE_RATIO))
            mosaic_side = min(mosaic_side, MAX_OUTPUT_SIZE)
            target_tile_size = mosaic_side / max(grid_base, grid_height)
            cell_size = _nearest_pow2(int(target_tile_size))

            # fmt: off
            return [
                str(Executables.oiiotool),
                *imgs,
                f"--mosaic:fit{cell_size}x{cell_size}", f"{grid_base}x{grid_height}",
                "--resize", f"{mosaic_side}x{mosaic_side}",
                "-o", f"{str(self.preview_path / name_base)}sRGB.jpeg",
            ]
            # fmt: on

        # construct list of grouped images
        img_list: dict[str, list[str]] = {}
        for imgs in self.imgs_by_tex_set:
            for img in imgs:
                if img.endswith(".jpeg"):
                    key_search = re.search(r"^(.*)\.\d{4}\.jpeg$", img)
                    if not key_search:  # no UDIMs
                        key_search = re.search(r"^(.*)\.jpeg$", img)
                        assert key_search is not None

                    key = key_search.group(1)
                    if key not in img_list:
                        img_list[key] = []
                    img_list[key].append(img)

        cmdlines = [
            jpeg_cmd(Path(root), sorted(imgs)) for root, imgs in img_list.items()
        ]

        total_preview = len(cmdlines)
        if total_preview <= 0:
            self._report_progress(
                PublishStage.CONVERTING_PREVIEW,
                "No preview textures were required for this publish.",
                current=1,
                total=1,
            )
            return []

        self._report_progress(
            PublishStage.CONVERTING_PREVIEW,
            f"Building preview textures ({total_preview} output file(s)).",
            current=0,
            total=total_preview,
        )

        finished_imgs = self._wait_and_check_cmds(
            cmdlines,
            batch_size=self.batch_size,
            stage=PublishStage.CONVERTING_PREVIEW,
            message="Building preview textures.",
        )

        if len(finished_imgs) != len(cmdlines):
            raise TexConversionError("Not all jpeg textures were converted")

        return finished_imgs

    @staticmethod
    def _img_dims(img: str) -> tuple[int, int]:
        img_info = subprocess.check_output(
            [
                str(Executables.oiiotool),
                "--info",
                img,
            ],
            startupinfo=silent_startupinfo(),
        ).decode("utf-8")
        img_dims = re.search(r"^.* : +(\d+) +x +(\d+), .*$", img_info)

        assert img_dims is not None
        matches = img_dims.group(1, 2)
        return (int(matches[0]), int(matches[1]))

    def _report_progress(
        self,
        stage: PublishStage,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            PublishProgressUpdate(
                stage=stage,
                message=message,
                current=current,
                total=total,
            )
        )

    def _wait_and_check_cmds(
        self,
        cmds: typing.Sequence[list[str]],
        batch_size: int = 18,
        skip_check: bool = False,
        stage: PublishStage | None = None,
        message: str | None = None,
    ) -> list[Path]:
        """Wait for list of processes to finish and print them to the debug log"""

        batched_cmds = (
            cmds[i : i + batch_size] for i in range(0, len(cmds), batch_size)
        )

        finished_imgs: list[Path] = []
        total_cmds = len(cmds)

        while batch := next(batched_cmds, None):
            start_time = time.time()

            procs = [
                subprocess.Popen(
                    cmd,
                    env=os.environ,
                    startupinfo=silent_startupinfo(),
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                for cmd in batch
            ]

            for p in procs:
                p.wait()
                if log.isEnabledFor(logging.DEBUG):
                    if p.stdout and (stdout := p.stdout.read().decode("utf-8")):
                        log.debug(stdout)
                    if p.stderr and (stderr := p.stderr.read().decode("utf-8")):
                        log.debug(stderr)

                _process_qt_events()

                if skip_check:
                    continue

                img = Path(cast(str, p.args[-1]))  # type: ignore

                # check file has been touched recently
                if start_time < img.stat().st_mtime:
                    log.debug(f"Successfully converted {img}")
                    finished_imgs.append(img)
                    if stage is not None and message is not None:
                        self._report_progress(
                            stage,
                            message,
                            current=len(finished_imgs),
                            total=total_cmds,
                        )

        return finished_imgs

    def _debug_out(self, func: typing.Callable[..., RT]) -> typing.Callable[..., RT]:
        """Decorator to debug print the output of the function"""

        def inner(self: TexConverter, *args, **kwargs) -> RT:
            ret = func(self, *args, **kwargs)
            log.debug(ret)
            return ret

        return inner
