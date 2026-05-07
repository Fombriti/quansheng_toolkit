"""
Wheel guard — stop QSpinBox/QComboBox/QSlider from eating the mouse
wheel when the user is just scrolling the page.

Default Qt behaviour: hovering over any of these widgets and turning
the wheel changes their value. That's friendly when you mean to edit
the widget, but hostile when you're scrolling a long form (Settings,
Toolkit, Calibration…) — any spinbox or dropdown along the cursor's
path silently increments. Users notice this only after committing
unintended edits to the radio.

Strategy: install a single QObject event filter on the QApplication
that watches Wheel events. When the target is one of the offending
widget classes AND it doesn't have keyboard focus, we drop the event
and pass it to the parent scroll area instead.

The widget can still react to the wheel as long as it's actively
focused (clicked into it, tabbed there, etc.). That preserves
intentional editing while killing accidental scroll-edits.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QScrollBar,
    QWidget,
)


# Widget classes whose wheelEvent we want to suppress.
_GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)

# Subclasses of guarded types that we DO want to keep responsive to
# the wheel. QScrollBar is the obvious one — it inherits QAbstractSlider
# but is exactly the widget that should consume wheel events to make
# the page scroll. Without this exclusion the entire app stops
# scrolling when the cursor is near the right edge of any scroll area.
_EXCLUDED = (QScrollBar,)


def _find_guarded_ancestor(widget: QObject) -> QObject | None:
    """Walk up the parent chain looking for a guarded widget.

    QSpinBox/QComboBox embed a QLineEdit child that receives the wheel
    event before the parent — looking only at the immediate target
    misses these cases.
    """
    cur = widget
    while cur is not None:
        if isinstance(cur, _GUARDED):
            return cur
        # parentWidget() is the safe accessor for widgets; non-widget
        # ancestors don't matter for our purposes.
        if isinstance(cur, QWidget):
            cur = cur.parentWidget()
        else:
            return None
    return None


class WheelGuard(QObject):
    """Application-level event filter that prevents wheel-driven edits
    on spinboxes / combos / sliders.

    The previous "only block when unfocused" version didn't work in
    practice: QSpinBox / QComboBox default to `Qt.WheelFocus` focus
    policy, which means Qt grants focus to the widget UPON the wheel
    event before delivering it. By the time our filter runs,
    `hasFocus()` is already True and we'd let the event through.

    Pragmatic fix: block the wheel UNCONDITIONALLY on these widgets.
    The user changes values by clicking, typing, or pressing the
    up/down arrow keys after focusing — never by wheeling. That
    matches the intent of the guard (no accidental edits while
    scrolling a long form).

    Install once with `app.installEventFilter(WheelGuard(app))`.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        guarded = _find_guarded_ancestor(obj)
        if guarded is None or isinstance(guarded, _EXCLUDED):
            return False
        # Downgrade the focus policy so the wheel doesn't grant focus
        # in the first place — Qt's default Qt.WheelFocus turns
        # wheel-and-hover into a focus event before the wheel itself
        # is delivered, which is what made the spinbox look like it
        # was being "selected" by scrolling.
        if guarded.focusPolicy() == Qt.FocusPolicy.WheelFocus:
            guarded.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Swallow. Wheel never edits a value; this is intentional.
        event.accept()
        return True


def install(app: QObject) -> WheelGuard:
    """Convenience: build the filter, install it on `app`, return it.

    Caller must keep the returned object alive (a member on QApplication
    is the usual pattern) — Qt does NOT take ownership of an event
    filter, and a garbage-collected filter would silently stop
    working.
    """
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
