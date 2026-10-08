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
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
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


# region SmoothScrollArea


class SmoothScrollArea(QScrollArea):
    """
    Momentum-based smooth scrolling area with proactive Safe-Scroll filtering.

    Safe-Scroll Rules:
      1. Proactively strips WheelFocus from all child spinboxes and comboboxes,
         preventing the mouse wheel from auto-focusing them on first touch.
      2. Non-editable QComboBox: Wheel never cycles options; smoothly scrolls the page.
      3. Spinboxes & Editable ComboBoxes: Wheel only changes value if explicitly clicked/focused;
         otherwise smoothly scrolls the page.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(300)
        self._target_value = 0.0

        self.verticalScrollBar().installEventFilter(self)

        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self._neutralize_child_wheel_focus(widget)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        w = self.widget()
        if w:
            self._neutralize_child_wheel_focus(w)

    def _neutralize_child_wheel_focus(self, container: QWidget) -> None:
        for spin in container.findChildren(QAbstractSpinBox):
            if spin.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        for combo in container.findChildren(QComboBox):
            if combo.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
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

                if self._anim.state() == QPropertyAnimation.State.Running:
                    self.wheelEvent(event)
                    return True

        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        vbar = self.verticalScrollBar()
        if self._anim.state() != QPropertyAnimation.State.Running:
            self._target_value = vbar.value()

        step = vbar.singleStep() * 5.25 * (delta / 120.0)
        self._target_value -= step
        self._target_value = max(vbar.minimum(), min(self._target_value, vbar.maximum()))

        self._anim.stop()
        self._anim.setStartValue(vbar.value())
        self._anim.setEndValue(self._target_value)
        self._anim.start()

        event.accept()


# endregion
