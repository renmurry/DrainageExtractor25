"""Pour-point snapping and watershed delineation."""

from __future__ import annotations

import numpy as np

from drainage_extractor.core.watershed import delineate_watershed, snap_pour_point


def test_snap_and_delineate(pipeline_result) -> None:
    res = pipeline_result
    pointer, facc, valid, transform = res.load_grids()

    # Click near the middle-south of the tile, where the trunk stream runs.
    rr, cc = np.unravel_index(int(np.argmax(np.where(valid, facc, -1))), facc.shape)
    x, y = transform * (cc + 0.5, rr + 0.5)
    rc = snap_pour_point(x + 30, y + 30, facc, valid, transform, snap_radius_cells=10)
    assert rc is not None
    assert facc[rc] >= facc[rr, cc] * 0.5  # snapped onto a strong flow line

    ws = delineate_watershed(
        pointer, valid, rc, transform, res.dem_info.crs, res.dem_info.cell_area_m2, point_id=1
    )
    assert len(ws) == 1
    area_km2 = float(ws["area_km2"].iloc[0])
    dem_km2 = res.dem_info.n_cells * res.dem_info.cell_area_m2 / 1e6
    assert 0 < area_km2 <= dem_km2
    assert ws.geometry.iloc[0].is_valid
    # The outlet's accumulation should roughly match the polygon's cell count.
    assert facc[rc] * res.dem_info.cell_area_m2 / 1e6 <= area_km2 * 1.05


def test_snap_outside_data_returns_none(pipeline_result) -> None:
    res = pipeline_result
    _, facc, valid, transform = res.load_grids()
    assert snap_pour_point(-1e9, -1e9, facc, valid, transform) is None
