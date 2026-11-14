"""
toolcare.gui.hooks - Safe, idempotent menu integration for eSim desktop application.
Attaches ToolCare management actions into eSim's existing menu bar without modifying
eSim core files or causing duplicate entries.
"""

from __future__ import annotations

from typing import Any, Optional

from toolcare.gui.qt_compat import QAction, is_qt_available
from toolcare.logger import get_logger

logger = get_logger("gui.hooks")

ACTION_LABEL = "ToolCare Toolchain Manager..."
ACTION_OBJECT_NAME = "toolcare_toolchain_manager_action"


def attach_toolcare_to_menu(main_window: Any) -> bool:
    """
    Safely and idempotently attach 'ToolCare Toolchain Manager...' to eSim's menu bar.

    Requirements:
      - Detects existing 'Tools' or 'Help' menu.
      - Adds 'ToolCare Toolchain Manager...' only once (idempotent).
      - Handles None, unsupported objects, and missing menus gracefully.
      - Connects action to launch ToolCareDialog with main_window as parent.

    Returns:
      True if successfully attached or already attached; False otherwise.
    """
    if main_window is None:
        logger.debug("attach_toolcare_to_menu: main_window is None.")
        return False

    # Check for menuBar access
    if not hasattr(main_window, "menuBar"):
        logger.debug("attach_toolcare_to_menu: main_window has no menuBar attribute.")
        return False

    try:
        bar = main_window.menuBar() if callable(main_window.menuBar) else main_window.menuBar
    except Exception as exc:
        logger.warning("attach_toolcare_to_menu: failed to retrieve menuBar: %s", exc)
        return False

    if bar is None:
        logger.debug("attach_toolcare_to_menu: menuBar is None.")
        return False

    # Check for existing action across all menus (idempotency check)
    try:
        # Check by object name if supported
        if hasattr(bar, "findChildren"):
            existing = bar.findChildren(QAction, ACTION_OBJECT_NAME)
            if existing:
                logger.debug("ToolCare action already attached by objectName.")
                return True

        # Check by action text
        for act in bar.actions():
            if act.text() == ACTION_LABEL:
                logger.debug("ToolCare action already attached in bar actions.")
                return True
            menu = act.menu() if hasattr(act, "menu") else None
            if menu and hasattr(menu, "actions"):
                for m_act in menu.actions():
                    if m_act.text() == ACTION_LABEL:
                        logger.debug("ToolCare action already attached in submenu '%s'.", menu.title() if hasattr(menu, "title") else "")
                        return True
    except Exception as exc:
        logger.debug("Idempotency check encountered error: %s", exc)

    # Locate target menu: prefer 'Tools', fallback to 'Help'
    target_menu = None
    tools_menu = None
    help_menu = None

    try:
        for act in bar.actions():
            menu = act.menu() if hasattr(act, "menu") else None
            title = act.text().replace("&", "").strip().lower()
            if menu is not None:
                if title == "tools":
                    tools_menu = menu
                elif title == "help":
                    help_menu = menu
    except Exception as exc:
        logger.debug("Menu discovery error: %s", exc)

    target_menu = tools_menu or help_menu

    # If target_menu is found, check if it already contains the action (idempotency)
    if target_menu is not None and hasattr(target_menu, "actions"):
        for act in target_menu.actions():
            if act.text() == ACTION_LABEL or (hasattr(act, "objectName") and act.objectName() == ACTION_OBJECT_NAME):
                logger.debug("ToolCare action already attached in target menu.")
                return True

    # If no existing menu found, create a '&Tools' menu if addMenu is supported
    if target_menu is None:
        if hasattr(bar, "addMenu"):
            try:
                target_menu = bar.addMenu("&Tools")
            except Exception:
                target_menu = None

    if target_menu is None:
        logger.warning("Could not find or create a target menu in menuBar.")
        return False

    # Add separator and ToolCare action
    try:
        if hasattr(target_menu, "addSeparator"):
            target_menu.addSeparator()

        action = target_menu.addAction(ACTION_LABEL)
        if hasattr(action, "setObjectName"):
            action.setObjectName(ACTION_OBJECT_NAME)

        # Connect trigger to launch_gui
        def on_triggered(*args):
            from toolcare.gui import launch_gui
            launch_gui(parent=main_window)

        if hasattr(action, "triggered"):
            action.triggered.connect(on_triggered)

        logger.info("Successfully attached ToolCare action to menu.")
        return True
    except Exception as exc:
        logger.error("Failed to attach action to menu: %s", exc)
        return False
