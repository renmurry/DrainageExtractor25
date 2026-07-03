"""Stream network extraction.

From a D8 pointer + flow-accumulation pair (produced by either engine), this
module thresholds stream cells, segments them into links at junctions,
assigns Strahler orders, prunes short first-order fingers, and vectorizes the
result into a GeoDataFrame of polylines with attributes:

* ``link_id`` — unique link identifier
* ``order`` — Strahler stream order
* ``length_m`` — geodesic length for geographic CRSs, planar otherwise
* ``upstream_area_m2`` / ``upstream_area_km2`` — drainage area at the link outlet
* ``to_link`` — ``link_id`` of the downstream link (-1 at network outlets)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
from pyproj import CRS, Geod
from rasterio.transform import Affine
from shapely.geometry import LineString

from drainage_extractor.core.d8 import receivers_flat

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")


def suggest_threshold_cells(n_valid_cells: int) -> int:
    """Auto-suggest a stream threshold (in cells) from the DEM size.

    Heuristic: 0.5 % of the valid cells, clamped to [50, 100 000]. On typical
    DEMs this lands near the classic "1 km² support area" rule of thumb while
    still producing a visible network on small tiles. Always user-adjustable.
    """
    return int(np.clip(round(n_valid_cells * 0.005), 50, 100_000))


@dataclass
class Link:
    """One stream link between topological nodes (raster-space)."""

    link_id: int
    cells: list[int]           # flat indices, upstream → downstream (may end on a junction cell)
    own_end: int               # last flat index owned by this link (excludes shared junction)
    ds_start: int | None       # flat index of the junction starting the downstream link
    order: int = 0
    to_link: int = -1
    length: float = 0.0        # metres
    upstream_area_m2: float = 0.0
    upstream_links: list[int] = field(default_factory=list)


def extract_stream_mask(facc: np.ndarray, valid: np.ndarray, threshold_cells: int) -> np.ndarray:
    """Boolean stream raster: valid cells whose accumulation meets the threshold."""
    return valid & (facc >= float(threshold_cells))


def _walk_links(
    pointer: np.ndarray, stream: np.ndarray, valid: np.ndarray
) -> tuple[list[Link], np.ndarray]:
    """Segment stream cells into links at junctions.

    Returns the links and the receiver array (reused by callers).

    Conventions: a link starts at a channel head (no stream donors) or at a
    junction cell (≥2 stream donors — the junction belongs to the downstream
    link); upstream links append the junction cell to their geometry so lines
    connect, but attributes are read at ``own_end`` (their last owned cell).
    """
    cols = pointer.shape[1]
    recv = receivers_flat(pointer, valid)
    stream_flat = stream.ravel()

    # In-degree restricted to the stream network.
    stream_idx = np.nonzero(stream_flat)[0]
    r_of_stream = recv[stream_idx]
    goes_to_stream = r_of_stream >= 0
    goes_to_stream[goes_to_stream] &= stream_flat[r_of_stream[goes_to_stream]]
    indeg = np.zeros(pointer.size, dtype=np.int32)
    np.add.at(indeg, r_of_stream[goes_to_stream], 1)

    heads = stream_idx[indeg[stream_idx] == 0]
    junctions = stream_idx[indeg[stream_idx] >= 2]
    is_junction = np.zeros(pointer.size, dtype=bool)
    is_junction[junctions] = True

    links: list[Link] = []
    start_to_link: dict[int, int] = {}
    for start in [*heads.tolist(), *junctions.tolist()]:
        path = [int(start)]
        u = int(start)
        ds_start: int | None = None
        while True:
            r = int(recv[u])
            if r < 0 or not stream_flat[r]:
                break  # network outlet / edge of data
            if is_junction[r]:
                ds_start = r
                path.append(r)  # shared vertex for connectivity
                break
            path.append(r)
            u = r
        own_end = u
        link = Link(link_id=len(links), cells=path, own_end=own_end, ds_start=ds_start)
        links.append(link)
        start_to_link[int(start)] = link.link_id

    for link in links:
        if link.ds_start is not None and link.ds_start in start_to_link:
            link.to_link = start_to_link[link.ds_start]
            links[link.to_link].upstream_links.append(link.link_id)
    _ = cols  # cols retained for clarity; receivers already flat-indexed
    return links, recv


def _assign_strahler(links: list[Link]) -> None:
    """Strahler ordering by topological (Kahn) traversal of the link graph."""
    remaining = {ln.link_id: len(ln.upstream_links) for ln in links}
    queue = [ln.link_id for ln in links if remaining[ln.link_id] == 0]
    while queue:
        lid = queue.pop()
        link = links[lid]
        if not link.upstream_links:
            link.order = 1
        else:
            orders = [links[u].order for u in link.upstream_links]
            top = max(orders)
            link.order = top + 1 if orders.count(top) >= 2 else top
        if link.to_link >= 0:
            remaining[link.to_link] -= 1
            if remaining[link.to_link] == 0:
                queue.append(link.to_link)
    for link in links:  # cycles cannot occur in D8, but never leave order 0
        if link.order == 0:
            link.order = 1


def _cell_center(flat: int, cols: int, transform: Affine) -> tuple[float, float]:
    r, c = divmod(flat, cols)
    x, y = transform * (c + 0.5, r + 0.5)
    return float(x), float(y)


def _link_geometry(link: Link, cols: int, transform: Affine, recv: np.ndarray) -> LineString | None:
    """Polyline through cell centres; single-cell links extend to their receiver."""
    pts = [_cell_center(f, cols, transform) for f in link.cells]
    if len(pts) == 1:
        r = int(recv[link.cells[0]])
        if r < 0:
            return None  # isolated single cell with nowhere to go — drop
        pts.append(_cell_center(r, cols, transform))
    return LineString(pts)


def _line_length_m(line: LineString, crs: CRS | None) -> float:
    if crs is not None and crs.is_geographic:
        return abs(_GEOD.geometry_length(line))
    return float(line.length)


def build_network(
    pointer: np.ndarray,
    facc: np.ndarray,
    valid: np.ndarray,
    transform: Affine,
    crs: CRS | None,
    threshold_cells: int,
    cell_area_m2: float,
    min_length_m: float = 0.0,
) -> gpd.GeoDataFrame:
    """Extract the vector stream network from pointer + accumulation rasters.

    Args:
        pointer: D8 codes (WBT convention), int array.
        facc: flow accumulation in cells.
        valid: data mask.
        transform: raster affine transform.
        crs: raster CRS (None allowed — lengths then in cell units).
        threshold_cells: accumulation threshold defining a stream cell.
        cell_area_m2: area of one cell in m².
        min_length_m: iteratively prune order-1 headwater links shorter than this.

    Returns:
        GeoDataFrame of stream links (possibly empty), CRS set from ``crs``.
    """
    stream = extract_stream_mask(facc, valid, threshold_cells)
    n_stream = int(stream.sum())
    log.info("Threshold %d cells → %d stream cells", threshold_cells, n_stream)
    if n_stream == 0:
        return gpd.GeoDataFrame(
            {"link_id": [], "order": [], "length_m": [], "upstream_area_m2": [],
             "upstream_area_km2": [], "to_link": []},
            geometry=[], crs=crs,
        )

    cols = pointer.shape[1]
    for _round in range(20):  # pruning re-derivation loop, bounded
        links, recv = _walk_links(pointer, stream, valid)
        _assign_strahler(links)

        geoms: dict[int, LineString] = {}
        for link in links:
            geom = _link_geometry(link, cols, transform, recv)
            if geom is not None:
                geoms[link.link_id] = geom
                link.length = _line_length_m(geom, crs)
                link.upstream_area_m2 = float(facc.ravel()[link.own_end]) * cell_area_m2

        if min_length_m <= 0:
            break
        doomed = [
            ln for ln in links
            if ln.link_id in geoms and not ln.upstream_links and ln.length < min_length_m
        ]
        # Only prune true headwater fingers; junction-continuation links stay.
        doomed = [ln for ln in doomed if ln.order == 1]
        if not doomed:
            break
        flat_stream = stream.ravel()
        for ln in doomed:
            own = [f for f in ln.cells if f != ln.ds_start]  # keep shared junction cells
            flat_stream[own] = False
        stream = flat_stream.reshape(stream.shape)
        log.info("Pruned %d short headwater link(s) < %.1f m; re-deriving topology", len(doomed), min_length_m)
    else:  # pragma: no cover
        log.warning("Pruning did not converge in 20 rounds; using current network")

    keep = [ln for ln in links if ln.link_id in geoms]
    gdf = gpd.GeoDataFrame(
        {
            "link_id": [ln.link_id for ln in keep],
            "order": [ln.order for ln in keep],
            "length_m": [round(ln.length, 2) for ln in keep],
            "upstream_area_m2": [round(ln.upstream_area_m2, 1) for ln in keep],
            "upstream_area_km2": [round(ln.upstream_area_m2 / 1e6, 4) for ln in keep],
            "to_link": [ln.to_link for ln in keep],
        },
        geometry=[geoms[ln.link_id] for ln in keep],
        crs=crs,
    )
    log.info(
        "Network: %d links, %.1f km total, max order %d",
        len(gdf), gdf["length_m"].sum() / 1000.0, int(gdf["order"].max()) if len(gdf) else 0,
    )
    return gdf


def network_summary(gdf: gpd.GeoDataFrame) -> str:
    """One-line summary for the status bar."""
    if gdf.empty:
        return "No streams at this threshold — try lowering it."
    total_km = gdf["length_m"].sum() / 1000.0
    return (
        f"{len(gdf)} links · {total_km:,.1f} km of streams · "
        f"max Strahler order {int(gdf['order'].max())}"
    )
