"""
base_widgets.py — Primitive, reusable Qt widget extensions for HyVis.

Includes:
  - FloatSpinBox: QDoubleSpinBox wired to global preferences for live decimals and stepping.
  - SuggestionComboBox: QComboBox with StrongFocus and popup notification.
  - SmoothScrollArea: Momentum-based easing scroll area with safe wheel routing.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
    QScrollBar,
    QTableWidget,
    QTextEdit,
    QWidget,
)

from hyvis.gui.settings import get_gui_settings, settings_signals

# region FloatSpinBox


class FloatSpinBox(QDoubleSpinBox):
    """
    A specialized QDoubleSpinBox wired to global GUI preferences for precision and stepping.
    Automatically subscribes to settings_signals for instant live updates.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        extra_decimals: int = 0,
        step_scale: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self._extra_decimals = extra_decimals
        self._step_scale = step_scale
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Initialize from current persistent preferences
        settings = get_gui_settings()
        self._apply_format(settings.spinbox_decimals, settings.spinbox_step)

        # Subscribe to live preference broadcasts
        settings_signals.spinbox_format_changed.connect(self._apply_format)

    def _apply_format(self, decimals: int, step: float) -> None:
        eff_decimals = decimals + self._extra_decimals
        self.setDecimals(eff_decimals)
        eff_step = round(step * self._step_scale, eff_decimals)
        self.setSingleStep(max(10 ** (-eff_decimals), eff_step))


# endregion


# region AutoResizingTableWidget


