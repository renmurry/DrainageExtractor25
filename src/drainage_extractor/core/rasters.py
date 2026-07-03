"""Raster helpers: hillshade, GeoTIFF writing, memory budgeting."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import psutil
import rasterio
from rasterio.transform import Affine

from drainage_extractor.core.errors import MemoryBudgetError

log = logging.getLogger(__name__)

#: Approximate metres per degree (for hillshading geographic DEMs only).
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON = 111_320.0


def hillshade(
    elevation: np.ma.MaskedArray,
    transform: Affine,
    *,
    geographic: bool = False,
    azimuth_deg: float = 315.0,
    altitude_deg: float = 45.0,
    z_factor: float = 1.0,
) -> np.ma.MaskedArray:
    """Horn hillshade of a (possibly masked) elevation array.

    Args:
        elevation: 2-D masked elevation array.
        transform: affine transform of the array's grid.
        geographic: True when the CRS is in degrees; cell sizes are then
            converted to approximate metres so relief still reads correctly.
        z_factor: additional vertical exaggeration.

    Returns:
        Masked uint8 array in [1, 255] (0 reserved for display nodata).
    """
    dx = abs(transform.a)
    dy = abs(transform.e)
    if geographic:
        # Rough latitude correction keeps shading plausible without a reprojection.
        lat = math.radians(abs(transform.f))
        dx *= M_PER_DEG_LON * max(0.05, math.cos(lat))
        dy *= M_PER_DEG_LAT

    z = elevation.astype("float64").filled(np.nan)
    gy, gx = np.gradient(z, dy, dx)
    gx *= z_factor
    gy *= z_factor

    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = math.radians(360.0 - azimuth_deg + 90.0)
    alt = math.radians(altitude_deg)

    shaded = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shaded = np.clip(np.nan_to_num(shaded, nan=0.0), 0.0, 1.0)
    out = (1.0 + shaded * 254.0).astype("uint8")

    mask = elevation.mask if np.ma.is_masked(elevation) else np.zeros(z.shape, bool)
    mask = mask | ~np.isfinite(z)
    return np.ma.MaskedArray(out, mask=mask)


def write_geotiff(
    path: str | Path,
    data: np.ndarray,
    transform: Affine,
    crs,
    nodata: float | int | None,
    dtype: str | None = None,
) -> Path:
    """Write a single-band tiled + deflate-compressed GeoTIFF."""
    path = Path(path)
    dtype = dtype or str(data.dtype)
    profile = {
        "driver": "GTiff",
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "compress": "deflate",
        "BIGTIFF": "IF_SAFER",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)
    return path


def read_masked(path: str | Path) -> tuple[np.ma.MaskedArray, Affine, object]:
    """Read band 1 fully into memory as a masked array (+ transform, crs)."""
    with rasterio.open(path) as ds:
        return ds.read(1, masked=True), ds.transform, ds.crs


def available_memory_bytes() -> int:
    """Currently available physical RAM."""
    return int(psutil.virtual_memory().available)


def check_memory_budget(required_bytes: int, what: str, hard_fraction: float = 0.85) -> list[str]:
    """Verify that ``required_bytes`` fits in RAM.

    Returns a list of warning strings when the run is possible but tight.

    Raises:
        MemoryBudgetError: when the estimate exceeds ``hard_fraction`` of the
            available memory — failing early beats a mid-run MemoryError.
    """
    avail = available_memory_bytes()
    if required_bytes > avail * hard_fraction:
        raise MemoryBudgetError(
            f"This DEM needs roughly {required_bytes / 1e9:.1f} GB of RAM for {what}, "
            f"but only {avail / 1e9:.1f} GB is available.",
            suggestion=(
                "Close other applications, tile the DEM into smaller pieces, or resample "
                "it to a coarser resolution and try again."
            ),
            details=f"required={required_bytes:,} B, available={avail:,} B",
        )
    if required_bytes > avail * 0.5:
        return [
            f"{what} will use about {required_bytes / 1e9:.1f} GB of the "
            f"{avail / 1e9:.1f} GB currently free — expect heavy memory pressure."
        ]
    return []
