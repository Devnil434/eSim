"""
toolcare.gui.qt_compat - Unified Qt abstraction layer supporting PyQt6, PyQt5,
and safe headless mock execution when Qt is not installed.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional

QT_BINDING: Optional[str] = None
IS_QT_AVAILABLE = False
IS_MOCK_QT = False


def _detect_and_import_qt():
    """
    Detect PyQt6 or PyQt5, prioritizing whichever is already loaded by the host process.
    """
    global QT_BINDING, IS_QT_AVAILABLE

    # 1. Check if host already loaded a binding
    if "PyQt6.QtCore" in sys.modules:
        try:
            import PyQt6.QtCore as QtCore
            import PyQt6.QtGui as QtGui
            import PyQt6.QtWidgets as QtWidgets
            QT_BINDING = "PyQt6"
            IS_QT_AVAILABLE = True
            return QtCore, QtGui, QtWidgets
        except ImportError:
            pass

    if "PyQt5.QtCore" in sys.modules:
        try:
            import PyQt5.QtCore as QtCore
            import PyQt5.QtGui as QtGui
            import PyQt5.QtWidgets as QtWidgets
            QT_BINDING = "PyQt5"
            IS_QT_AVAILABLE = True
            return QtCore, QtGui, QtWidgets
        except ImportError:
            pass

    # 2. Try importing PyQt5 (eSim's primary native binding)
    try:
        import PyQt5.QtCore as QtCore
        import PyQt5.QtGui as QtGui
        import PyQt5.QtWidgets as QtWidgets
        QT_BINDING = "PyQt5"
        IS_QT_AVAILABLE = True
        return QtCore, QtGui, QtWidgets
    except ImportError:
        pass

    # 3. Try importing PyQt6
    try:
        import PyQt6.QtCore as QtCore
        import PyQt6.QtGui as QtGui
        import PyQt6.QtWidgets as QtWidgets
        QT_BINDING = "PyQt6"
        IS_QT_AVAILABLE = True
        return QtCore, QtGui, QtWidgets
    except ImportError:
        pass

    return None, None, None


# Check for test mock override or attempt real import
_USE_MOCK = os.environ.get("TOOLCARE_TEST_MOCK_QT") == "1"

if not _USE_MOCK:
    _QtCore, _QtGui, _QtWidgets = _detect_and_import_qt()
else:
    _QtCore, _QtGui, _QtWidgets = None, None, None


# -----------------------------------------------------------------------------
# Real Qt Binding Layer
# -----------------------------------------------------------------------------
if IS_QT_AVAILABLE and _QtCore and _QtGui and _QtWidgets:
    QtCore = _QtCore
    QtGui = _QtGui
    QtWidgets = _QtWidgets

    QApplication = QtWidgets.QApplication
    QDialog = QtWidgets.QDialog
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QGridLayout = QtWidgets.QGridLayout
    QTabWidget = QtWidgets.QTabWidget
    QTableWidget = QtWidgets.QTableWidget
    QTableWidgetItem = QtWidgets.QTableWidgetItem
    QTreeWidget = QtWidgets.QTreeWidget
    QTreeWidgetItem = QtWidgets.QTreeWidgetItem
    QPushButton = QtWidgets.QPushButton
    QLabel = QtWidgets.QLabel
    QProgressBar = QtWidgets.QProgressBar
    QTextEdit = QtWidgets.QTextEdit
    QMessageBox = QtWidgets.QMessageBox
    QHeaderView = QtWidgets.QHeaderView
    QMenu = QtWidgets.QMenu
    QMenuBar = QtWidgets.QMenuBar
    QSplitter = QtWidgets.QSplitter
    QGroupBox = QtWidgets.QGroupBox

    QThread = QtCore.QThread
    pyqtSignal = QtCore.pyqtSignal
    Qt = QtCore.Qt

    # Normalize QAction location
    if QT_BINDING == "PyQt6":
        QAction = QtGui.QAction
        AlignmentCenter = Qt.AlignmentFlag.AlignCenter
        AlignmentLeft = Qt.AlignmentFlag.AlignLeft
        AlignmentRight = Qt.AlignmentFlag.AlignRight
        HeaderResizeStretch = QHeaderView.ResizeMode.Stretch
        HeaderResizeContents = QHeaderView.ResizeMode.ResizeToContents
        MessageBoxYes = QMessageBox.StandardButton.Yes
        MessageBoxNo = QMessageBox.StandardButton.No
        MessageBoxOk = QMessageBox.StandardButton.Ok
    else:
        QAction = QtWidgets.QAction
        AlignmentCenter = Qt.AlignCenter
        AlignmentLeft = Qt.AlignLeft
        AlignmentRight = Qt.AlignRight
        HeaderResizeStretch = QHeaderView.Stretch
        HeaderResizeContents = QHeaderView.ResizeToContents
        MessageBoxYes = QMessageBox.Yes
        MessageBoxNo = QMessageBox.No
        MessageBoxOk = QMessageBox.Ok

# -----------------------------------------------------------------------------
# Headless Mock Qt Layer (For offline Python test environments without PyQt)
# -----------------------------------------------------------------------------
else:
    IS_MOCK_QT = _USE_MOCK
    QT_BINDING = "MockQt" if _USE_MOCK else None

    class _MockSignal:
        def __init__(self, *types):
            self.types = types
            self._slots = []
            self._instances: Dict[int, _MockSignal] = {}

        def __get__(self, instance, owner):
            if instance is None:
                return self
            inst_id = id(instance)
            if inst_id not in self._instances:
                self._instances[inst_id] = _MockSignal(*self.types)
            return self._instances[inst_id]

        def connect(self, slot: Callable):
            if slot not in self._slots:
                self._slots.append(slot)

        def disconnect(self, slot: Optional[Callable] = None):
            if slot is None:
                self._slots.clear()
            elif slot in self._slots:
                self._slots.remove(slot)

        def emit(self, *args):
            for slot in list(self._slots):
                try:
                    slot(*args)
                except Exception as e:
                    # Correction 6: Do not silently swallow slot exceptions
                    raise e

    def pyqtSignal(*types):
        return _MockSignal(*types)

    class QObject:
        def __init__(self, parent=None):
            self._parent = parent
            self._object_name = ""
            self._children = []
            if parent and hasattr(parent, "_children"):
                parent._children.append(self)

        def parent(self):
            return self._parent

        def setObjectName(self, name: str):
            self._object_name = name

        def objectName(self) -> str:
            return self._object_name

        def findChildren(self, widget_class, name=None):
            res = []
            for c in getattr(self, "_children", []):
                if isinstance(c, widget_class):
                    if name is None or getattr(c, "objectName", lambda: "")() == name:
                        res.append(c)
                if hasattr(c, "findChildren"):
                    res.extend(c.findChildren(widget_class, name))
            return res

    class QThread(QObject):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._is_running = False

        def start(self):
            self._is_running = True
            # In mock environment, execute synchronously for reliable test assertions
            try:
                self.run()
            finally:
                self._is_running = False

        def run(self):
            pass

        def wait(self, msecs=None):
            pass

        def isRunning(self) -> bool:
            return self._is_running

    class QWidget(QObject):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._layout = None
            self._children = []
            self._enabled = True
            self._visible = True
            self._tooltip = ""
            if parent:
                parent._children.append(self)

        def setLayout(self, layout):
            self._layout = layout

        def layout(self):
            return self._layout

        def setEnabled(self, enabled: bool):
            self._enabled = enabled

        def isEnabled(self) -> bool:
            return self._enabled

        def setVisible(self, visible: bool):
            self._visible = visible

        def isVisible(self) -> bool:
            return self._visible

        def setToolTip(self, tooltip: str):
            self._tooltip = tooltip

        def toolTip(self) -> str:
            return self._tooltip

        def show(self):
            self._visible = True

        def close(self):
            self._visible = False
            return True

        def closeEvent(self, event):
            event.accept()

        def setMinimumSize(self, w, h):
            pass

        def resize(self, w, h):
            pass

        def setWindowTitle(self, title: str):
            self._window_title = title

        def windowTitle(self) -> str:
            return getattr(self, "_window_title", "")

        def findChildren(self, widget_class, name=None):
            res = []
            for c in self._children:
                if isinstance(c, widget_class):
                    if name is None or c.objectName() == name:
                        res.append(c)
                res.extend(c.findChildren(widget_class, name))
            return res

    class QDialog(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._result = 0

        def exec_(self) -> int:
            self.show()
            return self._result

        def exec(self) -> int:
            return self.exec_()

        def accept(self):
            self._result = 1
            self.close()

        def reject(self):
            self._result = 0
            self.close()

    class QMainWindow(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._menu_bar = None

        def menuBar(self):
            if not self._menu_bar:
                self._menu_bar = QMenuBar(self)
            return self._menu_bar

    class QAction(QObject):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._text = text
            self.triggered = _MockSignal()
            self._shortcut = ""
            self._menu = None
            if parent and hasattr(parent, "_children"):
                parent._children.append(self)

        def text(self) -> str:
            return self._text

        def setText(self, text: str):
            self._text = text

        def setShortcut(self, shortcut: str):
            self._shortcut = shortcut

        def setIconVisibleInMenu(self, visible: bool):
            pass

        def menu(self):
            return self._menu

    class QMenu(QWidget):
        def __init__(self, title="", parent=None):
            super().__init__(parent)
            self._title = title
            self._actions: List[QAction] = []

        def addAction(self, action_or_text):
            if isinstance(action_or_text, str):
                act = QAction(action_or_text, self)
                self._actions.append(act)
                return act
            self._actions.append(action_or_text)
            return action_or_text

        def addSeparator(self):
            pass

        def actions(self) -> List[QAction]:
            return list(self._actions)

        def title(self) -> str:
            return self._title

    class QMenuBar(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._menus: List[QMenu] = []

        def addMenu(self, title_or_menu):
            if isinstance(title_or_menu, str):
                m = QMenu(title_or_menu, self)
                self._menus.append(m)
                return m
            self._menus.append(title_or_menu)
            return title_or_menu

        def actions(self) -> List[QAction]:
            acts = []
            for m in self._menus:
                act = QAction(m.title(), self)
                act._menu = m
                acts.append(act)
            return acts

    class QLayout:
        def __init__(self, parent=None):
            self._widgets = []
            if parent:
                parent.setLayout(self)

        def addWidget(self, widget):
            self._widgets.append(widget)

        def addLayout(self, layout):
            self._widgets.append(layout)

        def addStretch(self, stretch=0):
            pass

    class QVBoxLayout(QLayout):
        pass

    class QHBoxLayout(QLayout):
        pass

    class QGridLayout(QLayout):
        pass

    class QTabWidget(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._tabs: List[tuple[QWidget, str]] = []
            self._current_index = 0

        def addTab(self, widget: QWidget, title: str):
            self._tabs.append((widget, title))
            self._children.append(widget)

        def count(self) -> int:
            return len(self._tabs)

        def tabText(self, index: int) -> str:
            return self._tabs[index][1] if 0 <= index < len(self._tabs) else ""

        def currentIndex(self) -> int:
            return self._current_index

        def setCurrentIndex(self, index: int):
            self._current_index = index

    class QTableWidgetItem:
        def __init__(self, text=""):
            self._text = str(text)

        def text(self) -> str:
            return self._text

        def setText(self, text: str):
            self._text = str(text)

        def setTextAlignment(self, align):
            pass

    class QTableWidget(QWidget):
        def __init__(self, rows=0, cols=0, parent=None):
            super().__init__(parent)
            self._rows = rows
            self._cols = cols
            self._headers = []
            self._items: Dict[tuple[int, int], QTableWidgetItem] = {}
            self._selected_rows = []

        def setColumnCount(self, count: int):
            self._cols = count

        def columnCount(self) -> int:
            return self._cols

        def setRowCount(self, count: int):
            self._rows = count

        def rowCount(self) -> int:
            return self._rows

        def setHorizontalHeaderLabels(self, labels: List[str]):
            self._headers = list(labels)

        def setItem(self, row: int, col: int, item: QTableWidgetItem):
            self._items[(row, col)] = item

        def item(self, row: int, col: int) -> Optional[QTableWidgetItem]:
            return self._items.get((row, col))

        def currentRow(self) -> int:
            return self._selected_rows[0] if self._selected_rows else -1

        def setCurrentRow(self, row: int):
            self._selected_rows = [row]

        def horizontalHeader(self):
            return QHeaderView()

        def setSelectionBehavior(self, b):
            pass

        def setSelectionMode(self, m):
            pass

        def setEditTriggers(self, t):
            pass

    class QTreeWidgetItem:
        def __init__(self, strings: Optional[List[str]] = None):
            self._strings = strings or []
            self._children: List[QTreeWidgetItem] = []

        def text(self, col: int) -> str:
            return self._strings[col] if col < len(self._strings) else ""

        def setText(self, col: int, text: str):
            while len(self._strings) <= col:
                self._strings.append("")
            self._strings[col] = text

        def addChild(self, item: QTreeWidgetItem):
            self._children.append(item)

        def childCount(self) -> int:
            return len(self._children)

        def child(self, index: int) -> Optional[QTreeWidgetItem]:
            return self._children[index] if 0 <= index < len(self._children) else None

    class QTreeWidget(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._headers = []
            self._items: List[QTreeWidgetItem] = []

        def setHeaderLabels(self, labels: List[str]):
            self._headers = list(labels)

        def addTopLevelItem(self, item: QTreeWidgetItem):
            self._items.append(item)

        def topLevelItemCount(self) -> int:
            return len(self._items)

        def topLevelItem(self, index: int) -> Optional[QTreeWidgetItem]:
            return self._items[index] if 0 <= index < len(self._items) else None

        def clear(self):
            self._items.clear()

        def expandAll(self):
            pass

    class QPushButton(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._text = text
            self.clicked = _MockSignal()

        def text(self) -> str:
            return self._text

        def setText(self, text: str):
            self._text = text

    class QLabel(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._text = text

        def text(self) -> str:
            return self._text

        def setText(self, text: str):
            self._text = text

        def setAlignment(self, align):
            pass

        def setWordWrap(self, wrap: bool):
            pass

    class QProgressBar(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._val = 0
            self._min = 0
            self._max = 100

        def setRange(self, min_val: int, max_val: int):
            self._min = min_val
            self._max = max_val

        def setValue(self, val: int):
            self._val = val

        def value(self) -> int:
            return self._val

    class QTextEdit(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._text = ""
            self._read_only = False

        def setReadOnly(self, ro: bool):
            self._read_only = ro

        def setPlainText(self, text: str):
            self._text = text

        def toPlainText(self) -> str:
            return self._text

        def append(self, text: str):
            self._text += ("\n" if self._text else "") + text

        def clear(self):
            self._text = ""

    class QHeaderView:
        SelectRows = 1
        SingleSelection = 1
        NoEditTriggers = 0
        Stretch = 0
        ResizeToContents = 1

        def setSectionResizeMode(self, *args):
            pass

    class QGroupBox(QWidget):
        def __init__(self, title="", parent=None):
            super().__init__(parent)
            self._title = title

    class QSplitter(QWidget):
        pass

    class QMessageBox:
        Yes = 1
        No = 2
        Ok = 3
        _next_response = Yes

        @classmethod
        def set_mock_response(cls, response: int):
            cls._next_response = response

        @classmethod
        def question(cls, parent, title, message, buttons=Yes | No, defaultButton=No):
            return cls._next_response

        @classmethod
        def information(cls, parent, title, message):
            return cls.Ok

        @classmethod
        def warning(cls, parent, title, message):
            return cls.Ok

        @classmethod
        def critical(cls, parent, title, message):
            return cls.Ok

    class _CloseEvent:
        def __init__(self):
            self._accepted = True

        def accept(self):
            self._accepted = True

        def ignore(self):
            self._accepted = False

        def isAccepted(self) -> bool:
            return self._accepted

    class QApplication:
        _instance = None

        def __init__(self, argv=None):
            QApplication._instance = self

        @classmethod
        def instance(cls):
            return cls._instance

        def exec_(self) -> int:
            return 0

        def exec(self) -> int:
            return 0

        def processEvents(self):
            pass

    class _QtConstants:
        AlignCenter = 1
        AlignLeft = 2
        AlignRight = 3
        ItemIsEnabled = 1
        ItemIsSelectable = 2

    Qt = _QtConstants
    AlignmentCenter = Qt.AlignCenter
    AlignmentLeft = Qt.AlignLeft
    AlignmentRight = Qt.AlignRight
    HeaderResizeStretch = QHeaderView.Stretch
    HeaderResizeContents = QHeaderView.ResizeToContents
    MessageBoxYes = QMessageBox.Yes
    MessageBoxNo = QMessageBox.No
    MessageBoxOk = QMessageBox.Ok


def is_qt_available() -> bool:
    """Return True if a real or mock Qt binding is loaded and ready."""
    return IS_QT_AVAILABLE or IS_MOCK_QT


def get_qt_binding_name() -> str:
    """Return the name of the active Qt binding ('PyQt6', 'PyQt5', 'MockQt', or 'None')."""
    return QT_BINDING or "None"
