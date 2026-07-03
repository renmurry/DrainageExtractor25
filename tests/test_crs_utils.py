"""EPSG search and UTM suggestion."""

from __future__ import annotations

from drainage_extractor.core.crs_utils import describe_crs, search_crs, utm_crs_for


def test_search_by_code() -> None:
    hits = search_crs("32610")
    assert hits and hits[0].code == 32610
    assert "UTM zone 10N" in hits[0].name


def test_search_by_code_prefix() -> None:
    hits = search_crs("326")
    codes = [h.code for h in hits]
    assert 32601 in codes or 326 in codes or any(str(c).startswith("326") for c in codes)


def test_search_by_name_words() -> None:
    hits = search_crs("wgs 84 utm 10n")
    assert any(h.code == 32610 for h in hits)


def test_search_empty_and_garbage() -> None:
    assert search_crs("") == []
    assert search_crs("zzzznotacrs") == []


def test_utm_for_hemispheres() -> None:
    assert utm_crs_for(-122.3, 45.5).to_epsg() == 32610   # Portland → 10N
    assert utm_crs_for(151.2, -33.9).to_epsg() == 32756   # Sydney → 56S
    assert utm_crs_for(-179.9, 10.0).to_epsg() == 32601   # antimeridian west edge
    assert utm_crs_for(179.9, 10.0).to_epsg() == 32660


def test_describe_crs_none() -> None:
    assert describe_crs(None) == "No CRS defined"
