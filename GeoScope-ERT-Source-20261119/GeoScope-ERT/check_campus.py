"""Real Qt picking and persistence checks on the fictional campus."""
import json
import numpy as np
from PySide6 import QtCore,QtTest


def run_checks(w,app,root):
    checks=[]; failures=[]
    def key(code,mods=QtCore.Qt.NoModifier):
        w.plotter.interactor.setFocus()
        QtTest.QTest.keyClick(w.plotter.interactor,code,mods)
        app.processEvents()
    def click(x,y):
        screen,_=w._screen(np.array([x,y,0])-w.terrain.origin)
        QtTest.QTest.mouseClick(w.plotter.interactor,QtCore.Qt.LeftButton,
            pos=QtCore.QPoint(*np.rint(screen).astype(int)))
        app.processEvents()
    try:
        w.load_campus()
        assert len(w.campus_scene['buildings'])==19 and w.campus_scene.get('render_meshes')
        assert w.campus_scene.get('model_id')=='shah-images-20261110'
        assert '虚拟' in w.scene_badge.text() and w.model is None
        assert np.allclose(w.terrain.z,0)
        checks.append('图片重建校园19栋楼及彩色展示层加载，明确示意标识')
        w.view_top();w.plotter.render()
        key(QtCore.Qt.Key_P);click(457,200)
        assert len(w.points)==1 and abs(w.points[0]['x']-457)<5
        assert abs(w.points[0]['z'])<.01
        checks.append('真实鼠标点击道路布点，坐标落在地面')
        key(QtCore.Qt.Key_L)
        a,_=w._screen(np.array([457,120,0])-w.terrain.origin)
        b,_=w._screen(np.array([457,280,0])-w.terrain.origin)
        start=QtCore.QPoint(*np.rint(a).astype(int));end=QtCore.QPoint(*np.rint(b).astype(int))
        QtTest.QTest.mousePress(w.plotter.interactor,QtCore.Qt.LeftButton,pos=start)
        QtTest.QTest.mouseMove(w.plotter.interactor,end,30)
        QtTest.QTest.mouseRelease(w.plotter.interactor,QtCore.Qt.LeftButton,pos=end)
        app.processEvents()
        assert len(w.line)==2 and w.history.undo_count==2
        key(QtCore.Qt.Key_Z,QtCore.Qt.ControlModifier)
        assert w.line==[] and len(w.points)==1
        key(QtCore.Qt.Key_Y,QtCore.Qt.ControlModifier)
        assert len(w.line)==2
        checks.append('真实鼠标绘线及Ctrl+Z/Y撤销重做')
        path=root/'.cache'/'campus-project.json';path.parent.mkdir(exist_ok=True)
        w.save_project_to(path);w.load_project_from(path)
        assert w.campus_scene.get('model_id')=='shah-images-20261110' and len(w.points)==1 and len(w.line)==2
        checks.append('保存重开后校园图层与测量布局恢复')
        w.view_iso();w.plotter.render();w.grab().save(str(root/'campus-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    (root/'campus-verification.json').write_text(json.dumps(dict(passed=not failures,
        checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    w.dirty=False;w.close();app.exit(1 if failures else 0)
