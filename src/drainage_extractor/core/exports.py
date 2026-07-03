"""Vector + raster exporters with on-the-fly CRS reprojection.

Vector formats: GeoPackage (default), Shapefile, GeoJSON, KML/KMZ, DXF.
Raster add-ons: conditioned DEM, flow accumulation, hillshade.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from drainage_extractor.core.errors import ExportError
from drainage_extractor.core.rasters import hillshade as make_hillshade

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorFormat:
    key: str
    label: str
    extension: str
    fixed_crs: int | None = None  # EPSG forced by the format (KML/GeoJSON → 4326)


VECTOR_FORMATS: tuple[VectorFormat, ...] = (
    VectorFormat("gpkg", "GeoPackage (*.gpkg)", ".gpkg"),
    VectorFormat("shp", "Shapefile (*.shp)", ".shp"),
    VectorFormat("geojson", "GeoJSON (*.geojson)", ".geojson", fixed_crs=4326),
    VectorFormat("kml", "KML (*.kml)", ".kml", fixed_crs=4326),
    VectorFormat("kmz", "KMZ (*.kmz)", ".kmz", fixed_crs=4326),
    VectorFormat("dxf", "AutoCAD DXF (*.dxf)", ".dxf"),
)

#: Shapefile DBF limits field names to 10 characters.
_SHP_RENAMES = {"upstream_area_m2": "uparea_m2", "upstream_area_km2": "uparea_km2"}

#: KML line colours (aabbggrr) per Strahler order, light → deep blue.
_KML_ORDER_COLOURS = (
    "ffffc66e", "fff5a542", "fff39621", "ffe5881e",
    "ffd27619", "ffc06515", "ffa1470d", "ff733306",
)


def _prepare(gdf: gpd.GeoDataFrame, dst_crs: CRS | None, fmt: VectorFormat) -> gpd.GeoDataFrame:
    """Validate + reproject a layer for export."""
    if gdf is None or gdf.empty:
        raise ExportError(
            "There is nothing to export yet.",
            suggestion="Extract a stream network first (or lower the threshold).",
        )
    target = CRS.from_epsg(fmt.fixed_crs) if fmt.fixed_crs else dst_crs
    if target is not None:
        if gdf.crs is None:
            raise ExportError(
                "The DEM has no coordinate reference system, so the layer cannot be reprojected.",
                suggestion="Export without changing the CRS, or load a DEM with a defined CRS.",
            )
        if CRS.from_user_input(gdf.crs) != target:
            gdf = gdf.to_crs(target)
    return gdf


def export_vector_layers(
    layers: dict[str, gpd.GeoDataFrame],
    path: str | Path,
    fmt_key: str,
    dst_crs: CRS | None = None,
) -> list[Path]:
    """Export named layers to ``path`` in the chosen format.

    GPKG holds all layers in one file; single-layer formats write one file per
    layer, suffixing the extra ones with the layer name.

    Returns the list of files written.
    """
    fmt = next((f for f in VECTOR_FORMATS if f.key == fmt_key), None)
    if fmt is None:
        raise ExportError(f"Unknown export format '{fmt_key}'.")
    path = Path(path).with_suffix(fmt.extension)
    layers = {name: g for name, g in layers.items() if g is not None and not g.empty}
    if not layers:
        raise ExportError(
            "There is nothing to export yet.",
            suggestion="Extract a stream network first.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt.key == "gpkg":
            for name, gdf in layers.items():
                _prepare(gdf, dst_crs, fmt).to_file(path, layer=name, driver="GPKG")
            written = [path]
        elif fmt.key in ("shp", "geojson"):
            written = []
            multi = len(layers) > 1
            driver = "ESRI Shapefile" if fmt.key == "shp" else "GeoJSON"
            for i, (name, gdf) in enumerate(layers.items()):
                out = path if (i == 0 and not multi) else path.with_stem(f"{path.stem}_{name}")
                g = _prepare(gdf, dst_crs, fmt)
                if fmt.key == "shp":
                    g = g.rename(columns=_SHP_RENAMES)
                g.to_file(out, driver=driver)
                written.append(out)
        elif fmt.key in ("kml", "kmz"):
            written = [_export_kml(layers, path, fmt)]
        elif fmt.key == "dxf":
            written = [_export_dxf(layers, path, dst_crs)]
        else:  # pragma: no cover
            raise ExportError(f"Format '{fmt.key}' is not implemented.")
    except ExportError:
        raise
    except Exception as exc:  # translate library errors into plain language
        raise ExportError(
            f"Writing '{path.name}' failed.",
            suggestion="Check that the file is not open in another program and the folder is writable.",
            details=repr(exc),
        ) from exc

    log.info("Exported %s layer(s) to %s", len(layers), ", ".join(p.name for p in written))
    return written


# ------------------------------------------------------------------------- KML
def _kml_colour(order: int) -> str:
    idx = max(0, min(len(_KML_ORDER_COLOURS) - 1, int(order) - 1))
    return _KML_ORDER_COLOURS[idx]


def _export_kml(layers: dict[str, gpd.GeoDataFrame], path: Path, fmt: VectorFormat) -> Path:
    import simplekml

    kml = simplekml.Kml(name=path.stem)
    for name, gdf in layers.items():
        g = _prepare(gdf, None, fmt)
        folder = kml.newfolder(name=name)
        for _, row in g.iterrows():
            geom = row.geometry
            if geom.geom_type == "LineString":
                ls = folder.newlinestring(
                    name=f"link {row.get('link_id', '')}".strip(),
                    coords=list(geom.coords),
                )
                order = int(row.get("order", 1) or 1)
                ls.style.linestyle.color = _kml_colour(order)
                ls.style.linestyle.width = 1 + order
                ls.description = (
                    f"Strahler order: {order}<br/>Length: {row.get('length_m', 0):,.0f} m"
                    f"<br/>Upstream area: {row.get('upstream_area_km2', 0):,.3f} km²"
                )
            elif geom.geom_type in ("Polygon", "MultiPolygon"):
                polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
                for p in polys:
                    poly = folder.newpolygon(
                        name=f"watershed {row.get('ws_id', '')}".strip(),
                        outerboundaryis=list(p.exterior.coords),
                    )
                    poly.style.polystyle.color = "5534b9f5"
                    poly.style.linestyle.color = "ff34b9f5"
                    poly.style.linestyle.width = 2
    if fmt.key == "kmz":
        kml.savekmz(str(path))
    else:
        kml.save(str(path))
    return path


# ------------------------------------------------------------------------- DXF
def _export_dxf(layers: dict[str, gpd.GeoDataFrame], path: Path, dst_crs: CRS | None) -> Path:
    """DXF R2010 with one layer per Strahler order (+ one per extra layer name)."""
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # AutoCAD colour indices: blues/cyans for streams, distinct for watersheds.
    order_aci = {1: 141, 2: 151, 3: 161, 4: 171, 5: 5, 6: 175, 7: 176, 8: 178}
    fmt = next(f for f in VECTOR_FORMATS if f.key == "dxf")
    for name, gdf in layers.items():
        g = _prepare(gdf, dst_crs, fmt)
        if "order" in g.columns:
            for order in sorted(g["order"].unique()):
                lname = f"STREAMS_ORDER_{int(order)}"
                doc.layers.add(lname, color=order_aci.get(int(order), 5))
                for geom in g[g["order"] == order].geometry:
                    msp.add_lwpolyline(list(geom.coords), dxfattribs={"layer": lname})
        else:
            lname = name.upper()[:31] or "LAYER"
            doc.layers.add(lname, color=40)
            for geom in g.geometry:
                polys = [geom] if geom.geom_type == "Polygon" else (
                    list(geom.geoms) if geom.geom_type == "MultiPolygon" else []
                )
                if polys:
                    for p in polys:
                        msp.add_lwpolyline(list(p.exterior.coords), dxfattribs={"layer": lname, "flags": 1})
                elif geom.geom_type == "LineString":
                    msp.add_lwpolyline(list(geom.coords), dxfattribs={"layer": lname})
    doc.saveas(path)
    return path


# ---------------------------------------------------------------------- raster
def export_raster(
    src_path: str | Path,
    dst_path: str | Path,
    dst_crs: CRS | None = None,
) -> Path:
    """Copy (or reproject) a single-band raster to ``dst_path``, block-streamed."""
    src_path, dst_path = Path(src_path), Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with contextlib.ExitStack() as stack:
            src = stack.enter_context(rasterio.open(src_path))
            need_warp = dst_crs is not None and src.crs is not None and CRS.from_user_input(src.crs) != dst_crs
            ds = (
                stack.enter_context(WarpedVRT(src, crs=dst_crs.to_wkt(), resampling=Resampling.bilinear))
                if need_warp
                else src
            )
            profile = {
                "driver": "GTiff", "width": ds.width, "height": ds.height, "count": 1,
                "dtype": ds.dtypes[0], "crs": ds.crs, "transform": ds.transform,
                "nodata": ds.nodata, "tiled": True, "compress": "deflate",
                "BIGTIFF": "IF_SAFER",
            }
            block = 1024
            with rasterio.open(dst_path, "w", **profile) as dst:
                for row in range(0, ds.height, block):
                    h = min(block, ds.height - row)
                    win = Window(0, row, ds.width, h)
                    dst.write(ds.read(1, window=win), 1, window=win)
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(
            f"Writing '{dst_path.name}' failed.",
            suggestion="Check disk space and that the file is not open elsewhere.",
            details=repr(exc),
        ) from exc
    return dst_path


def export_hillshade(
    conditioned_path: str | Path,
    dst_path: str | Path,
    geographic: bool,
) -> Path:
    """Render and save a full-resolution hillshade of the conditioned DEM."""
    conditioned_path, dst_path = Path(conditioned_path), Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(conditioned_path) as ds:
            elev = ds.read(1, masked=True)
            hs = make_hillshade(elev, ds.transform, geographic=geographic)
            profile = {
                "driver": "GTiff", "width": ds.width, "height": ds.height, "count": 1,
                "dtype": "uint8", "crs": ds.crs, "transform": ds.transform, "nodata": 0,
                "tiled": True, "compress": "deflate",
            }
            with rasterio.open(dst_path, "w", **profile) as dst:
                dst.write(hs.filled(0), 1)
    except Exception as exc:
        raise ExportError(
            f"Writing '{dst_path.name}' failed.",
            suggestion="Check disk space and that the file is not open elsewhere.",
            details=repr(exc),
        ) from exc
    return dst_path


def default_export_name(dem_name: str, fmt_key: str) -> str:
    """streams_<dem>.<ext> default file name for the export dialog."""
    fmt = next((f for f in VECTOR_FORMATS if f.key == fmt_key), VECTOR_FORMATS[0])
    stem = Path(dem_name).stem
    return f"streams_{stem}{fmt.extension}"
