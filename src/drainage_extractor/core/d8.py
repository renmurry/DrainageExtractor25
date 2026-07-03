"""Shared D8 flow-direction conventions.

Both hydrology engines (WhiteboxTools and the built-in fallback) produce D8
pointer rasters using the WhiteboxTools base-2 clockwise convention::

    64  128  1
    32   0   2
    16   8   4

i.e. 1=NE, 2=E, 4=SE, 8=S, 16=SW, 32=W, 64=NW, 128=N, and 0 marks a cell with
no downslope neighbour (pit or edge outlet). Nodata cells carry the raster's
nodata value.
"""

from __future__ import annotations

import numpy as np

#: D8 codes in clockwise order starting at NE.
D8_CODES: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128)

#: (row, col) offsets matching :data:`D8_CODES` (row axis points down/south).
D8_OFFSETS: tuple[tuple[int, int], ...] = (
    (-1, 1),   # 1   NE
    (0, 1),    # 2   E
    (1, 1),    # 4   SE
    (1, 0),    # 8   S
    (1, -1),   # 16  SW
    (0, -1),   # 32  W
    (-1, -1),  # 64  NW
    (-1, 0),   # 128 N
)

#: Distance factor (1 for cardinal, sqrt(2) for diagonal) per D8_CODES entry.
D8_DISTANCES: tuple[float, ...] = tuple(
    float(np.hypot(dr, dc)) for dr, dc in D8_OFFSETS
)

_CODE_TO_INDEX = {code: i for i, code in enumerate(D8_CODES)}


def code_to_offset(code: int) -> tuple[int, int]:
    """Return the (drow, dcol) offset for a D8 code.

    Raises:
        KeyError: if ``code`` is not one of the eight valid D8 codes.
    """
    return D8_OFFSETS[_CODE_TO_INDEX[int(code)]]


def receivers_flat(pointer: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Compute the flat (1-D) index of each cell's receiver.

    Args:
        pointer: 2-D int array of D8 codes (0 = no receiver).
        valid: 2-D bool array, True where the cell holds data.

    Returns:
        1-D int64 array of length ``pointer.size``; ``recv[i]`` is the flat
        index of the cell that cell ``i`` drains into, or ``-1`` when the cell
        has no receiver (pit, outlet, nodata, or receiver outside the grid /
        in nodata).
    """
    rows, cols = pointer.shape
    recv = np.full(pointer.size, -1, dtype=np.int64)
    rr, cc = np.nonzero(valid & (pointer > 0))
    codes = pointer[rr, cc]
    for i, code in enumerate(D8_CODES):
        sel = codes == code
        if not np.any(sel):
            continue
        dr, dc = D8_OFFSETS[i]
        tr = rr[sel] + dr
        tc = cc[sel] + dc
        inside = (tr >= 0) & (tr < rows) & (tc >= 0) & (tc < cols)
        # Receivers falling in nodata are treated as no-receiver (edge of data).
        inside[inside] &= valid[tr[inside], tc[inside]]
        src_flat = (rr[sel] * cols + cc[sel])[inside]
        recv[src_flat] = tr[inside] * cols + tc[inside]
    return recv
