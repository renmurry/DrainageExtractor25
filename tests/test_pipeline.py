"""Full pipeline runs on the sample DEM (Definition-of-Done coverage)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest

from drainage_extractor.core.engine import find_whitebox_binary
from drainage_extractor.core.errors import PipelineCancelled
from drainage_extractor.core.exports import export_vector_layers
from drainage_extractor.core.pipeline import PipelineParams, run_pipeline


def test_full_pipeline_on_sample(pipeline_result) -> None:
    res = pipeline_result
    assert res.threshold_cells > 0
    assert not res.streams.empty
    assert res.streams["order"].max() >= 2
    assert (res.streams["length_m"] > 0).all()
    assert (res.streams["upstream_area_m2"] > 0).all()
    for p in (res.conditioned_path, res.pointer_path, res.facc_path):
        assert Path(p).exists()
    # Built-in engine cannot breach → the downgrade must be surfaced to the user.
    if find_whitebox_binary() is None:
        assert any("breach" in w.lower() for w in res.warnings)


def test_pipeline_gpkg_roundtrip(pipeline_result, tmp_path: Path) -> None:
    """Fresh clone → extract → export GPKG → read back (the DoD path)."""
    out = tmp_path / "network.gpkg"
    written = export_vector_layers({"streams": pipeline_result.streams}, out, "gpkg")
    assert written == [out] and out.stat().st_size > 0
    back = gpd.read_file(out)
    assert len(back) == len(pipeline_result.streams)
    assert back.crs is not None and back.crs.to_epsg() == 32610
    assert {"order", "length_m", "upstream_area_m2"} <= set(back.columns)


def test_pipeline_fill_and_smooth(sample_dem: Path, tmp_path: Path) -> None:
    res = run_pipeline(
        sample_dem,
        PipelineParams(preprocess="fill", smooth=True, smooth_strength=4, engine="builtin"),
        workdir=tmp_path,
    )
    assert not res.streams.empty
    assert res.timings.get("smooth", 0) >= 0


def test_pipeline_cancellation(sample_dem: Path, tmp_path: Path) -> None:
    with pytest.raises(PipelineCancelled):
        run_pipeline(
            sample_dem,
            PipelineParams(engine="builtin"),
            workdir=tmp_path,
            cancelled=lambda: True,
        )


def test_explicit_threshold_respected(sample_dem: Path, tmp_path: Path) -> None:
    res = run_pipeline(
        sample_dem,
        PipelineParams(engine="builtin", threshold_cells=1500),
        workdir=tmp_path,
    )
    assert res.threshold_cells == 1500


@pytest.mark.skipif(find_whitebox_binary() is None, reason="WhiteboxTools binary not available")
def test_full_pipeline_whitebox(sample_dem: Path, tmp_path: Path) -> None:
    res = run_pipeline(
        sample_dem, PipelineParams(preprocess="breach", engine="whitebox"), workdir=tmp_path
    )
    assert not res.streams.empty
    assert res.engine_name == "WhiteboxTools"
