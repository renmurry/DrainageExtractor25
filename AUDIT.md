# Codebase Audit — DrainageExtractor25

**Date:** 2026-07-03
**Auditor:** Claude (automated overhaul)
**Baseline commit:** `chore: import original codebase (streamlit skeleton)`

## 1. Scope and method

Every file in the original repository was read in full. The repo was assessed against the
target: a production-ready desktop application that extracts drainage networks
(streams, Strahler order, watersheds) from digital elevation models.

## 2. Inventory of the original codebase

| Path | Contents | Verdict |
|---|---|---|
| `app/streamlit_app.py` | 11-line Streamlit page: title, file-uploader widget, "App is alive" banner. No processing wired to the uploader. | **Replace.** Streamlit is a web framework; the target is a native desktop app (PySide6). Nothing to salvage beyond the app title string. |
| `app/hello.py` | 2-line Streamlit hello-world smoke test. | **Delete.** Scaffolding leftover. |
| `core/dem_loader.py` | Empty (0 bytes). | **Rewrite from scratch** as `drainage_extractor.core.dem`. |
| `core/hydrology.py` | Empty (0 bytes). | **Rewrite from scratch** as the engine layer + stream/watershed modules. |
| `core/__init__.py` | Empty. | Superseded by new package. |
| `requirements.txt` | Single pin: `streamlit==1.39.0`. | **Replace** with `pyproject.toml` (src layout, PEP 621 metadata, extras). |
| `samples/.keep` | Placeholder. | Superseded by `examples/` with a real (synthetic) sample DEM. |
| `.gitignore` | Reasonable basics (`.venv/`, `__pycache__/`, `output/`, logs). Had a UTF-8 BOM. | **Keep the intent**, rewrite expanded and BOM-free. |

There were no tests, no license, no README, no CI, no packaging, no type hints, and no
processing code of any kind. Total original logic: ~13 lines of UI scaffolding.

## 3. Key findings

1. **The project is an intent, not an implementation.** The directory names
   (`core/dem_loader.py`, `core/hydrology.py`) show the right decomposition instinct —
   separate I/O from hydrology — but both files are empty.
2. **Framework mismatch.** Streamlit reruns the whole script per interaction, offers no
   real worker-thread/cancellation model, no drag-and-drop, no native file dialogs, and
   is awkward to ship as a single .exe. Wrong foundation for the stated goal; PySide6 is
   the right one.
3. **No dependency strategy for geospatial work.** Nothing in place for raster I/O,
   CRS handling, or hydrology. Decision recorded below.
4. **No reproducibility or quality gates.** No tests, lockstep versioning, CI, or license.

## 4. Decisions for the rebuild

* **Hydrology engine:** WhiteboxTools (standalone Rust binary, no GDAL CLI dependency,
  trivially bundled by PyInstaller) driven through a thin subprocess wrapper with
  progress parsing and cancellation. A pure-NumPy/SciPy fallback engine (priority-flood
  fill, D8, accumulation) keeps the test suite and the app functional when the binary is
  absent; breaching specifically requires WhiteboxTools and degrades to filling with a
  logged warning.
* **Raster/vector I/O:** rasterio + pyproj + geopandas/pyogrio. No `gdal` CLI calls anywhere.
* **Stream topology in Python:** thresholding, link segmentation, Strahler ordering, and
  vectorization are implemented once in NumPy/Shapely on top of either engine's
  D8/accumulation rasters — identical results and attributes regardless of backend.
* **GUI:** PySide6, dark QSS theme, QGraphicsView map canvas, all processing in a
  cancelable worker thread.
* **Structure:** `src/` layout, PEP 621 `pyproject.toml`, MIT license, pytest suite that
  runs the full pipeline on a committed synthetic sample DEM, PyInstaller onefile spec,
  GitHub Actions for CI + tagged-release .exe builds.

## 5. What was kept

The repository identity (name, remote), the git history (original code imported as the
baseline commit so the overhaul is diffable), the `.gitignore` intent, and the app's
name/purpose. Everything else is new.
