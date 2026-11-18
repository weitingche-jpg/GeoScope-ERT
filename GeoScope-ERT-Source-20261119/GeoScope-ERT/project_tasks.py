"""Responsive project file tasks with an application-modal progress dialog.

Callbacks run in a background ``QThread`` and may only touch plain Python data
and the filesystem.  Qt widgets, VTK objects and anything else owned by the GUI
thread must stay in the caller.  The GUI thread waits in a local ``QEventLoop``
while the dialog is application-modal, so timers and repainting continue but
editing, closing and starting another project file task are blocked.

``callback`` receives ``progress(value, text='')`` (value is 0..100) unless it
declares no positional parameters.  ``gui_finish`` runs back on the GUI thread
while the same dialog is still modal; it receives ``(result, tick)`` and may
call ``tick(value, text='')`` to update the dialog and let Qt repaint between
short modelling steps.  No ``QApplication`` means synchronous execution.
"""
from __future__ import annotations

import inspect

from PySide6 import QtCore, QtWidgets as Q

_ACTIVE_TASK = None
_STEP_ACTIVE = False


def project_task_active():
    """Return True while a background project file task owns the UI."""
    return _ACTIVE_TASK is not None


def _application():
    app = Q.QApplication.instance()
    return app if isinstance(app, Q.QApplication) else None


def _wants_progress(callback):
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return True
    return any(parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                  inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  inspect.Parameter.VAR_POSITIONAL)
               for parameter in parameters.values())


def _call(callback, progress):
    return callback(progress) if _wants_progress(callback) else callback()


def _ignore_progress(value, text=''):
    return None


class _Worker(QtCore.QThread):
    progressed = QtCore.Signal(object, object)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def report(self, value, text=''):
        self.progressed.emit(value, str(text or ''))

    def run(self):
        try:
            result = _call(self._callback, self.report)
        except BaseException as error:
            self.failed.emit(error)
        else:
            self.succeeded.emit(result)


class _ProgressDialog(Q.QProgressDialog):
    def __init__(self, title, parent=None):
        super().__init__(title, '', 0, 100, parent)
        self._closing = False
        self.setWindowTitle('GeoScope')
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setCancelButton(None)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowCloseButtonHint, False)

    @QtCore.Slot(object, object)
    def update_progress(self, value, text=''):
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            number = 0
        self.setValue(max(0, min(100, number)))
        if text:
            self.setLabelText(str(text))

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def reject(self):
        if self._closing:
            super().reject()

    def closeEvent(self, event):
        if self._closing:
            event.accept()
        else:
            event.ignore()

    def finish(self):
        self._closing = True
        # Stop QProgressDialog's delayed force-show timer before hiding; a
        # queued timer can otherwise reopen a finished modal dialog.
        self.reset()
        self.done(0)


class _Coordinator(QtCore.QObject):
    def __init__(self, loop, parent):
        super().__init__(parent)
        self.loop = loop
        self.result = None
        self.error = None
        self.done = False

    @QtCore.Slot(object)
    def completed(self, value):
        self.result = value
        self.done = True
        self.loop.quit()

    @QtCore.Slot(object)
    def failed(self, error):
        self.error = error
        self.done = True
        self.loop.quit()


class _CloseGuard(QtCore.QObject):
    """Swallow user/window-manager closes while a task is in flight."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._dialog = dialog

    def eventFilter(self, watched, event):
        if (event.type() == QtCore.QEvent.Type.Close
                and isinstance(watched, Q.QWidget) and watched.isWindow()
                and watched is not self._dialog):
            event.ignore()
            return True
        return False


def run_project_task(callback, parent=None, title='正在处理项目…', *, gui_finish=None):
    """Run ``callback`` off the GUI thread and wait with a modal progress dialog.

    Returns the callback's result (or ``gui_finish``'s return value).  Exceptions
    are re-raised on the GUI thread after the dialog is closed and the thread has
    finished.  Nested/duplicate calls raise ``RuntimeError`` instead of starting
    a second task.  Without ``QApplication`` the callback runs synchronously.
    """
    global _ACTIVE_TASK
    app = _application()
    if app is None:
        result = _call(callback, _ignore_progress)
        if gui_finish is not None:
            result = gui_finish(result, _ignore_progress)
        return result
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError('项目文件任务只能在界面线程中启动。')
    if _ACTIVE_TASK is not None:
        raise RuntimeError('已有项目文件任务正在进行，请等待完成。')

    dialog = _ProgressDialog(title, parent)
    guard = _CloseGuard(dialog)
    app.installEventFilter(guard)
    _ACTIVE_TASK = object()
    loop = QtCore.QEventLoop()
    coordinator = _Coordinator(loop, dialog)
    worker = _Worker(callback)
    worker.progressed.connect(dialog.update_progress)
    worker.succeeded.connect(coordinator.completed)
    worker.failed.connect(coordinator.failed)

    def tick(value, text=''):
        dialog.update_progress(value, text)
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    try:
        dialog.show()
        dialog.raise_()
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        worker.start()
        loop.exec()
        worker.wait()
        if not coordinator.done:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if not coordinator.done:
            raise RuntimeError('项目文件任务未返回结果。')
        if coordinator.error is not None:
            raise coordinator.error
        result = coordinator.result
        if gui_finish is not None:
            result = gui_finish(result, tick)
        return result
    finally:
        if worker.isRunning():
            worker.wait()
        app.removeEventFilter(guard)
        dialog.finish()
        dialog.deleteLater()
        _ACTIVE_TASK = None


def run_project_step(callback, parent=None, title='正在读取结果…'):
    """Run a pure IO restoration step under the existing project's busy guard.

    A public task remains non-reentrant. This internal step lets a GUI builder
    request additional files without blocking the GUI or opening another modal
    dialog. No widget or VTK object may be accessed by ``callback``.
    """
    global _STEP_ACTIVE
    app=_application()
    if app is None or _ACTIVE_TASK is None:
        return run_project_task(callback,parent,title)
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError('文件恢复步骤只能由界面线程启动。')
    if _STEP_ACTIVE:
        raise RuntimeError('文件恢复步骤正在进行。')
    dialog=app.activeModalWidget()
    if not isinstance(dialog,_ProgressDialog):
        raise RuntimeError('项目进度窗口不可用。')
    _STEP_ACTIVE=True
    loop=QtCore.QEventLoop()
    coordinator=_Coordinator(loop,dialog)
    worker=_Worker(callback)
    worker.progressed.connect(dialog.update_progress)
    worker.succeeded.connect(coordinator.completed)
    worker.failed.connect(coordinator.failed)
    try:
        dialog.setLabelText(title)
        worker.start();loop.exec();worker.wait()
        if not coordinator.done:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if not coordinator.done:raise RuntimeError('文件恢复步骤未返回结果。')
        if coordinator.error is not None:raise coordinator.error
        return coordinator.result
    finally:
        if worker.isRunning():worker.wait()
        coordinator.deleteLater()
        _STEP_ACTIVE=False
