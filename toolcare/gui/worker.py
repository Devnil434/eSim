"""
toolcare.gui.worker - Background execution thread with Qt signal dispatching.
Ensures zero GUI blocking, exception isolation, and thread safety.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from toolcare.gui.qt_compat import QThread, pyqtSignal
from toolcare.logger import get_logger

logger = get_logger("gui.worker")


class WorkerThread(QThread):
    """
    Dedicated background worker thread for long-running ToolCare operations.
    Dispatches status, progress, and logs strictly through Qt signals to avoid
    GUI thread manipulation or cross-thread widget access.
    """

    # Authoritative Qt signals specified in requirements
    started_task = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    log_message = pyqtSignal(str)
    finished_task = pyqtSignal(bool, str)

    def __init__(
        self,
        task_name: str,
        task_func: Callable[..., Any],
        *args: Any,
        parent: Optional[Any] = None,
        **kwargs: Any,
    ):
        super().__init__(parent)
        self.task_name = task_name
        self.task_func = task_func
        self.args = args
        self.kwargs = kwargs
        self._is_cancelled = False
        self._is_running = False

    def report_progress(self, percent: int, status_message: str = ""):
        """
        Thread-safe helper called by running task to notify progress.
        Use percent = -1 for indeterminate progress.
        """
        if not self._is_cancelled:
            self.progress.emit(percent, status_message)

    def log(self, message: str):
        """Thread-safe helper called by running task to post a log message."""
        if not self._is_cancelled:
            self.log_message.emit(message)

    def cancel(self):
        """Mark task as cancelled."""
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        """Check if task has been requested to cancel."""
        return self._is_cancelled

    def is_running(self) -> bool:
        """Return True if worker thread is actively executing."""
        return self._is_running

    def run(self):
        """Execute task_func on background thread."""
        self._is_running = True
        logger.info("Worker thread started task: %s", self.task_name)
        self.started_task.emit(self.task_name)

        try:
            # Check if task_func expects worker instance as first parameter
            sig = inspect.signature(self.task_func)
            if "worker" in sig.parameters or (
                len(sig.parameters) > 0
                and list(sig.parameters.keys())[0] in ("worker", "thread", "worker_thread")
            ):
                result = self.task_func(self, *self.args, **self.kwargs)
            else:
                result = self.task_func(*self.args, **self.kwargs)

            if self._is_cancelled:
                msg = f"Task '{self.task_name}' was cancelled."
                logger.info(msg)
                self.log_message.emit(msg)
                self.finished_task.emit(False, msg)
            else:
                success_msg = str(result) if result is not None else f"Task '{self.task_name}' completed successfully."
                logger.info("Worker task succeeded: %s", self.task_name)
                self.finished_task.emit(True, success_msg)

        except Exception as exc:
            err_msg = f"Task '{self.task_name}' failed: {exc}"
            logger.error(err_msg, exc_info=True)
            self.log_message.emit(f"ERROR: {exc}")
            self.finished_task.emit(False, str(exc))

        finally:
            self._is_running = False
