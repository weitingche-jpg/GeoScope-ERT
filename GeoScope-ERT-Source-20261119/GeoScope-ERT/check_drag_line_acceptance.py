"""Real Qt mouse acceptance for drag-created survey endpoints."""
import copy
import numpy as np
from PySide6 import QtCore,QtWidgets as Q,QtTest
from core import write_json


def run_checks(w,app,root):
    checks=[];failures=[]
    def settle():
        for _ in range(3):app.processEvents()
    def screen(xyz):
        xy,depth=w._screen(np.asarray(xyz,float)-w.terrain.origin)
        assert 0<=depth<=1
        return QtCore.QPoint(int(round(xy[0])),int(round(xy[1])))
    def press(pos):QtTest.QTest.mousePress(w.plotter.interactor,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier,pos);settle()
    def move(pos):QtTest.QTest.mouseMove(w.plotter.interactor,pos,30);settle()
    def release(pos):QtTest.QTest.mouseRelease(w.plotter.interactor,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier,pos);settle()
    def key(code):w.plotter.interactor.setFocus();QtTest.QTest.keyClick(w.plotter.interactor,code);settle()
    try:
        w.load_campus();cx=float(np.mean(w.terrain.x));cy=float(np.mean(w.terrain.y))
        az=float(w.terrain.height([[cx-20,cy]])[0]);bz=float(w.terrain.height([[cx+20,cy+15]])[0])
        anchor=dict(id='P001',x=cx-20,y=cy,z=az,elevation_source='原有测点')
        w.points=[anchor];w.points_follow_line=False;w.line=[[cx-20,cy-20],[cx+20,cy-20]];w.redraw()
        w.plotter.view_xy();w.plotter.reset_camera(bounds=(cx-60-w.terrain.origin[0],cx+60-w.terrain.origin[0],cy-50-w.terrain.origin[1],cy+50-w.terrain.origin[1],-5,5));w.plotter.render();settle()
        old=copy.deepcopy(w.line);points=copy.deepcopy(w.points);n=w.history.undo_count
        key(QtCore.Qt.Key_L);assert w.mode=='line' and w.line==old
        camera=np.asarray(list(w.plotter.camera_position),float)
        a=screen([anchor['x'],anchor['y'],anchor['z']+2]);b=screen([cx+20,cy+15,bz]);mid=screen([cx+5,cy+5,w.terrain.height([[cx+5,cy+5]])[0]])
        press(a);move(mid);text1=w.inspector.text();move(b);text2=w.inspector.text()
        assert text1!=text2 and 'X' in text2 and 'Y' in text2 and 'Z' in text2,'拖动坐标未实时变化'
        assert w.points==points,'拖动修改了原测点'
        assert w.history.undo_count==n,'每帧移动被写进撤销历史'
        w.grab().save(str(root/'drag-line-live-preview.png'))
        release(b);assert len(w.line)==2 and w.history.undo_count==n+1
        np.testing.assert_allclose(w.line[0],[anchor['x'],anchor['y']],atol=.01)
        np.testing.assert_allclose(w.line[1],[cx+20,cy+15],atol=1.)
        np.testing.assert_allclose(np.asarray(list(w.plotter.camera_position),float),camera)
        assert w.points==points
        checks.append('真实鼠标从已有测点按住拖出终点，实时XYZ/线长变化，相机不转，原点不改，每次拖动仅一步历史')
        completed=copy.deepcopy(w.line);w.undo_edit();settle();assert w.line==old
        w.redo_edit();settle();assert w.line==completed
        checks.append('拖动生成的整条测线可一次撤销/重做')
        key(QtCore.Qt.Key_L);press(a);move(mid);key(QtCore.Qt.Key_Escape)
        assert w.line==completed and w.points==points and Q.QWidget.mouseGrabber() is None
        checks.append('Esc取消恢复原几何并释放鼠标')
        n=w.history.undo_count;key(QtCore.Qt.Key_L);press(a);release(a)
        assert w.line==completed and w.history.undo_count==n and Q.QWidget.mouseGrabber() is None
        checks.append('单击无拖动不生成零长线，不留撤销记录')
        if w.mode!='browse':key(QtCore.Qt.Key_Escape)
        key(QtCore.Qt.Key_L);press(a);move(mid);release(QtCore.QPoint(-20,-20))
        assert w.line==completed and w.history.undo_count==n and Q.QWidget.mouseGrabber() is None
        checks.append('拖出有效地形松开取消，原布局和历史不变')
        if w.mode!='browse':key(QtCore.Qt.Key_Escape)
        w.redraw();settle()
        end=w.line_endpoint_records()[1]
        w.query_at(QtCore.QPointF(screen([end['x'],end['y'],end['z']+2])));settle()
        assert '终点 B' in w.inspector.text() and 'Z' in w.inspector.text()
        assert 'line-endpoint-markers' in w.plotter.actors
        checks.append('完成后端点标记和坐标持续显示，点击终点可查询XYZ')
        ga=screen([cx-15,cy+25,w.terrain.height([[cx-15,cy+25]])[0]])
        gb=screen([cx+25,cy+30,w.terrain.height([[cx+25,cy+30]])[0]])
        key(QtCore.Qt.Key_L);press(ga);move(gb);release(gb)
        np.testing.assert_allclose(w.line[0],[cx-15,cy+25],atol=1.)
        assert w.points==points
        checks.append('无测点的地形位置也可起拖，终点吸附地形，原有测点不变')
        old=copy.deepcopy(w.line);key(QtCore.Qt.Key_L);press(a);move(mid)
        w.set_mode('point');settle()
        assert w.line==old and Q.QWidget.mouseGrabber() is None
        w.set_mode('browse');settle()
        checks.append('拖动中切换工具会取消并恢复，释放鼠标')
        add=next(b for b in w.survey_dock.findChildren(Q.QPushButton) if b.text()=='新增测线')
        add.click();settle();assert len(w.survey_state['lines'])==2
        assert w.line==[] and w.points==[] and w.workflow_step==1
        assert w.survey_table.item(1,3).text()=='待绘线'
        checks.append('右侧新增测线只建空行，选中后可到测点与数据步骤绘线')
        first=w.survey_state['lines'][0]['id'];other=copy.deepcopy(w.survey_state['lines'][1])
        w.activate_survey_line(first);settle()
        a=screen([anchor['x'],anchor['y'],anchor['z']])
        mid=screen([cx+5,cy+5,w.terrain.height([[cx+5,cy+5]])[0]])
        key(QtCore.Qt.Key_L);press(a);move(mid);release(mid)
        np.testing.assert_allclose(w.line[0],[anchor['x'],anchor['y']],atol=.01)
        assert w.survey_state['lines'][1]==other
        assert w.survey_state['lines'][0]['workspace']['points']==points
        checks.append('多测线场景仅修改当前线，另一条线和原测点完整保留')
        w.grab().save(str(root/'drag-line-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    write_json(root/'drag-line-verification.json',dict(passed=not failures,checks=checks,failures=failures))
    if hasattr(w,'cancel_current_action') and w.mode!='browse':w.cancel_current_action()
    for window in list(w.manager.windows):window.dirty=False;window.close()
    app.exit(1 if failures else 0)
