"""Watershed delineation from user-supplied pour points.

Pure-Python on top of the D8 pointer raster, so it behaves identically with
both hydrology engines: snap the click to the strongest nearby flow line,
then collect every cell whose flow path reaches it.
"""

from __future__ import annotations

import logging
from collections import deque

import geopandas as gpd
import numpy as np
import rasterio.features
from pyproj import CRS
from rasterio.transform import Affine, rowcol
from shapely.geometry import Point, shape

from drainage_extractor.core.d8 import receivers_flat

log = logging.getLogger(__name__)


def snap_pour_point(
    x: float,
    y: float,
    facc: np.ndarray,
    valid: np.ndarray,
    transform: Affine,
    snap_radius_cells: int = 15,
) -> tuple[int, int] | None:
    """Snap map coordinates to the highest-accumulation cell nearby.

    Returns the (row, col) of the snapped cell, or None when the click is
    outside the data.
    """
    try:
        r, c = rowcol(transform, x, y)
    except ValueError:
        return None
    rows, cols = facc.shape
    if not (0 <= r < rows and 0 <= c < cols):
        return None
    r0, r1 = max(0, r - snap_radius_cells), min(rows, r + snap_radius_cells + 1)
    c0, c1 = max(0, c - snap_radius_cells), min(cols, c + snap_radius_cells + 1)
    window_acc = np.where(valid[r0:r1, c0:c1], facc[r0:r1, c0:c1], -1.0)
    if window_acc.max() < 0:
        return None
    dr, dc = np.unravel_index(int(np.argmax(window_acc)), window_acc.shape)
    snapped = (r0 + int(dr), c0 + int(dc))
    log.info("Pour point snapped from (%.1f, %.1f) to cell %s (acc=%.0f)", x, y, snapped, window_acc.max())
    return snapped


def delineate_watershed(
    pointer: np.ndarray,
    valid: np.ndarray,
    outlet_rc: tuple[int, int],
    transform: Affine,
    crs: CRS | None,
    cell_area_m2: float,
    point_id: int = 1,
) -> gpd.GeoDataFrame:
    """Delineate the catchment draining to ``outlet_rc``.

    Upstream breadth-first traversal over the inverted D8 graph, then
    polygonized with rasterio. Returns a GeoDataFrame with ``ws_id``,
    ``area_m2``, ``area_km2`` and the outlet coordinates.
    """
    rows, cols = pointer.shape
    recv = receivers_flat(pointer, valid)

    # Invert the flow graph: donors[i] = cells draining directly into i.
    order = np.argsort(recv, kind="stable")
    sorted_recv = recv[order]
    first = np.searchsorted(sorted_recv, 0)  # skip the -1 block

    starts = np.searchsorted(sorted_recv, np.arange(pointer.size), side="left")
    ends = np.searchsorted(sorted_recv, np.arange(pointer.size), side="right")

    outlet_flat = outlet_rc[0] * cols + outlet_rc[1]
    mask = np.zeros(pointer.size, dtype=bool)
    mask[outlet_flat] = True
    queue: deque[int] = deque([outlet_flat])
    while queue:
        u = queue.popleft()
        s, e = int(starts[u]), int(ends[u])
        if e <= s:
            continue
        for donor in order[max(s, first):e]:
            d = int(donor)
            if not mask[d]:
                mask[d] = True
                queue.append(d)

    ws = mask.reshape(rows, cols) & valid
    n_cells = int(ws.sum())
    geom = None
    for g, v in rasterio.features.shapes(ws.astype(np.uint8), mask=ws, transform=transform):
        s = shape(g)
        geom = s if geom is None else geom.union(s)
        _ = v
    if geom is None:  # pragma: no cover — outlet always contributes itself
        geom = Point(transform * (outlet_rc[1] + 0.5, outlet_rc[0] + 0.5)).buffer(abs(transform.a) / 2)

    ox, oy = transform * (outlet_rc[1] + 0.5, outlet_rc[0] + 0.5)
    area_m2 = n_cells * cell_area_m2
    log.info("Watershed %d: %d cells, %.3f km²", point_id, n_cells, area_m2 / 1e6)
    return gpd.GeoDataFrame(
        {
            "ws_id": [point_id],
            "area_m2": [round(area_m2, 1)],
            "area_km2": [round(area_m2 / 1e6, 4)],
            "outlet_x": [round(ox, 3)],
            "outlet_y": [round(oy, 3)],
        },
        geometry=[geom],
        crs=crs,
    )
