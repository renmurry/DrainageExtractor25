"""Hydrology engines: built-in NumPy implementation (+ WhiteboxTools when present)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio

from drainage_extractor.core.d8 import D8_CODES
from drainage_extractor.core.engine import WhiteboxEngine, find_whitebox_binary, get_engine
from drainage_extractor.core.fallback import (
    BuiltinEngine,
    d8_pointer_array,
    fill_depressions_array,
    flow_accumulation_array,
)

SOUTH = 8  # WBT D8 code for due-south flow


def _read(path: Path) -> np.ndarray:
    with rasterio.open(path) as ds:
        return ds.read(1, masked=True)


def test_fill_removes_depressions(bowl_dem: Path) -> None:
    engine = BuiltinEngine()
    out = engine.condition_dem(bowl_dem, bowl_dem.parent / "filled.tif", "fill")
    filled = _read(out)
    z = filled.filled(np.nan)
    valid = ~filled.mask if np.ma.is_masked(filled) else np.isfinite(z)
    pointer = d8_pointer_array(z.astype("float64"), valid)
    interior = np.zeros_like(valid)
    interior[1:-1, 1:-1] = True
    # After epsilon-filling, every interior cell must have a downslope neighbour.
    assert np.all(pointer[valid & interior] > 0), "pits remain after filling"


def test_breach_downgrades_to_fill_with_builtin(bowl_dem: Path, caplog) -> None:
    engine = BuiltinEngine()
    out = engine.condition_dem(bowl_dem, bowl_dem.parent / "b.tif", "breach")
    assert out.exists()
    assert not engine.supports_breaching


def test_d8_on_tilted_plane(tilted_plane: Path) -> None:
    z = _read(tilted_plane)
    arr = z.astype("float64").filled(np.nan)
    valid = ~np.ma.getmaskarray(z)
    filled = fill_depressions_array(arr, valid)
    pointer = d8_pointer_array(filled, valid)
    interior = pointer[1:-1, 1:-1]
    assert np.all(np.isin(interior, D8_CODES)), "interior cells must all drain"
    assert (interior == SOUTH).mean() > 0.95  # essentially everything flows south


def test_flow_accumulation_conservation(tilted_plane: Path) -> None:
    z = _read(tilted_plane)
    arr = z.astype("float64").filled(np.nan)
    valid = ~np.ma.getmaskarray(z)
    filled = fill_depressions_array(arr, valid)
    pointer = d8_pointer_array(filled, valid)
    acc = flow_accumulation_array(pointer, valid)
    rows = z.shape[0]
    # Straight-south flow: the last row collects its full column.
    assert acc.max() == pytest.approx(rows, abs=1)
    assert acc[valid].min() >= 1.0


def test_get_engine_builtin_explicit() -> None:
    assert isinstance(get_engine("builtin"), BuiltinEngine)


@pytest.mark.skipif(find_whitebox_binary() is None, reason="WhiteboxTools binary not available")
def test_whitebox_full_stack(sample_dem: Path, tmp_path: Path) -> None:
    """Integration: breach → pointer → accumulation through the real binary."""
    eng = WhiteboxEngine()
    cond = eng.condition_dem(sample_dem, tmp_path / "c.tif", "breach")
    pntr = eng.d8_pointer(cond, tmp_path / "p.tif")
    facc = eng.flow_accumulation(pntr, tmp_path / "f.tif")
    with rasterio.open(facc) as ds:
        acc = ds.read(1, masked=True)
    assert float(acc.max()) > 1000  # a real outlet accumulated
    with rasterio.open(pntr) as ds:
        codes = ds.read(1, masked=True).compressed()
    assert set(np.unique(codes)).issubset({0, *D8_CODES})
