"""CRS helpers: EPSG search, UTM suggestion, coordinate conversion."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.database import query_crs_info
from pyproj.enums import PJType


@dataclass(frozen=True)
class CRSCandidate:
    """One row of an EPSG search result."""

    code: int
    name: str
    kind: str  # "projected" or "geographic"

    @property
    def label(self) -> str:
        return f"EPSG:{self.code} — {self.name} ({self.kind})"

    def to_crs(self) -> CRS:
        return CRS.from_epsg(self.code)


@lru_cache(maxsize=1)
def _epsg_catalogue() -> tuple[CRSCandidate, ...]:
    """All non-deprecated EPSG projected + geographic 2D CRSs (cached)."""
    out: list[CRSCandidate] = []
    for kind, pj_type in (("projected", PJType.PROJECTED_CRS), ("geographic", PJType.GEOGRAPHIC_2D_CRS)):
        for info in query_crs_info(auth_name="EPSG", pj_types=pj_type):
            if info.deprecated:
                continue
            out.append(CRSCandidate(code=int(info.code), name=info.name, kind=kind))
    out.sort(key=lambda c: c.code)
    return tuple(out)


def search_crs(query: str, limit: int = 40) -> list[CRSCandidate]:
    """Search the EPSG registry by code or name substring.

    A purely numeric query matches codes by prefix ("326" finds all northern
    UTM zones); anything else matches case-insensitive name substrings, with
    multiple words all required ("utm 10 wgs" finds EPSG:32610).
    """
    q = query.strip()
    if not q:
        return []
    catalogue = _epsg_catalogue()
    if q.isdigit():
        exact = [c for c in catalogue if str(c.code) == q]
        prefix = [c for c in catalogue if str(c.code).startswith(q) and str(c.code) != q]
        return (exact + prefix)[:limit]
    words = q.lower().split()
    hits = [c for c in catalogue if all(w in c.name.lower() or w in str(c.code) for w in words)]
    return hits[:limit]


def utm_crs_for(lon: float, lat: float) -> CRS:
    """Return the WGS 84 UTM CRS containing a lon/lat point."""
    zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def centroid_lonlat(crs: CRS, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    """Centroid of a bounding box expressed in WGS 84 lon/lat.

    Args:
        crs: the CRS the bounds are expressed in.
        bounds: (left, bottom, right, top).
    """
    cx = (bounds[0] + bounds[2]) / 2.0
    cy = (bounds[1] + bounds[3]) / 2.0
    if crs.is_geographic:
        return cx, cy
    tr = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    lon, lat = tr.transform(cx, cy)
    return float(lon), float(lat)


def describe_crs(crs: CRS | None) -> str:
    """Short human-readable CRS description for the info panel."""
    if crs is None:
        return "No CRS defined"
    auth = crs.to_authority()
    tag = f"{auth[0]}:{auth[1]}" if auth else "custom"
    kind = "geographic" if crs.is_geographic else "projected"
    return f"{crs.name} ({tag}, {kind})"
