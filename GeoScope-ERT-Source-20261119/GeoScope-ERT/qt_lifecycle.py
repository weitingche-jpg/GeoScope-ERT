"""Keep Windows accessibility callbacks outside transient native reparenting."""
from contextlib import contextmanager
from PySide6 import QtGui


@contextmanager
def native_widget_transition():
    active=QtGui.QAccessible.isActive()
    QtGui.QAccessible.setActive(False)
    try:
        yield
    finally:
        QtGui.QAccessible.setActive(active)
