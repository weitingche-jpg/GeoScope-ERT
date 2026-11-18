"""Live Qt wheel and drag acceptance for the default campus navigation."""
import json
import numpy as np
from PySide6 import QtCore,QtGui,QtTest,QtWidgets


def run_checks(window,app,root):
    checks=[];failures=[];metrics={}
    try:
        window.load_campus()
        assert hasattr(window.plotter,'_stable_navigation')
        window.set_mode('browse')
        camera=window.plotter.camera
        window.plotter.camera_position=[(0,-100,100),(0,0,20),(0,0,1)]
        camera.Dolly(100000)
        window.plotter.reset_camera_clipping_range()
        window.plotter.render();app.processEvents()
        canvas=window.plotter.interactor
        center=QtCore.QPoint(canvas.width()//2,canvas.height()//2)
        def depth():
            position=np.asarray(camera.position)
            direction=np.asarray(camera.GetDirectionOfProjection())
            return -position[2]/direction[2]
        def wheel(delta):
            event=QtGui.QWheelEvent(QtCore.QPointF(center),QtCore.QPointF(canvas.mapToGlobal(center)),
                QtCore.QPoint(),QtCore.QPoint(0,delta),QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,QtCore.Qt.NoScrollPhase,False)
            QtWidgets.QApplication.sendEvent(canvas,event)
            app.processEvents()
        old_depth=depth()
        old_position=np.asarray(camera.position)
        wheel(120)
        ratio=old_depth/depth()
        assert 1.18<ratio<1.24,ratio
        wheel(-120)
        assert np.linalg.norm(np.asarray(camera.position)-old_position)<.05
        metrics['wheel_zoom_ratio']=float(ratio)
        checks.append('陈旧焦点极端放大后，实际Qt滚轮仍按约1.21倍缩放且正反可逆')
        position=np.asarray(camera.position);direction=np.asarray(camera.GetDirectionOfProjection())
        point=position+direction*depth()
        before=window._screen(point)[0]
        target=center+QtCore.QPoint(80,0)
        QtTest.QTest.mousePress(canvas,QtCore.Qt.MiddleButton,pos=center)
        QtTest.QTest.mouseMove(canvas,target,30)
        QtTest.QTest.mouseRelease(canvas,QtCore.Qt.MiddleButton,pos=target)
        app.processEvents()
        movement=window._screen(point)[0]-before
        metrics['pan_80px_screen_delta']=movement.tolist()
        assert abs(movement[0]-80)<3 and abs(movement[1])<3,movement
        checks.append('极端放大后实际中键拖动80像素，画面位移仍约80像素')
        assert len(window.points)==0 and not window.line
        assert all(not actor.GetPickable() for actor in window.campus_actors)
        checks.append('导航未创建测点/测线，展示层不可拾取状态完整恢复')
        window.view_iso();window.plotter.render();app.processEvents()
        window.grab().save(str(root/'navigation-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    (root/'navigation-verification.json').write_text(json.dumps(dict(passed=not failures,
        checks=checks,metrics=metrics,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    window.dirty=False;window.close();app.exit(1 if failures else 0)
