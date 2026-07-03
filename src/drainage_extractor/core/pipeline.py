"""The staged extraction pipeline: DEM → conditioned DEM → D8 → accumulation → vector streams.

Every stage reports progress through a single callback and honours a
cancellation check between (and inside) stages, so the GUI thread can drive a
progress bar and a working Cancel button without ever blocking.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

from drainage_extractor.core import streams as streams_mod
from drainage_extractor.core.dem import DEMInfo, ensure_hydrology_ready, load_dem_info
from drainage_extractor.core.engine import Engine, get_engine
from drainage_extractor.core.errors import PipelineCancelled
from drainage_extractor.core.rasters import check_memory_budget

log = logging.getLogger(__name__)

ProgressCB = Callable[[str, float, str], None]  # (stage_id, overall 0..1, message)
CancelCheck = Callable[[], bool]

#: (stage_id, human label, relative weight)
STAGES: tuple[tuple[str, str, float], ...] = (
    ("prepare", "Preparing DEM", 5),
    ("smooth", "Smoothing", 10),
    ("condition", "Removing depressions", 35),
    ("pointer", "Flow directions", 15),
    ("facc", "Flow accumulation", 20),
    ("extract", "Tracing streams", 15),
)


@dataclass
class PipelineParams:
    """User-facing extraction parameters."""

    preprocess: str = "breach"          # "breach" (default) or "fill"
    smooth: bool = False
    smooth_strength: int = 3            # 1..10
    threshold_cells: int | None = None  # None → auto-suggested from DEM stats
    min_stream_length_m: float = 0.0
    engine: str = "auto"                # "auto" | "whitebox" | "builtin"


@dataclass
class PipelineResult:
    """Everything the GUI and the exporters need after a run."""

    dem_info: DEMInfo
    workdir: Path
    conditioned_path: Path
    pointer_path: Path
    facc_path: Path
    streams: gpd.GeoDataFrame
    threshold_cells: int
    engine_name: str
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def load_grids(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, rasterio.Affine]:
        """Re-read (pointer, facc, valid, transform) for watershed tools."""
        with rasterio.open(self.pointer_path) as ds:
            pm = ds.read(1, masked=True)
            transform = ds.transform
        pointer = pm.filled(-1).astype(np.int16)
        with rasterio.open(self.facc_path) as ds:
            fm = ds.read(1, masked=True)
        facc = fm.filled(0).astype("float64")
        valid = (~np.ma.getmaskarray(pm)) & (pointer >= 0) & (~np.ma.getmaskarray(fm))
        return pointer, facc, valid, transform


class _Progress:
    """Maps per-stage fractions onto one overall 0..1 progress value."""

    def __init__(self, cb: ProgressCB | None, enabled_stages: dict[str, bool]) -> None:
        self._cb = cb
        weights = [(sid, w) for sid, _, w in STAGES if enabled_stages.get(sid, True)]
        total = sum(w for _, w in weights) or 1.0
        self._start: dict[str, float] = {}
        self._span: dict[str, float] = {}
        acc = 0.0
        for sid, w in weights:
            self._start[sid] = acc / total
            self._span[sid] = w / total
            acc += w

    def stage_cb(self, stage_id: str) -> Callable[[float, str], None]:
        def cb(fraction: float, message: str) -> None:
            if self._cb is None or stage_id not in self._start:
                return
            overall = self._start[stage_id] + max(0.0, min(1.0, fraction)) * self._span[stage_id]
            self._cb(stage_id, overall, message)

        return cb


def run_pipeline(
    dem_path: str | Path,
    params: PipelineParams,
    workdir: str | Path | None = None,
    dem_info: DEMInfo | None = None,
    engine: Engine | None = None,
    progress: ProgressCB | None = None,
    cancelled: CancelCheck | None = None,
) -> PipelineResult:
    """Run the full extraction pipeline.

    Args:
        dem_path: hydrology-ready or raw DEM path (GeoTIFF/IMG/ASC).
        params: extraction parameters (see :class:`PipelineParams`).
        workdir: directory for intermediate rasters (a temp dir when None).
        dem_info: pre-computed validation report, to skip re-validation.
        engine: pre-constructed engine (tests); otherwise chosen from params.
        progress: ``(stage_id, overall_fraction, message)`` callback.
        cancelled: return True to abort; raises :class:`PipelineCancelled`.

    Raises:
        DEMValidationError, EngineError, MemoryBudgetError, PipelineCancelled
    """
    t0 = time.perf_counter()
    dem_path = Path(dem_path)
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="drainage_"))
    wd.mkdir(parents=True, exist_ok=True)

    def check() -> bool:
        return bool(cancelled and cancelled())

    if dem_info is None:
        dem_info = load_dem_info(dem_path)
    warnings = list(dem_info.warnings)

    eng = engine or get_engine(params.engine)
    log.info("Engine: %s", eng.name)

    # Fail fast if the in-memory stages cannot fit in RAM.
    est = dem_info.estimated_memory_bytes(arrays=8 if eng.supports_breaching is False else 5)
    warnings += check_memory_budget(est, "drainage extraction")

    prog = _Progress(progress, {"smooth": params.smooth})
    timings: dict[str, float] = {}

    def timed(stage: str, fn: Callable[[], Path | None]) -> None:
        if check():
            raise PipelineCancelled()
        t = time.perf_counter()
        fn()
        timings[stage] = time.perf_counter() - t

    # -- prepare ---------------------------------------------------------
    stage_paths: dict[str, Path] = {}

    def _prepare() -> None:
        stage_paths["ready"] = ensure_hydrology_ready(dem_path, wd, cancelled=check)
        prog.stage_cb("prepare")(1.0, "DEM ready for hydrology")

    timed("prepare", _prepare)

    # -- smooth (optional) -------------------------------------------------
    if params.smooth:
        timed(
            "smooth",
            lambda: stage_paths.update(
                smoothed=eng.smooth_dem(
                    stage_paths["ready"], wd / "dem_smoothed.tif", params.smooth_strength,
                    progress=prog.stage_cb("smooth"), cancelled=check,
                )
            ),
        )
    surface = stage_paths.get("smoothed", stage_paths["ready"])

    # -- condition ---------------------------------------------------------
    method = params.preprocess
    if method == "breach" and not eng.supports_breaching:
        warnings.append(
            "Depression breaching needs WhiteboxTools; used sink filling instead. "
            "Run scripts/fetch_whitebox.py to enable breaching."
        )
        method = "fill"
    conditioned = wd / "dem_conditioned.tif"
    timed(
        "condition",
        lambda: eng.condition_dem(
            surface, conditioned, method, progress=prog.stage_cb("condition"), cancelled=check
        ),
    )

    # -- pointer / accumulation ---------------------------------------------
    pointer_path = wd / "d8_pointer.tif"
    timed(
        "pointer",
        lambda: eng.d8_pointer(
            conditioned, pointer_path, progress=prog.stage_cb("pointer"), cancelled=check
        ),
    )
    facc_path = wd / "flow_accum.tif"
    timed(
        "facc",
        lambda: eng.flow_accumulation(
            pointer_path, facc_path, progress=prog.stage_cb("facc"), cancelled=check
        ),
    )

    # -- extract -------------------------------------------------------------
    if check():
        raise PipelineCancelled()
    t = time.perf_counter()
    ex_cb = prog.stage_cb("extract")
    ex_cb(0.05, "Reading flow grids")
    with rasterio.open(pointer_path) as ds:
        pm = ds.read(1, masked=True)
        transform = ds.transform
        crs = dem_info.crs
    pointer = pm.filled(-1).astype(np.int16)
    with rasterio.open(facc_path) as ds:
        fm = ds.read(1, masked=True)
    facc = fm.filled(0).astype("float64")
    valid = (~np.ma.getmaskarray(pm)) & (pointer >= 0) & (~np.ma.getmaskarray(fm))

    threshold = params.threshold_cells or streams_mod.suggest_threshold_cells(int(valid.sum()))
    ex_cb(0.3, f"Extracting streams at {threshold:,} cells")
    network = streams_mod.build_network(
        pointer, facc, valid, transform, crs,
        threshold_cells=threshold,
        cell_area_m2=dem_info.cell_area_m2,
        min_length_m=params.min_stream_length_m,
    )
    if check():
        raise PipelineCancelled()
    ex_cb(1.0, "Network ready")
    timings["extract"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0
    log.info("Pipeline finished in %.1f s (%s)", timings["total"], eng.name)

    return PipelineResult(
        dem_info=dem_info,
        workdir=wd,
        conditioned_path=conditioned,
        pointer_path=pointer_path,
        facc_path=facc_path,
        streams=network,
        threshold_cells=threshold,
        engine_name=eng.name,
        warnings=warnings,
        timings=timings,
    )