# todo behaviour of height size stuff is weird
class AutoResizingTableWidget(QTableWidget):
    """
    QTableWidget with clean header styling that automatically recalculates
    its height as rows are inserted, removed, or populated.
    """

    def __init__(
        self,
        rows: int = 0,
        columns: int = 0,
        min_height: int = 86,
        max_height: int = 210,
        row_height: int = 27,
        header_height: int = 30,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(rows, columns, parent)
        self._min_height = min_height
        self._max_height = max_height
        self._row_height = row_height
        self._header_height = header_height

        self.verticalHeader().setDefaultSectionSize(self._row_height)
        self.adjust_table_height()

    def adjust_table_height(self) -> None:
        rows = self.rowCount()
        needed = self._header_height + max(rows, 1) * self._row_height
        target = max(self._min_height, min(needed, self._max_height))
        self.setFixedHeight(target)

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        self.adjust_table_height()

    def insertRow(self, row: int) -> None:
        super().insertRow(row)
        self.adjust_table_height()

    def removeRow(self, row: int) -> None:
        super().removeRow(row)
        self.adjust_table_height()


# endregion


# region SuggestionComboBox


class SuggestionComboBox(QComboBox):
    """QComboBox that notifies listeners right before showing its dropdown popup and enforces StrongFocus."""

    about_to_show_popup = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Never allow mouse wheel to steal focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showPopup(self) -> None:
        self.about_to_show_popup.emit()
        super().showPopup()


# endregion


# region AutoResizingTextEdit


class AutoResizingTextEdit(QTextEdit):
    """
    QTextEdit with automatic word-wrapping that dynamically resizes
    its height to fit all content without showing vertical scrollbars.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().documentLayout().documentSizeChanged.connect(self._adjust_height)
        self.textChanged.connect(self._adjust_height)

    def _adjust_height(self) -> None:
        vp = self.viewport()
        if vp and vp.width() > 0:
            self.document().setTextWidth(vp.width())
        doc_h = int(self.document().documentLayout().documentSize().height())
        m = self.contentsMargins()
        total_h = doc_h + m.top() + m.bottom()
        self.setFixedHeight(max(total_h, 36))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adjust_height()


# endregion


# region SmoothScroll Driver


class SmoothScrollDriver(QObject):
    """
    Single source of truth for smooth scroll momentum physics and animations.
    Controls a target QScrollBar using cubic easing and accumulated wheel deltas.
    """

    DURATION_MS: int = 260
    STEP_MULTIPLIER: float = 5.25

    def __init__(self, scrollbar: QScrollBar, parent: QObject | None = None) -> None:
        super().__init__(parent or scrollbar)
        self._scrollbar = scrollbar
        self._target_value: float = 0.0

        self._anim = QPropertyAnimation(self._scrollbar, b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(self.DURATION_MS)

    def is_running(self) -> bool:
        """Return True if a scroll animation is actively in progress."""
        return self._anim.state() == QPropertyAnimation.State.Running

    def scroll_by_delta(self, delta: int) -> bool:
        """
        Process a vertical wheel delta and animate the scrollbar.
        Returns False immediately on horizontal scroll (delta == 0) or boundary limits.
        """
        # Strictly ignore horizontal-only wheel / touchpad events
        if delta == 0:
            return False

        vbar = self._scrollbar
        if vbar.maximum() <= vbar.minimum():
            return False

        current = vbar.value()
        # If already animating, accumulate distance from active target for continuous momentum
        if self.is_running():
            pass
        else:
            self._target_value = float(current)

        single = vbar.singleStep()
        base_step = single if 10 <= single <= 40 else 20
        step = base_step * self.STEP_MULTIPLIER * (delta / 120.0)

        self._target_value = max(
            float(vbar.minimum()),
            min(self._target_value - step, float(vbar.maximum())),
        )

        self._anim.stop()
        self._anim.setStartValue(current)
        self._anim.setEndValue(round(self._target_value))
        self._anim.start()
        return True


# endregion


# region SmoothScrollArea


class SmoothScrollArea(QScrollArea):
    """
    Momentum-based smooth scrolling area for widget layouts (pages & dialogs).
    Uses SmoothScrollDriver for animations while enforcing proactive Safe-Scroll
    and optional dynamic right margins.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scroll_margin: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._scroll_margin = scroll_margin
        self._driver = SmoothScrollDriver(self.verticalScrollBar(), parent=self)

        self.verticalScrollBar().installEventFilter(self)
        if self._scroll_margin is not None:
            self.verticalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)

        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self._neutralize_child_wheel_focus(widget)
        self._update_dynamic_margin()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        w = self.widget()
        if w:
            self._neutralize_child_wheel_focus(w)
            self._update_dynamic_margin()

    def _on_scrollbar_range_changed(self, min_val: int, max_val: int) -> None:
        del min_val, max_val
        self._update_dynamic_margin()

    def _update_dynamic_margin(self) -> None:
        if self._scroll_margin is None:
            return
        w = self.widget()
        if not w or not w.layout():
            return

        vbar = self.verticalScrollBar()
        has_scrollbar = vbar.maximum() > vbar.minimum()
        target_right = self._scroll_margin if has_scrollbar else 0

        m = w.layout().contentsMargins()
        if m.right() != target_right:
            w.layout().setContentsMargins(m.left(), m.top(), target_right, m.bottom())

    def _neutralize_child_wheel_focus(self, container: QWidget) -> None:
        for spin in container.findChildren(QAbstractSpinBox):
            if spin.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        for combo in container.findChildren(QComboBox):
            if combo.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            # Ignore horizontal wheel events entirely
            if event.angleDelta().y() == 0:
                return super().eventFilter(obj, event)

            if obj == self.verticalScrollBar():
                self.wheelEvent(event)
                return True

            if isinstance(obj, QWidget) and self.isAncestorOf(obj):
                target_input: QWidget | None = None
                curr: QObject | None = obj
                while curr is not None and isinstance(curr, QWidget) and self.isAncestorOf(curr):
                    if isinstance(curr, (QAbstractSpinBox, QComboBox)):
                        target_input = curr
                        break
                    curr = curr.parent()

                if target_input is not None:
                    if target_input.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                        target_input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

                    if isinstance(target_input, QComboBox) and not target_input.isEditable():
                        self.wheelEvent(event)
                        return True

                    active_focus = QApplication.focusWidget()
                    is_focused = active_focus is not None and (
                        active_focus == target_input or target_input.isAncestorOf(active_focus)
                    )

                    if not is_focused:
                        self.wheelEvent(event)
                        return True
                    else:
                        return False

                # Restored: only intercept in-flight animations; otherwise let Qt dispatch naturally
                if self._driver.is_running():
                    self.wheelEvent(event)
                    return True

        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if self._driver.scroll_by_delta(delta):
            event.accept()
        else:
            super().wheelEvent(event)


# endregion


# region SmoothItemScrollFilter & Helper


class SmoothItemScrollFilter(QObject):
    """
    Event filter that enables pixel-based momentum smooth scrolling
    on any QAbstractItemView (QListWidget, QTableWidget, QTreeWidget, etc.).
    Delegates all animation physics to SmoothScrollDriver.
    """

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        self._view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._driver = SmoothScrollDriver(self._view.verticalScrollBar(), parent=self)

        self._view.viewport().installEventFilter(self)
        self._view.verticalScrollBar().installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            delta = event.angleDelta().y()
            if self._driver.scroll_by_delta(delta):
                event.accept()
                return True
        return super().eventFilter(obj, event)


def install_smooth_scroll(view: QAbstractItemView) -> SmoothItemScrollFilter:
    """Attach smooth pixel-level momentum scrolling to any list, table, or tree view."""
    filt = SmoothItemScrollFilter(view)
    view._smooth_scroll_filter = filt
    return filt


# endregion
