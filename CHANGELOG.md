# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-07-03

Complete rewrite: from a Streamlit placeholder to a production desktop app
(see `AUDIT.md` for the audit of the original codebase).

### Added
- PySide6 desktop GUI: dark theme, drag-and-drop DEM loading, hillshade map
  canvas with Strahler-coloured stream overlay, watershed picking, log-scale
  threshold slider, collapsible log console, cancelable worker threads.
- Core pipeline: depression breaching (WhiteboxTools) or sink filling,
  optional feature-preserving smoothing, D8 flow direction, flow
  accumulation, threshold-based stream extraction with an auto-suggested
  default, Strahler ordering, short-headwater pruning, vectorization with
  `order` / `length_m` / `upstream_area_m2` attributes.
- Watershed delineation from clicked pour points, with snapping to the
  strongest nearby flow line.
- DEM validation on load (CRS, resolution, nodata, size, elevation stats)
  with a guided auto-reprojection to UTM for geographic-CRS inputs.
- Hydrology engines: WhiteboxTools subprocess backend (progress parsing,
  cancellation) plus a pure NumPy/SciPy fallback so the app and tests work
  without the binary; breaching degrades to filling with a clear warning.
- Exports: GeoPackage, Shapefile, GeoJSON, KML/KMZ, DXF with EPSG search and
  on-export reprojection; optional conditioned-DEM, flow-accumulation and
  hillshade GeoTIFFs.
- Robustness: memory budgeting before runs, block-streamed raster I/O,
  plain-language error dialogs, rotating file logs.
- Packaging: PyInstaller one-file Windows spec bundling WhiteboxTools and the
  app icon; GitHub Actions CI plus tagged-release exe builds.
- `examples/sample_dem.tif` (synthetic, deterministic) and a pytest suite
  covering the full pipeline, every export format and the GUI (offscreen).

### Changed
- Repository restructured to a `src/` layout with PEP 621 packaging
  (`pip install -e .`), MIT license, typed and documented modules.

### Removed
- Streamlit skeleton (`app/streamlit_app.py`, `app/hello.py`) and the empty
  `core/` placeholder modules.

## [0.1.0] — original import

- Streamlit "app is alive" skeleton with a file-uploader stub.
