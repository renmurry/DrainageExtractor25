"""Built-in hydrology engine — pure NumPy/SciPy, no external binary.

Used automatically when the WhiteboxTools executable cannot be found, and by
the test suite so CI never depends on a download. Slower than WhiteboxTools
and does not implement least-cost breaching (requests to breach degrade to
sink filling with a logged warning), but produces byte-compatible products:
conditioned DEM, D8 pointer (WBT code convention) and cell-count flow
accumulation.
"""

from __future__ import annotations

import heapq
import logging
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
import rasterio
import scipy.ndimage as ndi

from drainage_extractor.core.d8 import D8_CODES, D8_DISTANCES, D8_OFFSETS, receivers_flat
from drainage_extractor.core.engine import Engine
from drainage_extractor.core.errors import PipelineCancelled
from drainage_extractor.core.rasters import write_geotiff

log = logging.getLogger(__name__)

ProgressCB = Callable[[float, str], None]
CancelCheck = Callable[[], bool]

#: Minimum elevation increment used to keep filled flats draining.
FILL_EPSILON = 1e-4

_CANCEL_STRIDE = 50_000


def _tick(cancelled: CancelCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise PipelineCancelled()


def _read(path: Path) -> tuple[np.ndarray, np.ndarray, rasterio.Affine, object, float]:
    """Read band 1 → (float64 array, valid mask, transform, crs, nodata)."""
    with rasterio.open(path) as ds:
        masked = ds.read(1, masked=True)
        nodata = ds.nodata if ds.nodata is not None else -32768.0
        arr = masked.astype("float64").filled(np.nan)
        valid = ~np.ma.getmaskarray(masked) & np.isfinite(arr)
        return arr, valid, ds.transform, ds.crs, float(nodata)


def fill_depressions_array(
    z: np.ndarray,
    valid: np.ndarray,
    *,
    epsilon: float = FILL_EPSILON,
    progress: ProgressCB | None = None,
    cancelled: CancelCheck | None = None,
) -> np.ndarray:
    """Priority-flood depression filling with an epsilon drainage gradient.

    Implements Barnes et al. (2014): flood inward from the data boundary using
    a min-heap, raising every cell to at least ``spill + epsilon`` so that the
    output has no pits and no perfectly flat areas.

    Args:
        z: 2-D float64 elevation (NaN allowed on invalid cells).
        valid: 2-D bool mask of data cells.

    Returns:
        Filled float64 array (NaN preserved outside ``valid``).
    """
    rows, cols = z.shape
    filled = z.copy()
    visited = ~valid
    n_valid = int(valid.sum())

    # Seed: valid cells on the grid edge or touching nodata.
    edge = np.zeros_like(valid)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    interior_touching = ndi.binary_dilation(~valid, structure=np.ones((3, 3), bool)) & valid
    seeds = valid & (edge | interior_touching)

    heap: list[tuple[float, int, int]] = [
        (float(z[r, c]), int(r), int(c)) for r, c in zip(*np.nonzero(seeds))
    ]
    heapq.heapify(heap)
    visited[seeds] = True

    done = len(heap)
    while heap:
        e, r, c = heapq.heappop(heap)
        for dr, dc in D8_OFFSETS:
            nr, nc = r + dr, c + dc
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols or visited[nr, nc]:
                continue
            visited[nr, nc] = True
            filled[nr, nc] = max(float(z[nr, nc]), e + epsilon)
            heapq.heappush(heap, (float(filled[nr, nc]), nr, nc))
            done += 1
            if done % _CANCEL_STRIDE == 0:
                _tick(cancelled)
                if progress is not None and n_valid:
                    progress(done / n_valid, "Filling sinks")
    if progress is not None:
        progress(1.0, "Filling sinks")
    return filled


def d8_pointer_array(z: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Steepest-descent D8 codes (WBT convention) from a conditioned surface.

    Cells with no downslope neighbour get code 0 (pit/outlet).
    """
    zz = np.where(valid, z, np.nan)
    best = np.full(z.shape, 0.0)
    code = np.zeros(z.shape, dtype=np.int16)
    for i, (dr, dc) in enumerate(D8_OFFSETS):
        shifted = np.full_like(zz, np.nan)
        src = (slice(max(0, -dr), z.shape[0] - max(0, dr)), slice(max(0, -dc), z.shape[1] - max(0, dc)))
        dst = (slice(max(0, dr), z.shape[0] + min(0, dr) or None), slice(max(0, dc), z.shape[1] + min(0, dc) or None))
        # shifted[r,c] = zz[r+dr, c+dc]
        shifted[src[0], src[1]] = zz[dst[0], dst[1]]
        drop = (zz - shifted) / D8_DISTANCES[i]
        drop = np.where(np.isfinite(drop), drop, -np.inf)
        better = drop > best
        code[better] = D8_CODES[i]
        best = np.maximum(best, drop)
    code[~valid] = -1
    return code


def flow_accumulation_array(
    pointer: np.ndarray,
    valid: np.ndarray,
    *,
    progress: ProgressCB | None = None,
    cancelled: CancelCheck | None = None,
) -> np.ndarray:
    """Cell-count D8 flow accumulation via topological (Kahn) traversal."""
    n = pointer.size
    recv = receivers_flat(pointer, valid)
    acc = np.where(valid.ravel(), 1.0, 0.0)
    indeg = np.bincount(recv[recv >= 0], minlength=n).astype(np.int64)

    queue: deque[int] = deque(np.nonzero(valid.ravel() & (indeg == 0))[0].tolist())
    processed = 0
    n_valid = int(valid.sum()) or 1
    while queue:
        u = queue.popleft()
        r = int(recv[u])
        if r >= 0:
            acc[r] += acc[u]
            indeg[r] -= 1
            if indeg[r] == 0:
                queue.append(r)
        processed += 1
        if processed % _CANCEL_STRIDE == 0:
            _tick(cancelled)
            if progress is not None:
                progress(processed / n_valid, "Accumulating flow")
    if progress is not None:
        progress(1.0, "Accumulating flow")
    return acc.reshape(pointer.shape)


class BuiltinEngine(Engine):
    """NumPy/SciPy implementation of the :class:`~.engine.Engine` interface."""

    name = "Built-in (NumPy)"
    supports_breaching = False

    def condition_dem(
        self,
        dem: Path,
        out: Path,
        method: str,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        if method == "breach":
            log.warning(
                "The built-in engine cannot breach depressions — falling back to sink filling. "
                "Install WhiteboxTools for least-cost breaching."
            )
        z, valid, transform, crs, nodata = _read(Path(dem))
        filled = fill_depressions_array(z, valid, progress=progress, cancelled=cancelled)
        write_geotiff(out, np.where(valid, filled, nodata), transform, crs, nodata, dtype="float64")
        return out

    def smooth_dem(
        self,
        dem: Path,
        out: Path,
        strength: int,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        z, valid, transform, crs, nodata = _read(Path(dem))
        _tick(cancelled)
        sigma = max(1, min(10, strength)) / 2.0
        w = valid.astype("float64")
        num = ndi.gaussian_filter(np.where(valid, z, 0.0), sigma=sigma)
        den = ndi.gaussian_filter(w, sigma=sigma)
        with np.errstate(invalid="ignore", divide="ignore"):
            sm = np.where(den > 1e-9, num / den, z)
        if progress is not None:
            progress(1.0, "Smoothing the surface")
        write_geotiff(out, np.where(valid, sm, nodata), transform, crs, nodata, dtype="float32")
        return out

    def d8_pointer(
        self,
        conditioned: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        z, valid, transform, crs, _ = _read(Path(conditioned))
        _tick(cancelled)
        code = d8_pointer_array(z, valid)
        if progress is not None:
            progress(1.0, "Computing flow directions")
        write_geotiff(out, code, transform, crs, -1, dtype="int16")
        return out

    def flow_accumulation(
        self,
        pointer: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        with rasterio.open(pointer) as ds:
            codes = ds.read(1, masked=True)
            transform, crs = ds.transform, ds.crs
        valid = ~np.ma.getmaskarray(codes) & (codes.filled(-1) >= 0)
        acc = flow_accumulation_array(codes.filled(-1).astype(np.int16), valid, progress=progress, cancelled=cancelled)
        write_geotiff(out, np.where(valid, acc, -1.0), transform, crs, -1.0, dtype="float64")
        return out
