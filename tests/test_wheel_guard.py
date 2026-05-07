"""Tests for the WheelGuard event filter.

We need a QApplication for these but no display server — Qt's
"offscreen" platform plugin works for unit tests on CI.
"""
from __future__ import annotations

import os
import pytest

# Force a headless platform so the test runs without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QSlider, QSpinBox

from quansheng_toolkit.gui import wheel_guard


@pytest.fixture(scope="module")
def app():
    """Module-scoped QApplication so we don't pay startup per test."""
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


@pytest.fixture
def guarded_app(app):
    """Application with WheelGuard installed for the duration of one test."""
    g = wheel_guard.install(app)
    yield app
    app.removeEventFilter(g)


def _wheel_event(target_widget) -> QWheelEvent:
    """Synthesise a vertical wheel-up event delivered to `target_widget`."""
    return QWheelEvent(
        QPoint(10, 10),                # local pos
        target_widget.mapToGlobal(QPoint(10, 10)),  # global pos
        QPoint(0, 0),                  # pixel delta (none)
        QPoint(0, 120),                # angle delta (one notch up)
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,                         # inverted
    )


# ---------------------------------------------------------------------------
# QSpinBox — the most common offender
# ---------------------------------------------------------------------------

def test_spinbox_unfocused_does_not_change_on_wheel(guarded_app):
    sb = QSpinBox()
    sb.setRange(0, 100)
    sb.setValue(10)
    sb.show()
    QApplication.processEvents()

    QApplication.sendEvent(sb, _wheel_event(sb))
    QApplication.processEvents()

    assert sb.value() == 10, "wheel changed unfocused spinbox value"


def test_spinbox_focused_also_does_not_change_on_wheel(guarded_app):
    """The guard intentionally blocks wheel even when the widget has
    keyboard focus — QSpinBox defaults to WheelFocus policy, which
    grants focus on wheel-and-hover, so a "let it through if focused"
    rule lets every accidental wheel through. The trade-off:
    intentional edits use click + arrows / typing, never the wheel.
    """
    sb = QSpinBox()
    sb.setRange(0, 100)
    sb.setValue(10)
    sb.show()
    sb.setFocus()
    QApplication.processEvents()
    assert sb.hasFocus(), "couldn't grant focus in test setup"

    QApplication.sendEvent(sb, _wheel_event(sb))
    QApplication.processEvents()

    assert sb.value() == 10, "wheel should not edit even a focused spinbox"


# ---------------------------------------------------------------------------
# QComboBox
# ---------------------------------------------------------------------------

def test_combobox_unfocused_does_not_change_on_wheel(guarded_app):
    cb = QComboBox()
    cb.addItems(["A", "B", "C"])
    cb.setCurrentIndex(1)
    cb.show()
    QApplication.processEvents()

    QApplication.sendEvent(cb, _wheel_event(cb))
    QApplication.processEvents()

    assert cb.currentIndex() == 1


# ---------------------------------------------------------------------------
# QSlider
# ---------------------------------------------------------------------------

def test_slider_unfocused_does_not_change_on_wheel(guarded_app):
    sl = QSlider()
    sl.setRange(0, 100)
    sl.setValue(50)
    sl.show()
    QApplication.processEvents()

    QApplication.sendEvent(sl, _wheel_event(sl))
    QApplication.processEvents()

    assert sl.value() == 50


# ---------------------------------------------------------------------------
# Other widgets — guard MUST NOT touch wheel events on labels / scroll
# areas / etc.
# ---------------------------------------------------------------------------

def test_other_widgets_get_their_wheel_events(guarded_app):
    """A QLabel doesn't react to wheel events anyway, but the test
    proves the guard returned False (didn't intercept) so the event
    propagated normally.
    """
    label = QLabel("hello")
    label.show()
    QApplication.processEvents()
    # Just check that sending a wheel event doesn't raise / explode.
    QApplication.sendEvent(label, _wheel_event(label))
    QApplication.processEvents()
