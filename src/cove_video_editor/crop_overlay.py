from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


HANDLE_SIZE = 10
HIT_PAD = 14
MIN_NORMALIZED = 0.05

CROP_ASPECT_PRESETS: dict[str, float | None] = {
    "Free (Custom)": None,
    "16:9 (Landscape / YouTube)": 16 / 9,
    "9:16 (TikTok / Reels / Shorts)": 9 / 16,
    "1:1 (Square / Instagram)": 1.0,
    "4:5 (Portrait / Social)": 4 / 5,
    "4:3 (Standard / Classic)": 4 / 3,
    "21:9 (Cinematic / Ultrawide)": 21 / 9,
}


class CropOverlay(QWidget):
    """Draggable crop rectangle in normalized 0..1 source coords.

    Renders on top of a video widget, accounts for letterboxing so the rect
    always tracks the actual video pixels rather than the widget area.
    Supports locking to standard aspect ratio presets (16:9, 9:16, 1:1, etc.).
    """

    cropChanged = Signal(QRectF)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._video_aspect: float = 16 / 9
        self._aspect_lock: float | None = None
        self._preset_name: str = "Free (Custom)"
        self._fit_mode: str = "fill"  # "fill" (crop to fill), "fit" (letterbox / keep proportions), "stretch"
        self._rect_norm: QRectF = QRectF(0.0, 0.0, 1.0, 1.0)
        self._drag_target: str | None = None
        self._drag_start_widget: QPointF | None = None
        self._drag_start_rect: QRectF | None = None

    def set_fit_mode(self, mode: str) -> None:
        """Set canvas fit mode: 'fill' (crop to fill), 'fit' (letterbox / keep all), 'stretch'."""
        self._fit_mode = mode if mode in ("fill", "fit", "stretch") else "fill"
        if self._fit_mode in ("fit", "stretch"):
            self._rect_norm = QRectF(0.0, 0.0, 1.0, 1.0)
        elif self._aspect_lock is not None:
            self.fit_to_canvas()
        self.update()
        self.cropChanged.emit(self.normalized_rect())

    def fit_mode(self) -> str:
        return self._fit_mode

    def set_video_aspect(self, aspect: float) -> None:
        if aspect > 0:
            self._video_aspect = aspect
            if self._aspect_lock is not None and self._fit_mode == "fill":
                self.set_aspect_ratio_preset(self._aspect_lock, self._preset_name)
            self.update()

    def set_aspect_ratio_preset(
        self, aspect_target: float | None, preset_name: str = "",
    ) -> None:
        """Set or clear the aspect ratio lock.

        aspect_target: target pixel aspect ratio (e.g. 16/9, 9/16, 1.0) or None for Free.
        Automatically fits and centers a maximum-area crop rectangle within 0..1 bounds.
        """
        self._aspect_lock = aspect_target
        self._preset_name = preset_name or ("Free (Custom)" if aspect_target is None else f"{aspect_target:.2f}")
        if aspect_target is not None and self._video_aspect > 0:
            norm_ar = aspect_target / self._video_aspect
            if norm_ar <= 1.0:
                h = 1.0
                w = max(MIN_NORMALIZED, norm_ar)
            else:
                w = 1.0
                h = max(MIN_NORMALIZED, 1.0 / norm_ar)
            x = max(0.0, (1.0 - w) / 2.0)
            y = max(0.0, (1.0 - h) / 2.0)
            self._rect_norm = QRectF(x, y, w, h)
            self.update()
            self.cropChanged.emit(self.normalized_rect())
        else:
            self.update()

    def aspect_ratio_preset(self) -> float | None:
        return self._aspect_lock

    def set_normalized_rect(self, rect: QRectF) -> None:
        self._rect_norm = self._clamp(QRectF(rect))
        self.update()

    def normalized_rect(self) -> QRectF:
        return QRectF(self._rect_norm)

    def reset(self) -> None:
        self._aspect_lock = None
        self._preset_name = "Free (Custom)"
        self._rect_norm = QRectF(0.0, 0.0, 1.0, 1.0)
        self.update()
        self.cropChanged.emit(self.normalized_rect())

    def fit_to_canvas(self) -> None:
        """Fit and center the crop rectangle to the maximum bounds on the canvas.

        If an aspect ratio preset is active, maximizes that ratio within 0..1.
        If in Free mode, fills the entire canvas (0.0, 0.0, 1.0, 1.0).
        """
        if self._aspect_lock is not None and self._video_aspect > 0:
            norm_ar = self._aspect_lock / self._video_aspect
            if norm_ar <= 1.0:
                h = 1.0
                w = max(MIN_NORMALIZED, norm_ar)
            else:
                w = 1.0
                h = max(MIN_NORMALIZED, 1.0 / norm_ar)
            x = max(0.0, (1.0 - w) / 2.0)
            y = max(0.0, (1.0 - h) / 2.0)
            self._rect_norm = QRectF(x, y, w, h)
            self.update()
            self.cropChanged.emit(self.normalized_rect())
        else:
            self._rect_norm = QRectF(0.0, 0.0, 1.0, 1.0)
            self.update()
            self.cropChanged.emit(self.normalized_rect())

    def _video_display_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return QRectF(0, 0, 0, 0)
        widget_aspect = w / h
        if widget_aspect > self._video_aspect:
            actual_h = float(h)
            actual_w = h * self._video_aspect
            x = (w - actual_w) / 2
            y = 0.0
        else:
            actual_w = float(w)
            actual_h = w / self._video_aspect
            x = 0.0
            y = (h - actual_h) / 2
        return QRectF(x, y, actual_w, actual_h)

    def _crop_rect_widget(self) -> QRectF:
        v = self._video_display_rect()
        n = self._rect_norm
        return QRectF(
            v.x() + n.x() * v.width(),
            v.y() + n.y() * v.height(),
            n.width() * v.width(),
            n.height() * v.height(),
        )

    def _handle_centers(self, c: QRectF) -> dict[str, QPointF]:
        cx = (c.left() + c.right()) / 2
        cy = (c.top() + c.bottom()) / 2
        return {
            "tl": QPointF(c.left(), c.top()),
            "tr": QPointF(c.right(), c.top()),
            "bl": QPointF(c.left(), c.bottom()),
            "br": QPointF(c.right(), c.bottom()),
            "t":  QPointF(cx, c.top()),
            "b":  QPointF(cx, c.bottom()),
            "l":  QPointF(c.left(), cy),
            "r":  QPointF(c.right(), cy),
        }

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        if not self.isVisible():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        v = self._video_display_rect()
        c = self._crop_rect_widget()

        if self._fit_mode == "fill":
            dim = QColor(0, 0, 0, 150)
            if c.top() > v.top():
                p.fillRect(QRectF(v.left(), v.top(), v.width(), c.top() - v.top()), dim)
            if c.bottom() < v.bottom():
                p.fillRect(QRectF(v.left(), c.bottom(), v.width(), v.bottom() - c.bottom()), dim)
            p.fillRect(QRectF(v.left(), c.top(), c.left() - v.left(), c.height()), dim)
            p.fillRect(QRectF(c.right(), c.top(), v.right() - c.right(), c.height()), dim)

            thirds_pen = QPen(QColor(255, 255, 255, 80), 1, Qt.DashLine)
            p.setPen(thirds_pen)
            for i in (1, 2):
                x = c.left() + c.width() * i / 3
                p.drawLine(QPointF(x, c.top()), QPointF(x, c.bottom()))
                y = c.top() + c.height() * i / 3
                p.drawLine(QPointF(c.left(), y), QPointF(c.right(), y))

            border_pen = QPen(QColor("#5eead4"))
            border_pen.setWidth(2)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(c)

            p.setBrush(QColor("#5eead4"))
            p.setPen(QPen(QColor("#0d1216"), 1))
            s = HANDLE_SIZE
            for pt in self._handle_centers(c).values():
                p.drawRect(QRectF(pt.x() - s / 2, pt.y() - s / 2, s, s))
        else:
            # Fit (letterbox) or Stretch mode
            border_pen = QPen(QColor("#5eead4"), 2, Qt.DashLine if self._fit_mode == "fit" else Qt.SolidLine)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(v)

        # Render aspect ratio tag pill on top-left of crop box
        if self._preset_name and self._preset_name != "Free (Custom)":
            short_tag = self._preset_name.split(" ")[0]
            if self._fit_mode == "fit":
                short_tag += " (Fit)"
            elif self._fit_mode == "stretch":
                short_tag += " (Stretch)"
            from PySide6.QtGui import QFont
            p.setFont(QFont("Inter", 9, QFont.Bold))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(short_tag)
            badge_x = c.left() + 8 if self._fit_mode == "fill" else v.left() + 8
            badge_y = c.top() + 8 if self._fit_mode == "fill" else v.top() + 8
            badge_rect = QRectF(badge_x, badge_y, tw + 14, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(11, 16, 19, 210))
            p.drawRoundedRect(badge_rect, 4, 4)
            p.setPen(QColor("#5eead4"))
            p.drawText(badge_rect, Qt.AlignCenter, short_tag)

        p.end()

    def _hit_test(self, pos: QPointF) -> str | None:
        if self._fit_mode != "fill":
            return None
        c = self._crop_rect_widget()
        for name, center in self._handle_centers(c).items():
            if (abs(pos.x() - center.x()) <= HIT_PAD
                    and abs(pos.y() - center.y()) <= HIT_PAD):
                return name
        if c.contains(pos):
            return "move"
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        target = self._hit_test(event.position())
        if target:
            self._drag_target = target
            self._drag_start_widget = event.position()
            self._drag_start_rect = QRectF(self._rect_norm)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_target:
            self._apply_drag(event.position())
        else:
            self.setCursor(_cursor_for(self._hit_test(event.position())))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_target:
            self._drag_target = None
            self.cropChanged.emit(self.normalized_rect())

    def _apply_drag(self, pos: QPointF) -> None:
        v = self._video_display_rect()
        if v.width() <= 0 or v.height() <= 0 or self._drag_start_widget is None:
            return
        dx = (pos.x() - self._drag_start_widget.x()) / v.width()
        dy = (pos.y() - self._drag_start_widget.y()) / v.height()
        orig = QRectF(self._drag_start_rect)
        target = self._drag_target

        if target == "move":
            r = QRectF(orig)
            r.translate(dx, dy)
            self._rect_norm = self._clamp(r)
        elif self._aspect_lock is not None and self._video_aspect > 0:
            norm_ar = self._aspect_lock / self._video_aspect
            min_w = MIN_NORMALIZED * norm_ar if norm_ar >= 1 else MIN_NORMALIZED
            min_h = min_w / norm_ar

            if target == "br":
                max_w = min(1.0 - orig.left(), (1.0 - orig.top()) * norm_ar)
                delta_w = dx if abs(dx) >= abs(dy * norm_ar) else dy * norm_ar
                w = max(min_w, min(max_w, orig.width() + delta_w))
                h = w / norm_ar
                r = QRectF(orig.left(), orig.top(), w, h)
            elif target == "tl":
                max_w = min(orig.right(), orig.bottom() * norm_ar)
                delta_w = -dx if abs(dx) >= abs(dy * norm_ar) else -dy * norm_ar
                w = max(min_w, min(max_w, orig.width() + delta_w))
                h = w / norm_ar
                r = QRectF(orig.right() - w, orig.bottom() - h, w, h)
            elif target == "tr":
                max_w = min(1.0 - orig.left(), orig.bottom() * norm_ar)
                delta_w = dx if abs(dx) >= abs(dy * norm_ar) else -dy * norm_ar
                w = max(min_w, min(max_w, orig.width() + delta_w))
                h = w / norm_ar
                r = QRectF(orig.left(), orig.bottom() - h, w, h)
            elif target == "bl":
                max_w = min(orig.right(), (1.0 - orig.top()) * norm_ar)
                delta_w = -dx if abs(dx) >= abs(dy * norm_ar) else dy * norm_ar
                w = max(min_w, min(max_w, orig.width() + delta_w))
                h = w / norm_ar
                r = QRectF(orig.right() - w, orig.top(), w, h)
            elif target == "r":
                max_w = min(1.0 - orig.left(), norm_ar)
                w = max(min_w, min(max_w, orig.width() + dx))
                h = w / norm_ar
                cy = (orig.top() + orig.bottom()) / 2.0
                top = max(0.0, min(1.0 - h, cy - h / 2.0))
                r = QRectF(orig.left(), top, w, h)
            elif target == "l":
                max_w = min(orig.right(), norm_ar)
                w = max(min_w, min(max_w, orig.width() - dx))
                h = w / norm_ar
                cy = (orig.top() + orig.bottom()) / 2.0
                top = max(0.0, min(1.0 - h, cy - h / 2.0))
                r = QRectF(orig.right() - w, top, w, h)
            elif target == "b":
                max_h = min(1.0 - orig.top(), 1.0 / norm_ar)
                h = max(min_h, min(max_h, orig.height() + dy))
                w = h * norm_ar
                cx = (orig.left() + orig.right()) / 2.0
                left = max(0.0, min(1.0 - w, cx - w / 2.0))
                r = QRectF(left, orig.top(), w, h)
            elif target == "t":
                max_h = min(orig.bottom(), 1.0 / norm_ar)
                h = max(min_h, min(max_h, orig.height() - dy))
                w = h * norm_ar
                cx = (orig.left() + orig.right()) / 2.0
                left = max(0.0, min(1.0 - w, cx - w / 2.0))
                r = QRectF(left, orig.bottom() - h, w, h)
            else:
                r = orig

            self._rect_norm = self._clamp(r)
        else:
            r = QRectF(orig)
            if "l" in target:
                r.setLeft(min(r.right() - MIN_NORMALIZED, r.left() + dx))
            if "r" in target:
                r.setRight(max(r.left() + MIN_NORMALIZED, r.right() + dx))
            if "t" in target:
                r.setTop(min(r.bottom() - MIN_NORMALIZED, r.top() + dy))
            if "b" in target:
                r.setBottom(max(r.top() + MIN_NORMALIZED, r.bottom() + dy))
            self._rect_norm = self._clamp(r)

        self.update()

    def _clamp(self, r: QRectF) -> QRectF:
        if r.width() < MIN_NORMALIZED:
            r.setWidth(MIN_NORMALIZED)
        if r.height() < MIN_NORMALIZED:
            r.setHeight(MIN_NORMALIZED)
        if r.left() < 0:
            r.translate(-r.left(), 0)
        if r.top() < 0:
            r.translate(0, -r.top())
        if r.right() > 1:
            r.translate(1 - r.right(), 0)
        if r.bottom() > 1:
            r.translate(0, 1 - r.bottom())
        return QRectF(
            max(0.0, r.left()),
            max(0.0, r.top()),
            min(1.0 - max(0.0, r.left()), r.width()),
            min(1.0 - max(0.0, r.top()), r.height()),
        )


def _cursor_for(target: str | None) -> Qt.CursorShape:
    return {
        "move": Qt.SizeAllCursor,
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    }.get(target, Qt.ArrowCursor)
