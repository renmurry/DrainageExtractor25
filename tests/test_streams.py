"""Stream topology: link segmentation, Strahler orders, pruning, attributes."""

from __future__ import annotations

import numpy as np
from rasterio.transform import from_origin

from drainage_extractor.core.streams import build_network, suggest_threshold_cells

TRANSFORM = from_origin(0.0, 100.0, 10.0, 10.0)  # 10 m cells
S, SE, SW, E = 8, 4, 16, 2  # WBT D8 codes


def _y_network() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two headwater branches joining at row 3, trunk continuing south.

    Layout (5×5, streams marked, everything else drains into the channels):

        .A.B.
        .A.B.
        .AB..
        .T...      A,B: order-1 branches → T: order-2 trunk
        .T...
    """
    pointer = np.full((6, 5), S, dtype=np.int16)
    facc = np.ones((6, 5), dtype="float64")
    valid = np.ones((6, 5), dtype=bool)

    # Branch A: (0,1)->(1,1)->(2,1) then SE into the junction (3,1)? Keep A due south into (3,1).
    for r in (0, 1, 2):
        pointer[r, 1] = S
        facc[r, 1] = 10 * (r + 1)
    # Branch B: (0,3)->(1,3) then SW to (2,2), SW to junction (3,1)
    pointer[0, 3] = S
    pointer[1, 3] = SW
    pointer[2, 2] = SW
    facc[0, 3], facc[1, 3], facc[2, 2] = 10, 20, 30
    # Junction + trunk
    facc[3, 1] = 70
    pointer[3, 1] = S
    facc[4, 1] = 80
    pointer[4, 1] = S
    facc[5, 1] = 90
    pointer[5, 1] = 0  # outlet
    return pointer, facc, valid


def test_strahler_y_junction() -> None:
    pointer, facc, valid = _y_network()
    gdf = build_network(
        pointer, facc, valid, TRANSFORM, None, threshold_cells=10, cell_area_m2=100.0
    )
    orders = sorted(gdf["order"].tolist())
    assert orders.count(1) == 2, f"expected two order-1 branches, got {gdf[['order']].values}"
    assert max(orders) == 2, "the trunk below the junction must be order 2"

    trunk = gdf[gdf["order"] == 2].iloc[0]
    assert trunk["to_link"] == -1  # trunk ends at the outlet
    branches = gdf[gdf["order"] == 1]
    assert set(branches["to_link"]) == {trunk["link_id"]}
    # Upstream area grows downstream and reflects facc at the link end.
    assert trunk["upstream_area_m2"] >= branches["upstream_area_m2"].max()
    assert (gdf["length_m"] > 0).all()


def test_min_length_pruning() -> None:
    pointer, facc, valid = _y_network()
    # Give branch B a stumpy variant: raise the threshold so only 2 of its cells qualify.
    gdf_all = build_network(pointer, facc, valid, TRANSFORM, None, 10, 100.0, min_length_m=0.0)
    gdf_pruned = build_network(pointer, facc, valid, TRANSFORM, None, 10, 100.0, min_length_m=45.0)
    assert len(gdf_pruned) <= len(gdf_all)
    # Whatever survives must respect the length floor for order-1 headwaters
    # (junction-fed links are exempt by design).
    heads = gdf_pruned[(gdf_pruned["order"] == 1)]
    assert (heads["length_m"] >= 45.0).all() or heads.empty


def test_empty_network_at_absurd_threshold() -> None:
    pointer, facc, valid = _y_network()
    gdf = build_network(pointer, facc, valid, TRANSFORM, None, 10_000, 100.0)
    assert gdf.empty


def test_threshold_suggestion_bounds() -> None:
    assert suggest_threshold_cells(1_000) == 50           # clamped low
    assert suggest_threshold_cells(100_000) == 500        # 0.5 %
    assert suggest_threshold_cells(10**9) == 100_000      # clamped high
