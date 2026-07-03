"""Map canvas: hillshade preview with vector overlays (QGraphicsView).

Scene coordinates are pixel coordinates of the decimated preview grid; the
affine ``transform`` of that grid converts to/from map coordinates, so vector
layers in map CRS overlay exactly.
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView
from rasterio.transform import Affine

from drainage_extractor.gui import theme

log = logging.getLogger(__name__)


class MapCanvas(QGraphicsView):
    """Interactive canvas: pan, zoom, and a pour-point picking mode."""

    #: Emitted in pour-point mode with the clicked map coordinates.
    map_clicked = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor(theme.BG))
        self.setFrameShape(QGraphicsView.NoFrame)

        self._image_item: QGraphicsPixmapItem | None = None
        self._qimage: QImage | None = None
        self._transform: Affine | None = None
        self._stream_items: list = []
        self._watershed_items: list = []
        self._marker_items: list = []
        self._pour_mode = False
        self._zoom = 1.0

    # ------------------------------------------------------------ base image
    def set_hillshade(self, shade: np.ma.MaskedArray, transform: Affine) -> None:
        """Show a hillshade preview (uint8 masked array + its affine transform)."""
        arr = np.ascontiguousarray(shade.filled(0), dtype=np.uint8)
        h, w = arr.shape
        img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8).copy()
        self._qimage = img
        self._transform = transform

        self.clear_overlays()
        if self._image_item is not None:
            self._scene.removeItem(self._image_item)
        self._image_item = self._scene.addPixmap(QPixmap.fromImage(img))
        self._image_item.setZValue(0)
        self._scene.setSceneRect(QRectF(-w * 0.05, -h * 0.05, w * 1.1, h * 1.1))
        self.fit_view()

    def has_image(self) -> bool:
        return self._image_item is not None

    def fit_view(self) -> None:
        if self._image_item is not None:
            self.fitInView(self._image_item, Qt.KeepAspectRatio)
            self._zoom = 1.0

    # -------------------------------------------------------------- overlays
    def _to_scene(self, x: float, y: float) -> QPointF:
        col, row = ~self._transform * (x, y)  # type: ignore[operator]
        return QPointF(col, row)

    def _to_map(self, scene_pt: QPointF) -> tuple[float, float]:
        x, y = self._transform * (scene_pt.x(), scene_pt.y())  # type: ignore[operator]
        return float(x), float(y)

    def set_streams(self, gdf) -> None:
        """Overlay stream polylines coloured (and widened) by Strahler order."""
        for item in self._stream_items:
            self._scene.removeItem(item)
        self._stream_items.clear()
        if self._transform is None or gdf is None or gdf.empty:
            return

        paths: dict[int, QPainterPath] = {}
        for order, geom in zip(gdf["order"].astype(int), gdf.geometry):
            path = paths.setdefault(order, QPainterPath())
            coords = list(geom.coords)
            path.moveTo(self._to_scene(*coords[0][:2]))
            for pt in coords[1:]:
                path.lineTo(self._to_scene(*pt[:2]))
        for order in sorted(paths):
            pen = QPen(theme.order_color(order), theme.order_width(order))
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            item = self._scene.addPath(paths[order], pen)
            item.setZValue(10 + order)
            self._stream_items.append(item)
        log.debug("Canvas: drew %d stream links in %d order groups", len(gdf), len(paths))

    def add_watershed(self, gdf) -> None:
        """Overlay watershed polygon(s) with a translucent fill."""
        if self._transform is None or gdf is None or gdf.empty:
            return
        pen = QPen(QColor("#f5b942"), 1.6)
        pen.setCosmetic(True)
        brush = QBrush(QColor(245, 185, 66, 55))
        for geom in gdf.geometry:
            polys = [geom] if geom.geom_type == "Polygon" else (
                list(geom.geoms) if geom.geom_type == "MultiPolygon" else []
            )
            for poly in polys:
                path = QPainterPath()
                ext = list(poly.exterior.coords)
                path.moveTo(self._to_scene(*ext[0][:2]))
                for pt in ext[1:]:
                    path.lineTo(self._to_scene(*pt[:2]))
                path.closeSubpath()
                item = self._scene.addPath(path, pen, brush)
                item.setZValue(5)
                self._watershed_items.append(item)

    def add_pour_marker(self, x: float, y: float) -> None:
        """Cross-hair marker at a snapped pour point (map coordinates)."""
        if self._transform is None:
            return
        pt = self._to_scene(x, y)
        pen = QPen(QColor("#f5b942"), 2.0)
        pen.setCosmetic(True)
        r = 4.0
        item = self._scene.addEllipse(pt.x() - r, pt.y() - r, 2 * r, 2 * r, pen)
        item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
        item.setZValue(30)
        self._marker_items.append(item)

    def clear_watersheds(self) -> None:
        for item in (*self._watershed_items, *self._marker_items):
            self._scene.removeItem(item)
        self._watershed_items.clear()
        self._marker_items.clear()

    def clear_overlays(self) -> None:
        self.set_streams(None)
        self.clear_watersheds()

    # ------------------------------------------------------------ interaction
    def set_pour_mode(self, enabled: bool) -> None:
        """Toggle pour-point picking (click emits :attr:`map_clicked`)."""
        self._pour_mode = enabled
        self.setDragMode(QGraphicsView.NoDrag if enabled else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt naming
        if self._pour_mode and event.button() == Qt.LeftButton and self._transform is not None:
            x, y = self._to_map(self.mapToScene(event.position().toPoint()))
            self.map_clicked.emit(x, y)
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._image_item is None:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_zoom = self._zoom * factor
        if 0.05 <= new_zoom <= 200.0:
            self._zoom = new_zoom
            self.scale(factor, factor)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        if self._image_item is None:
            painter.resetTransform()
            painter.setPen(QColor(theme.TEXT_DIM))
            font = painter.font()
            font.setPointSize(13)
            painter.setFont(font)
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignCenter,
                "Drop a DEM here (GeoTIFF · IMG · ASC)\nor use File → Open DEM…",
            )
