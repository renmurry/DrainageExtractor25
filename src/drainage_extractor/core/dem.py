"""DEM loading, validation and reprojection.

Supported input formats: GeoTIFF (.tif/.tiff), ERDAS Imagine (.img) and
ESRI ASCII grid (.asc), at any resolution. All raster access goes through
rasterio — no GDAL command-line dependencies.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from drainage_extractor.core.crs_utils import centroid_lonlat, describe_crs, utm_crs_for
from drainage_extractor.core.errors import DEMValidationError, PipelineCancelled

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".tif", ".tiff", ".img", ".asc"})

#: Default nodata assigned when a float DEM has none defined.
DEFAULT_NODATA = -32768.0

ProgressCB = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class DEMInfo:
    """Validation report for a loaded DEM."""

    path: Path
    width: int
    height: int
    band_count: int
    dtype: str
    crs: CRS | None
    transform: rasterio.Affine
    res: tuple[float, float]
    bounds: tuple[float, float, float, float]
    nodata: float | None
    driver: str
    warnings: list[str] = field(default_factory=list)
    stats_min: float | None = None
    stats_max: float | None = None
    valid_fraction: float | None = None

    # ------------------------------------------------------------------ props
    @property
    def n_cells(self) -> int:
        return self.width * self.height

    @property
    def is_geographic(self) -> bool:
        return bool(self.crs is not None and self.crs.is_geographic)

    @property
    def has_crs(self) -> bool:
        return self.crs is not None

    @property
    def linear_units(self) -> str:
        if self.crs is None:
            return "unknown"
        if self.crs.is_geographic:
            return "degrees"
        try:
            return self.crs.axis_info[0].unit_name or "metre"
        except (AttributeError, IndexError):
            return "metre"

    @property
    def cell_area_m2(self) -> float:
        """Approximate cell area in m² (uses latitude-corrected degree size for geographic CRSs)."""
        rx, ry = abs(self.res[0]), abs(self.res[1])
        if self.is_geographic:
            lat = (self.bounds[1] + self.bounds[3]) / 2.0
            mx = rx * 111_320.0 * max(0.05, math.cos(math.radians(lat)))
            my = ry * 110_540.0
            return mx * my
        return rx * ry

    def estimated_memory_bytes(self, arrays: int = 6) -> int:
        """Rough peak RAM for in-memory processing (``arrays`` float64 copies)."""
        return self.n_cells * 8 * arrays

    # ---------------------------------------------------------------- summary
    def summary(self) -> str:
        """Human-readable validation report (info panel + log)."""
        rx, ry = abs(self.res[0]), abs(self.res[1])
        unit = self.linear_units
        size_mb = self.n_cells * np.dtype(self.dtype).itemsize / 1e6
        lines = [
            f"File: {self.path.name} ({self.driver})",
            f"Size: {self.width} × {self.height} cells ({self.n_cells:,} — {size_mb:,.1f} MB in memory)",
            f"Resolution: {rx:g} × {ry:g} {unit}",
            f"CRS: {describe_crs(self.crs)}",
            f"Nodata: {self.nodata if self.nodata is not None else 'not defined'}",
        ]
        if self.stats_min is not None and self.stats_max is not None:
            lines.append(f"Elevation range: {self.stats_min:,.1f} – {self.stats_max:,.1f}")
        if self.valid_fraction is not None:
            lines.append(f"Valid data: {self.valid_fraction * 100:,.1f}% of cells")
        for w in self.warnings:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)

    def suggested_utm(self) -> CRS | None:
        """UTM CRS at the DEM centroid, for reprojecting geographic DEMs."""
        if self.crs is None:
            return None
        lon, lat = centroid_lonlat(self.crs, self.bounds)
        return utm_crs_for(lon, lat)


def load_dem_info(path: str | Path, sample_stats: bool = True) -> DEMInfo:
    """Open and validate a DEM, returning a :class:`DEMInfo` report.

    Raises:
        DEMValidationError: when the file is missing, unreadable, or has no
            usable elevation band.
    """
    p = Path(path)
    if not p.exists():
        raise DEMValidationError(
            f"The file '{p.name}' does not exist.",
            suggestion="Check that the file was not moved or renamed.",
            details=str(p),
        )
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise DEMValidationError(
            f"'{p.suffix}' files are not supported.",
            suggestion="Load a GeoTIFF (.tif), ERDAS Imagine (.img) or ASCII grid (.asc) DEM.",
            details=str(p),
        )
    try:
        ds = rasterio.open(p)
    except RasterioIOError as exc:
        raise DEMValidationError(
            f"'{p.name}' could not be opened as a raster.",
            suggestion="The file may be corrupt, incomplete, or not really a DEM.",
            details=str(exc),
        ) from exc

    with ds:
        if ds.count < 1 or ds.width == 0 or ds.height == 0:
            raise DEMValidationError(
                f"'{p.name}' contains no raster data.",
                suggestion="Export the DEM again from its source.",
                details=f"bands={ds.count}, size={ds.width}x{ds.height}",
            )

        warnings: list[str] = []
        if ds.count > 1:
            warnings.append(f"File has {ds.count} bands; band 1 will be used as elevation.")
        if not np.issubdtype(np.dtype(ds.dtypes[0]), np.number):
            raise DEMValidationError(
                f"'{p.name}' band 1 is not numeric and cannot be elevation data.",
                details=f"dtype={ds.dtypes[0]}",
            )
        crs = CRS.from_user_input(ds.crs) if ds.crs else None
        if crs is None:
            warnings.append(
                "No coordinate reference system defined — lengths and areas will be in cell units."
            )
        elif crs.is_geographic:
            warnings.append(
                "CRS is geographic (degrees). Hydrology on unprojected data distorts flow; "
                "reprojecting to a projected CRS (e.g. UTM) is strongly recommended."
            )
        if abs(abs(ds.res[0]) - abs(ds.res[1])) > 1e-6 * max(abs(ds.res[0]), abs(ds.res[1])):
            warnings.append(f"Pixels are not square ({ds.res[0]:g} × {ds.res[1]:g}).")
        nodata = ds.nodata
        if nodata is None:
            warnings.append("No nodata value defined — all cells will be treated as valid.")

        info = DEMInfo(
            path=p,
            width=ds.width,
            height=ds.height,
            band_count=ds.count,
            dtype=str(ds.dtypes[0]),
            crs=crs,
            transform=ds.transform,
            res=(float(ds.res[0]), float(ds.res[1])),
            bounds=tuple(ds.bounds),  # type: ignore[arg-type]
            nodata=float(nodata) if nodata is not None else None,
            driver=ds.driver,
            warnings=warnings,
        )

        if sample_stats:
            arr, _ = read_decimated(ds, max_size=1024)
            if arr.count() == 0:
                raise DEMValidationError(
                    f"'{p.name}' contains only nodata cells.",
                    suggestion="Check the export extent and nodata value of the source DEM.",
                )
            info.stats_min = float(arr.min())
            info.stats_max = float(arr.max())
            info.valid_fraction = float(arr.count()) / float(arr.size)
            if info.stats_min == info.stats_max:
                info.warnings.append("The DEM is perfectly flat — no drainage can be derived.")

    log.info("Loaded DEM:\n%s", info.summary())
    return info


def read_decimated(
    ds: rasterio.DatasetReader, max_size: int = 2048
) -> tuple[np.ma.MaskedArray, rasterio.Affine]:
    """Read band 1 decimated so the longest side is <= ``max_size`` pixels.

    Returns the masked array and the transform matching the decimated grid.
    Used for previews and quick statistics; never for hydrology.
    """
    scale = max(ds.width, ds.height) / float(max_size)
    if scale <= 1.0:
        out_shape = (ds.height, ds.width)
    else:
        out_shape = (max(1, round(ds.height / scale)), max(1, round(ds.width / scale)))
    arr = ds.read(1, out_shape=out_shape, masked=True, resampling=Resampling.average)
    transform = ds.transform * ds.transform.scale(ds.width / arr.shape[1], ds.height / arr.shape[0])
    return arr, transform


def reproject_dem(
    src_path: str | Path,
    dst_path: str | Path,
    dst_crs: CRS,
    progress: ProgressCB | None = None,
    cancelled: CancelCheck | None = None,
) -> Path:
    """Reproject a DEM to ``dst_crs``, streaming block-by-block (memory-safe).

    Bilinear resampling; output is a tiled, deflate-compressed GeoTIFF with an
    explicit nodata value.

    Raises:
        PipelineCancelled: if ``cancelled()`` becomes True mid-way.
    """
    src_path, dst_path = Path(src_path), Path(dst_path)
    with rasterio.open(src_path) as src:
        nodata = src.nodata if src.nodata is not None else DEFAULT_NODATA
        with WarpedVRT(
            src,
            crs=dst_crs.to_wkt(),
            resampling=Resampling.bilinear,
            src_nodata=src.nodata,
            nodata=nodata,
        ) as vrt:
            profile = {
                "driver": "GTiff",
                "width": vrt.width,
                "height": vrt.height,
                "count": 1,
                "dtype": "float32",
                "crs": vrt.crs,
                "transform": vrt.transform,
                "nodata": float(nodata),
                "tiled": True,
                "compress": "deflate",
                "BIGTIFF": "IF_SAFER",
            }
            block = 512
            n_rows = math.ceil(vrt.height / block)
            with rasterio.open(dst_path, "w", **profile) as dst:
                for i in range(n_rows):
                    if cancelled is not None and cancelled():
                        raise PipelineCancelled()
                    h = min(block, vrt.height - i * block)
                    win = Window(0, i * block, vrt.width, h)
                    data = vrt.read(1, window=win).astype("float32")
                    dst.write(data, 1, window=win)
                    if progress is not None:
                        progress((i + 1) / n_rows, f"Reprojecting… row block {i + 1}/{n_rows}")
    log.info("Reprojected %s -> %s (%s)", src_path.name, dst_path.name, dst_crs.name)
    return dst_path


def ensure_hydrology_ready(
    src_path: str | Path, workdir: str | Path, cancelled: CancelCheck | None = None
) -> Path:
    """Normalise a DEM for the hydrology engines.

    WhiteboxTools reads GeoTIFFs most reliably, and the built-in engine wants a
    float32 array with a defined nodata. Non-GeoTIFF inputs (or those without
    nodata) are converted into ``workdir/dem_input.tif``; compliant GeoTIFFs
    pass through untouched.
    """
    src_path = Path(src_path)
    with rasterio.open(src_path) as src:
        is_ok = (
            src.driver == "GTiff"
            and src.nodata is not None
            and str(src.dtypes[0]) in ("float32", "float64", "int16", "int32", "uint16")
        )
        if is_ok:
            return src_path
        out = Path(workdir) / "dem_input.tif"
        nodata = src.nodata if src.nodata is not None else DEFAULT_NODATA
        profile = {
            "driver": "GTiff",
            "width": src.width,
            "height": src.height,
            "count": 1,
            "dtype": "float32",
            "crs": src.crs,
            "transform": src.transform,
            "nodata": float(nodata),
            "tiled": True,
            "compress": "deflate",
            "BIGTIFF": "IF_SAFER",
        }
        block = 1024
        with rasterio.open(out, "w", **profile) as dst:
            for i in range(0, src.height, block):
                if cancelled is not None and cancelled():
                    raise PipelineCancelled()
                h = min(block, src.height - i)
                win = Window(0, i, src.width, h)
                data = src.read(1, window=win, masked=True).astype("float32")
                dst.write(data.filled(float(nodata)), 1, window=win)
    log.info("Converted %s to hydrology-ready GeoTIFF %s", src_path.name, out.name)
    return out
