"""DEM loading, validation and reprojection."""

from __future__ import annotations

from pathlib import Path

import pytest
import rasterio

from drainage_extractor.core.dem import load_dem_info, reproject_dem
from drainage_extractor.core.errors import DEMValidationError


def test_sample_dem_info(sample_dem: Path) -> None:
    info = load_dem_info(sample_dem)
    assert info.width == 300 and info.height == 260
    assert info.crs is not None and info.crs.to_epsg() == 32610
    assert not info.is_geographic
    assert info.nodata == -9999.0
    assert abs(info.res[0]) == 5.0
    assert info.stats_min is not None and info.stats_max is not None
    assert info.stats_max > info.stats_min
    assert 0.9 < (info.valid_fraction or 0) < 1.0  # the nodata blob
    assert "sample_dem.tif" in info.summary()


def test_missing_file() -> None:
    with pytest.raises(DEMValidationError, match="does not exist"):
        load_dem_info("no/such/file.tif")


def test_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "dem.xyz"
    bad.write_text("nope")
    with pytest.raises(DEMValidationError, match="not supported"):
        load_dem_info(bad)


def test_corrupt_raster(tmp_path: Path) -> None:
    bad = tmp_path / "broken.tif"
    bad.write_bytes(b"this is not a geotiff at all")
    with pytest.raises(DEMValidationError, match="could not be opened"):
        load_dem_info(bad)


def test_geographic_detection_and_utm_suggestion(geographic_dem: Path) -> None:
    info = load_dem_info(geographic_dem)
    assert info.is_geographic
    assert any("geographic" in w.lower() for w in info.warnings)
    utm = info.suggested_utm()
    assert utm is not None
    assert utm.to_epsg() == 32610  # Portland, OR → UTM 10N


def test_reproject_geographic_to_utm(geographic_dem: Path, tmp_path: Path) -> None:
    info = load_dem_info(geographic_dem)
    out = reproject_dem(geographic_dem, tmp_path / "utm.tif", info.suggested_utm())
    reinfo = load_dem_info(out)
    assert not reinfo.is_geographic
    assert reinfo.crs.to_epsg() == 32610
    with rasterio.open(out) as ds:
        assert ds.nodata is not None
        assert ds.read(1, masked=True).count() > 0
