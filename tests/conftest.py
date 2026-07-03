"""Shared fixtures: sample DEM path, tiny synthetic DEMs, a cached pipeline run."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from drainage_extractor.core.pipeline import PipelineParams, run_pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEM = REPO_ROOT / "examples" / "sample_dem.tif"


@pytest.fixture(scope="session")
def sample_dem() -> Path:
    """The committed synthetic sample DEM."""
    assert SAMPLE_DEM.exists(), "examples/sample_dem.tif missing — run examples/make_sample_dem.py"
    return SAMPLE_DEM


def write_dem(
    path: Path,
    z: np.ndarray,
    *,
    crs: str = "EPSG:32610",
    cell: float = 10.0,
    nodata: float = -9999.0,
    origin: tuple[float, float] = (500_000.0, 5_000_000.0),
) -> Path:
    """Write a float32 GeoTIFF DEM for tests."""
    profile = {
        "driver": "GTiff",
        "width": z.shape[1],
        "height": z.shape[0],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": from_origin(origin[0], origin[1], cell, cell),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(z.astype("float32"), 1)
    return path


@pytest.fixture()
def tilted_plane(tmp_path: Path) -> Path:
    """A 30×20 plane dipping south — every interior cell flows due south."""
    rows, cols = 30, 20
    z = np.repeat(np.arange(rows, 0, -1, dtype="float32")[:, None], cols, axis=1) * 2.0
    return write_dem(tmp_path / "plane.tif", z)


@pytest.fixture()
def bowl_dem(tmp_path: Path) -> Path:
    """A tilted plane with a closed 3-cell-deep depression in the middle."""
    rows, cols = 30, 20
    z = np.repeat(np.arange(rows, 0, -1, dtype="float32")[:, None], cols, axis=1) * 2.0
    z[12:15, 8:11] -= 12.0
    return write_dem(tmp_path / "bowl.tif", z)


@pytest.fixture()
def geographic_dem(tmp_path: Path) -> Path:
    """A small DEM in EPSG:4326 (degrees) near Portland, OR."""
    rows, cols = 24, 30
    rng = np.random.default_rng(7)
    z = 100.0 + np.cumsum(rng.standard_normal((rows, cols)), axis=0).astype("float32")
    return write_dem(
        tmp_path / "geo.tif", z, crs="EPSG:4326", cell=0.0005, origin=(-122.7, 45.6)
    )


@pytest.fixture(scope="session")
def pipeline_result(sample_dem: Path, tmp_path_factory: pytest.TempPathFactory):
    """One full built-in-engine pipeline run on the sample DEM, shared by tests."""
    wd = tmp_path_factory.mktemp("pipe")
    return run_pipeline(
        sample_dem,
        PipelineParams(preprocess="breach", engine="builtin", min_stream_length_m=25.0),
        workdir=wd,
    )
