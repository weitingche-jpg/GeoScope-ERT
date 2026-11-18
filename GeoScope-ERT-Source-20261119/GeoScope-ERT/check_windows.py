"""Exercise independent terrain windows through Qt keyboard events."""
import copy
import json
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets as Q


def run_checks(first, app, root):
    checks, failures = [], []
    manager = first.manager
    second = None
    app.setQuitOnLastWindowClosed(False)
    def key(window, code, mods=QtCore.Qt.ControlModifier):
        window.plotter.interactor.setFocus()
        QtTest.QTest.keyClick(window.plotter.interactor, code, mods)
        app.processEvents()
    try:
        original = copy.deepcopy(first.points)
        key(first, QtCore.Qt.Key_N)
        second = manager.windows[-1]
        assert second is not first and second.terrain is None and len(manager.windows)==2
        checks.append('Ctrl+N 创建空白独立窗口')
        second.load_terrain(root/'data'/'ruapehu'/'Ruapehu_15m_NZTM.tif')
        assert second.terrain.crs != first.terrain.crs
        assert first.model is not None and second.model is None
        checks.append('两个真实地形及不同坐标系同时加载，模型分别保存')
        second.set_mode('point')
        xy=np.array([second.terrain.x.mean(),second.terrain.y.mean()])
        second.picked(np.r_[xy-second.terrain.origin[:2],0])
        second.set_mode('browse')
        assert len(second.points)==1 and first.points==original
        key(second,QtCore.Qt.Key_Z)
        assert second.points==[] and first.points==original
        key(second,QtCore.Qt.Key_Y)
        assert len(second.points)==1 and first.history.undo_count==0
        checks.append('布点、Ctrl+Z/Y 仅作用于目标窗口')
        target=root/'.cache'/'second-window.json'
        target.parent.mkdir(exist_ok=True)
        second.save_project_to(target)
        assert second.project_path==target.resolve() and first.project_path is None
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=('','')):
            manager.open_terrain(first)
        assert len(manager.windows)==2
        checks.append('项目保存路径隔离，取消导入不增开窗口')
        manager.tile()
        app.processEvents()
        assert not first.geometry().intersects(second.geometry())
        second.grab().save(str(root/'multi-window-preview.png'))
        checks.append('左右平铺独立窗口')
        first.dirty=False
        key(first,QtCore.Qt.Key_W)
        assert first not in manager.windows and second.isVisible()
        key(second,QtCore.Qt.Key_Z)
        assert second.points==[]
        checks.append('关闭首窗口后另一窗口继续编辑')
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
        print(failures[-1],flush=True)
    (root/'window-verification.json').write_text(json.dumps(dict(passed=not failures,
        checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    for window in list(manager.windows):
        window.dirty=False
        window.close()
    app.exit(1 if failures else 0)
