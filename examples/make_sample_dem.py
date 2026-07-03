"""Generate the synthetic sample DEM shipped in ``examples/sample_dem.tif``.

The terrain is deterministic (seeded): smoothed random hills on a southward
regional slope, with a nodata blob in the north-east corner so masking code
paths get exercised. Re-run this script to regenerate the file:

    python examples/make_sample_dem.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.ndimage import gaussian_filter

ROWS, COLS = 260, 300
CELL = 5.0          # metres
EPSG = 32610        # WGS 84 / UTM zone 10N
ORIGIN = (551_000.0, 5_231_000.0)
NODATA = -9999.0
SEED = 42


def build_elevation() -> np.ndarray:
    """Deterministic hills + regional slope, ~40–140 m of relief."""
    rng = np.random.default_rng(SEED)
    noise = gaussian_filter(rng.standard_normal((ROWS, COLS)), sigma=14)
    noise = (noise - noise.min()) / (noise.max() - noise.min())  # 0..1 rolling hills

    rows = np.arange(ROWS, dtype="float64")[:, None]
    regional = (ROWS - 1 - rows) * 0.18  # drains toward the south edge
    z = 40.0 + noise * 90.0 + regional

    # A couple of closed depressions so breaching/filling has real work to do.
    yy, xx = np.mgrid[0:ROWS, 0:COLS]
    for cy, cx, r, depth in ((70, 90, 12, 6.0), (150, 210, 9, 4.0)):
        d2 = (yy - cy) ** 2 + (xx - cx) ** 2
        z -= depth * np.exp(-d2 / (2.0 * (r / 2.0) ** 2))
    return z.astype("float32")


def write_sample(path: Path) -> Path:
    z = build_elevation()

    # Nodata blob in the NE corner (exercises mask handling everywhere).
    yy, xx = np.mgrid[0:ROWS, 0:COLS]
    blob = (yy - 18) ** 2 + (xx - (COLS - 22)) ** 2 < 28**2
    z_out = np.where(blob, NODATA, z).astype("float32")

    profile = {
        "driver": "GTiff",
        "width": COLS,
        "height": ROWS,
        "count": 1,
        "dtype": "float32",
        "crs": f"EPSG:{EPSG}",
        "transform": from_origin(ORIGIN[0], ORIGIN[1], CELL, CELL),
        "nodata": NODATA,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(z_out, 1)
    return path


if __name__ == "__main__":
    out = write_sample(Path(__file__).parent / "sample_dem.tif")
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
