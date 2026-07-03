"""Worker threads — every slow operation runs off the GUI thread.

:class:`PipelineWorker` drives the extraction pipeline with progress and a
working cancel; :class:`FuncWorker` wraps any callable (DEM loading,
reprojection, exports, watershed delineation) with the same error contract.
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from drainage_extractor.core.dem import DEMInfo
from drainage_extractor.core.errors import DrainageError, PipelineCancelled
from drainage_extractor.core.pipeline import PipelineParams, PipelineResult, run_pipeline

log = logging.getLogger(__name__)


class PipelineWorker(QThread):
    """Runs :func:`run_pipeline` in a background thread."""

    progress = Signal(str, float, str)      # stage_id, overall 0..1, message
    finished_ok = Signal(object)            # PipelineResult
    failed = Signal(object)                 # DrainageError | Exception
    cancelled_sig = Signal()

    def __init__(
        self,
        dem_path: Path,
        params: PipelineParams,
        dem_info: DEMInfo | None = None,
        workdir: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._dem_path = dem_path
        self._params = params
        self._dem_info = dem_info
        self._workdir = workdir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Request cooperative cancellation (kills WhiteboxTools mid-tool)."""
        self._cancel.set()

    def run(self) -> None:  # noqa: D102 — QThread entry point
        try:
            result: PipelineResult = run_pipeline(
                self._dem_path,
                self._params,
                workdir=self._workdir,
                dem_info=self._dem_info,
                progress=lambda s, f, m: self.progress.emit(s, f, m),
                cancelled=self._cancel.is_set,
            )
        except PipelineCancelled:
            log.info("Extraction cancelled by user")
            self.cancelled_sig.emit()
        except DrainageError as exc:
            log.error("Pipeline failed: %s", exc.full_text())
            self.failed.emit(exc)
        except Exception as exc:  # pragma: no cover — last-resort catch
            log.exception("Unexpected pipeline failure")
            exc.details = traceback.format_exc()  # type: ignore[attr-defined]
            self.failed.emit(exc)
        else:
            self.finished_ok.emit(result)


class FuncWorker(QThread):
    """Runs an arbitrary callable with the standard done/failed signals.

    The callable may accept a ``cancelled`` keyword (a ``() -> bool`` check)
    when it supports cooperative cancellation.
    """

    finished_ok = Signal(object)
    failed = Signal(object)
    cancelled_sig = Signal()

    def __init__(self, fn: Callable[..., Any], *args: Any, pass_cancel: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._pass_cancel = pass_cancel
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        try:
            if self._pass_cancel:
                self._kwargs["cancelled"] = self._cancel.is_set
            result = self._fn(*self._args, **self._kwargs)
        except PipelineCancelled:
            self.cancelled_sig.emit()
        except DrainageError as exc:
            log.error("Task failed: %s", exc.full_text())
            self.failed.emit(exc)
        except Exception as exc:  # pragma: no cover
            log.exception("Unexpected task failure")
            exc.details = traceback.format_exc()  # type: ignore[attr-defined]
            self.failed.emit(exc)
        else:
            self.finished_ok.emit(result)
