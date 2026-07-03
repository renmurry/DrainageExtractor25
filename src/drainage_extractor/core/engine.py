"""Hydrology engine layer.

Two interchangeable engines produce the same three products from a DEM —
conditioned (breached/filled) DEM, D8 pointer, and D8 flow accumulation —
as GeoTIFF files:

* :class:`WhiteboxEngine` — drives the standalone WhiteboxTools binary via
  subprocess (fast, scales to very large rasters, supports least-cost
  breaching). Preferred whenever the binary can be located.
* :class:`BuiltinEngine` (see :mod:`.fallback`) — pure NumPy/SciPy
  implementation used when WhiteboxTools is absent. Supports filling but not
  breaching.

All stages accept a ``progress`` callback ``(fraction, message)`` and a
``cancelled`` callable; cancellation kills the WBT subprocess.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from drainage_extractor.core.errors import EngineError, PipelineCancelled

log = logging.getLogger(__name__)

ProgressCB = Callable[[float, str], None]
CancelCheck = Callable[[], bool]

#: Environment variable that overrides WhiteboxTools binary discovery.
WBT_ENV_VAR = "DRAINAGE_EXTRACTOR_WBT"

_PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")


class Engine(ABC):
    """Common interface of the hydrology backends."""

    name: str = "abstract"
    supports_breaching: bool = False

    @abstractmethod
    def condition_dem(
        self,
        dem: Path,
        out: Path,
        method: str,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Remove depressions from ``dem`` (``method``: 'breach' or 'fill')."""

    @abstractmethod
    def smooth_dem(
        self,
        dem: Path,
        out: Path,
        strength: int,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Denoise ``dem`` (strength 1–10) before conditioning."""

    @abstractmethod
    def d8_pointer(
        self,
        conditioned: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """D8 flow directions (WhiteboxTools code convention, see :mod:`.d8`)."""

    @abstractmethod
    def flow_accumulation(
        self,
        pointer: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Number of upslope cells (self included) draining through each cell."""


# --------------------------------------------------------------------------- WBT
def find_whitebox_binary() -> Path | None:
    """Locate the WhiteboxTools executable.

    Search order: ``DRAINAGE_EXTRACTOR_WBT`` env var → PyInstaller bundle →
    ``drainage_extractor/bin/`` (populated by ``scripts/fetch_whitebox.py``) →
    ``PATH`` → the ``whitebox`` pip package, if installed.
    """
    exe = "whitebox_tools.exe" if os.name == "nt" else "whitebox_tools"

    env = os.environ.get(WBT_ENV_VAR)
    if env and Path(env).is_file():
        return Path(env)

    if getattr(sys, "frozen", False):  # PyInstaller
        bundled = Path(getattr(sys, "_MEIPASS", ".")) / "wbt" / exe
        if bundled.is_file():
            return bundled

    pkg_bin = Path(__file__).resolve().parent.parent / "bin" / exe
    if pkg_bin.is_file():
        return pkg_bin

    on_path = shutil.which(exe)
    if on_path:
        return Path(on_path)

    try:  # optional `whitebox` pip package carries the binary too
        import whitebox  # type: ignore[import-not-found]

        candidate = Path(whitebox.__file__).parent / "WBT" / exe
        if candidate.is_file():
            return candidate
    except ImportError:
        pass
    return None


class WhiteboxEngine(Engine):
    """WhiteboxTools subprocess wrapper with progress parsing and cancellation."""

    name = "WhiteboxTools"
    supports_breaching = True

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or find_whitebox_binary()
        if self.binary is None:
            raise EngineError(
                "WhiteboxTools could not be found.",
                suggestion=(
                    "Run 'python scripts/fetch_whitebox.py' to download it, or set the "
                    f"{WBT_ENV_VAR} environment variable to the whitebox_tools executable."
                ),
            )
        log.info("Using WhiteboxTools at %s", self.binary)

    # ------------------------------------------------------------------ runner
    def _run(
        self,
        tool: str,
        args: list[str],
        progress: ProgressCB | None,
        cancelled: CancelCheck | None,
        message: str,
    ) -> None:
        cmd = [str(self.binary), f"--run={tool}", *args, "-v"]
        log.debug("WBT: %s", " ".join(cmd))
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creation,
            )
        except OSError as exc:
            raise EngineError(
                "WhiteboxTools failed to start.",
                suggestion="The binary may be corrupt or blocked by antivirus — re-download it.",
                details=f"{self.binary}: {exc}",
            ) from exc

        tail: list[str] = []
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    tail.append(line)
                    del tail[:-25]
                    log.debug("WBT| %s", line)
                m = _PROGRESS_RE.search(line)
                if m and progress is not None:
                    progress(min(100, int(m.group(1))) / 100.0, message)
                if cancelled is not None and cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise PipelineCancelled()
            code = proc.wait()
        finally:
            proc.stdout.close()
        if code != 0:
            raise EngineError(
                f"The {tool} step failed inside WhiteboxTools.",
                suggestion="Check the log for details; the DEM may have unusual values or nodata.",
                details="\n".join(tail[-12:]),
            )

    # ------------------------------------------------------------------ stages
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
            self._run(
                "BreachDepressionsLeastCost",
                [f"--dem={dem}", f"--output={out}", "--dist=100", "--fill"],
                progress,
                cancelled,
                "Breaching depressions",
            )
        elif method == "fill":
            self._run(
                "FillDepressions",
                [f"--dem={dem}", f"--output={out}", "--fix_flats"],
                progress,
                cancelled,
                "Filling sinks",
            )
        else:
            raise ValueError(f"Unknown conditioning method: {method!r}")
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
        size = 2 * max(1, min(10, strength)) + 1  # 3..21, odd
        self._run(
            "FeaturePreservingSmoothing",
            [f"--dem={dem}", f"--output={out}", f"--filter={size}", "--norm_diff=15.0"],
            progress,
            cancelled,
            "Smoothing the surface",
        )
        return out

    def d8_pointer(
        self,
        conditioned: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        self._run(
            "D8Pointer",
            [f"--dem={conditioned}", f"--output={out}"],
            progress,
            cancelled,
            "Computing flow directions",
        )
        return out

    def flow_accumulation(
        self,
        pointer: Path,
        out: Path,
        *,
        progress: ProgressCB | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        self._run(
            "D8FlowAccumulation",
            [f"--input={pointer}", f"--output={out}", "--out_type=cells", "--pntr"],
            progress,
            cancelled,
            "Accumulating flow",
        )
        return out


def get_engine(preferred: str = "auto") -> Engine:
    """Return the best available engine.

    Args:
        preferred: "auto" (WhiteboxTools when available, else built-in),
            "whitebox", or "builtin".
    """
    from drainage_extractor.core.fallback import BuiltinEngine

    if preferred == "builtin":
        return BuiltinEngine()
    if preferred == "whitebox":
        return WhiteboxEngine()
    if find_whitebox_binary() is not None:
        try:
            return WhiteboxEngine()
        except EngineError:  # pragma: no cover - race between find and init
            pass
    log.warning("WhiteboxTools not found — using the built-in NumPy engine (slower, no breaching).")
    return BuiltinEngine()
