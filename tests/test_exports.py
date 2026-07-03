"""Every export format + on-export reprojection."""

from __future__ import annotations

import zipfile
from pathlib import Path

import ezdxf
import geopandas as gpd
import pytest
from pyproj import CRS

from drainage_extractor.core.errors import ExportError
from drainage_extractor.core.exports import (
    default_export_name,
    export_hillshade,
    export_raster,
    export_vector_layers,
)


@pytest.mark.parametrize("fmt", ["gpkg", "shp", "geojson", "kml", "kmz", "dxf"])
def test_every_format_writes(pipeline_result, tmp_path: Path, fmt: str) -> None:
    out = tmp_path / f"net.{fmt}"
    written = export_vector_layers({"streams": pipeline_result.streams}, out, fmt)
    assert written, f"{fmt} produced nothing"
    for p in written:
        assert p.exists() and p.stat().st_size > 0, f"{fmt}: {p} empty"


def test_gpkg_multi_layer_roundtrip(pipeline_result, tmp_path: Path) -> None:
    streams = pipeline_result.streams
    fake_ws = streams.head(1).copy()
    fake_ws["geometry"] = fake_ws.geometry.buffer(50)
    out = tmp_path / "multi.gpkg"
    export_vector_layers({"streams": streams, "watersheds": fake_ws}, out, "gpkg")
    assert len(gpd.read_file(out, layer="streams")) == len(streams)
    assert len(gpd.read_file(out, layer="watersheds")) == 1


def test_export_reprojection(pipeline_result, tmp_path: Path) -> None:
    out = tmp_path / "wgs84.gpkg"
    export_vector_layers(
        {"streams": pipeline_result.streams}, out, "gpkg", dst_crs=CRS.from_epsg(4326)
    )
    back = gpd.read_file(out)
    assert back.crs.to_epsg() == 4326
    minx, miny, maxx, maxy = back.total_bounds
    assert -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


def test_geojson_always_wgs84(pipeline_result, tmp_path: Path) -> None:
    written = export_vector_layers({"streams": pipeline_result.streams}, tmp_path / "n.geojson", "geojson")
    back = gpd.read_file(written[0])
    minx, _, maxx, _ = back.total_bounds
    assert -180 <= minx <= maxx <= 180


def test_shapefile_field_names_fit_dbf(pipeline_result, tmp_path: Path) -> None:
    written = export_vector_layers({"streams": pipeline_result.streams}, tmp_path / "s.shp", "shp")
    back = gpd.read_file(written[0])
    assert all(len(c) <= 10 for c in back.columns if c != "geometry")
    assert "uparea_m2" in back.columns


def test_kmz_is_zip(pipeline_result, tmp_path: Path) -> None:
    written = export_vector_layers({"streams": pipeline_result.streams}, tmp_path / "n.kmz", "kmz")
    assert zipfile.is_zipfile(written[0])


def test_dxf_layers_by_order(pipeline_result, tmp_path: Path) -> None:
    written = export_vector_layers({"streams": pipeline_result.streams}, tmp_path / "n.dxf", "dxf")
    doc = ezdxf.readfile(written[0])
    names = {layer.dxf.name for layer in doc.layers}
    assert "STREAMS_ORDER_1" in names
    assert len(list(doc.modelspace())) == len(pipeline_result.streams)


def test_empty_export_raises_friendly(tmp_path: Path) -> None:
    empty = gpd.GeoDataFrame({"a": []}, geometry=[], crs="EPSG:32610")
    with pytest.raises(ExportError, match="nothing to export"):
        export_vector_layers({"streams": empty}, tmp_path / "e.gpkg", "gpkg")


def test_raster_exports(pipeline_result, tmp_path: Path) -> None:
    import rasterio

    cond = export_raster(pipeline_result.conditioned_path, tmp_path / "cond.tif")
    warped = export_raster(
        pipeline_result.facc_path, tmp_path / "facc4326.tif", dst_crs=CRS.from_epsg(4326)
    )
    hs = export_hillshade(pipeline_result.conditioned_path, tmp_path / "hs.tif", geographic=False)
    with rasterio.open(cond) as ds:
        assert ds.crs.to_epsg() == 32610
    with rasterio.open(warped) as ds:
        assert ds.crs.to_epsg() == 4326
    with rasterio.open(hs) as ds:
        assert ds.dtypes[0] == "uint8"
        band = ds.read(1)
        assert band.max() > 1  # actual relief rendered


def test_default_export_name() -> None:
    assert default_export_name("dem_tile.tif", "gpkg") == "streams_dem_tile.gpkg"
