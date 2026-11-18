"""Native performance/UX acceptance for background project package IO.

Call from app.py (not wired yet) as::

    from check_project_performance import run_checks
    QtCore.QTimer.singleShot(1200, lambda: run_checks(window, app, ROOT))

It writes ``performance-verification.json`` and ``performance-preview.png`` into
``root`` and exits the application with 0/1 like the other check_*.py scripts.
The deliberately slowed archive write proves the GUI timer keeps beating while
save/reopen and corrupt-package recovery run through the modal task dialog.
"""
import json
import shutil
import time
import traceback
import uuid
from pathlib import Path

from PySide6 import QtCore, QtWidgets as Q


def run_checks(window, app, root):
    root = Path(root)
    folder = root / '.cache' / ('project-performance-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=True)
    checks = []
    failures = []
    metrics = {}
    projects = []
    timer = None
    timer_ticks = []
    progress_events = []

    import project_bundle
    import project_tasks
    from method_shell import MethodProject

    class Fake(Q.QWidget):
        dirty = True
        process = None

        def __init__(self):
            super().__init__()
            box = Q.QVBoxLayout(self)
            box.addWidget(Q.QLabel('GeoScope 项目文件 IO 性能验证\n'
                                   '后台保存 / 打开 / 坏包错误恢复'))
            box.addStretch()

        def export_state(self):
            return {'points': [{'id': 'P01', 'x': 1., 'y': 2., 'z': 0.}]}

        def import_state(self, state):
            self.loaded = state

        def set_terrain(self, *args):
            pass

        def shutdown(self):
            pass

        def mark_saved(self):
            self.dirty = False

        def busy(self):
            return False

    def finish():
        nonlocal timer
        if timer is not None:
            timer.stop()
        result = dict(passed=not failures, checks=checks, failures=failures, metrics=metrics)
        (root / 'performance-verification.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        for project in projects:
            try:
                project.shutdown()
            except Exception:
                pass
        shutil.rmtree(folder, ignore_errors=True)
        app.exit(1 if failures else 0)

    project = None
    try:
        project = MethodProject(factory=lambda *_: Fake())
        projects.append(project)
        host = project.new_host()
        project.open_module('ert', host)
        path = folder / 'performance.geoscope'
        real_write = project_bundle.write_bundle

        def slow_write(*args, **kwargs):
            callback = kwargs.get('progress')

            def counted(value, text=''):
                progress_events.append((value, text))
                if callback:
                    callback(value, text)
            kwargs['progress'] = counted
            time.sleep(0.12)
            return real_write(*args, **kwargs)

        timer = QtCore.QTimer(host)
        timer.setInterval(20)

        def beat():
            timer_ticks.append(time.perf_counter())
        timer.timeout.connect(beat)
        preview = root / 'performance-preview.png'
        captured = [False]

        def capture_busy_dialog():
            dialog = Q.QApplication.activeModalWidget()
            if dialog is not None and dialog.grab().save(str(preview)):
                captured[0] = True

        timer.start()
        started = time.perf_counter()
        # The slow callback keeps the modal dialog visible long enough to capture it.
        QtCore.QTimer.singleShot(80, capture_busy_dialog)
        project_bundle.write_bundle = slow_write
        try:
            saved = project.save(path)
        finally:
            project_bundle.write_bundle = real_write
            timer.stop()
        elapsed = time.perf_counter() - started
        marks = [started] + timer_ticks
        gaps = [b - a for a, b in zip(marks, marks[1:])]
        metrics.update(save_seconds=round(elapsed, 3),
                       timer_ticks=len(timer_ticks),
                       max_timer_gap_ms=round(max(gaps, default=0.) * 1000, 1),
                       progress_events=len(progress_events))
        assert saved and path.exists()
        assert project.dirty is False
        assert project.modules['ert'].dirty is False
        assert metrics['timer_ticks'] >= 2, '保存期间 Qt 定时器没有触发'
        assert metrics['max_timer_gap_ms'] < 500, metrics
        assert metrics['progress_events'] > 0, '后台保存没有报告进度'
        checks.append('慢 IO 保存期间 GUI 定时器持续触发，进度事件到达，保存完成后才标记已保存')

        started = time.perf_counter()
        reopened = MethodProject.load(path, factory=lambda *_: Fake())
        projects.append(reopened)
        metrics['load_seconds'] = round(time.perf_counter() - started, 3)
        assert reopened.modules['ert'].loaded['points'][0]['id'] == 'P01'
        checks.append('保存后重新打开项目包，模块状态在 GUI 线程恢复')

        broken = folder / 'broken.geoscope'
        data = path.read_bytes()
        broken.write_bytes(data[:max(32, len(data) // 2)])
        try:
            MethodProject.load(broken, factory=lambda *_: Fake())
        except Exception as error:
            metrics['bad_bundle_error'] = type(error).__name__ + ': ' + str(error)
        else:
            raise AssertionError('损坏项目包未被拒绝')
        app.processEvents()
        assert Q.QApplication.activeModalWidget() is None, '失败后仍有模态对话框'
        assert project_tasks.project_task_active() is False, '失败后任务仍被占用'
        checks.append('损坏项目包报错后关闭进度对话框、释放任务锁，界面可继续操作')

        app.processEvents()
        reopened_again = MethodProject.load(path, factory=lambda *_: Fake())
        projects.append(reopened_again)
        assert reopened_again.modules['ert'].loaded['points'][0]['id'] == 'P01'
        checks.append('错误恢复后仍可重新打开同一项目包')

        target = window if isinstance(window, Q.QWidget) and window.isVisible() else host
        target.resize(1200, 780)
        target.show()
        app.processEvents()
        if not captured[0]:
            captured[0] = bool(target.grab().save(str(preview)))
        metrics['preview'] = preview.name
        assert captured[0] and preview.exists() and preview.stat().st_size > 0
        checks.append('生成 performance-preview.png 供人工核对忙碌对话框与项目窗口')
    except Exception:
        failures.append(traceback.format_exc())
    finally:
        finish()
