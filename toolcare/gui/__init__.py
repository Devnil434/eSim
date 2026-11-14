"""
toolcare.gui - Graphical User Interface for eSim ToolCare.
Provides PyQt5/PyQt6-compatible interactive dialog, background worker thread,
and non-intrusive eSim menu hooks.
"""

from __future__ import annotations

import sys
from typing import Optional

from toolcare.gui.qt_compat import (
    QApplication,
    get_qt_binding_name,
    is_qt_available,
)

# Lazy/conditional imports to ensure importing toolcare.gui doesn't crash if Qt is missing
try:
    from toolcare.gui.worker import WorkerThread
    from toolcare.gui.dialog import ToolCareDialog
    from toolcare.gui.hooks import attach_toolcare_to_menu
except ImportError:
    WorkerThread = None  # type: ignore
    ToolCareDialog = None  # type: ignore
    attach_toolcare_to_menu = None  # type: ignore


def launch_gui(parent=None) -> int:
    """
    Launch the standalone ToolCare GUI dialog.
    If PyQt is not installed, prints a clear remediation message and exits cleanly with 1.
    """
    if not is_qt_available():
        print(
            "Error: PyQt (PyQt5 or PyQt6) is not installed on this system.\n\n"
            "To launch the ToolCare graphical management interface, please install PyQt:\n"
            "  pip install PyQt5    (or: pip install PyQt6)\n\n"
            "Alternatively, use the full-featured command-line interface:\n"
            "  python -m toolcare --help\n"
            "  python -m toolcare doctor\n"
            "  python -m toolcare update --check",
            file=sys.stderr,
        )
        return 1

    app = QApplication.instance()
    owns_app = False
    if not app:
        app = QApplication(sys.argv)
        owns_app = True

    from toolcare.gui.dialog import ToolCareDialog

    if parent is not None and hasattr(parent, "children"):
        for child in parent.children():
            if isinstance(child, ToolCareDialog):
                child.show()
                child.raise_()
                child.activateWindow()
                return 0

    dialog = ToolCareDialog(parent=parent)
    dialog.show()

    if owns_app:
        return app.exec_() if hasattr(app, "exec_") else app.exec()
    return 0


__all__ = [
    "launch_gui",
    "ToolCareDialog",
    "WorkerThread",
    "attach_toolcare_to_menu",
    "is_qt_available",
    "get_qt_binding_name",
]
