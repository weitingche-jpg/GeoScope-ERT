"""End-to-end Qt checks for imports and reversible geometry edits."""
import copy
import json
import shutil
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q,QtTest


def run_checks(w,app,root):
    checks,failures=[],[]
    def key(code,mods=QtCore.Qt.NoModifier,widget=None):
        target=widget or w.plotter.interactor
        target.setFocus()
        QtTest.QTest.keyClick(target,code,mods)
        app.processEvents()
    ctrl=QtCore.Qt.ControlModifier
    try:
        key(QtCore.Qt.Key_I,ctrl)
        dialog=getattr(w.shortcut_controller,'import_dialog',None)
        assert dialog is not None and dialog.isVisible(),'Ctrl+I 没有打开稳定的导入窗口'
        target=root/'.cache'/'导入 测试地形.tif'
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/'data'/'terrain.tif',target)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(target),'')) as picker:
            w.shortcut_controller.import_buttons['terrain'].click()
            app.processEvents()
            assert picker.call_count==1 and w.terrain.source==str(target.resolve())
            assert w.model is None and w.points==[]
        checks.append('Ctrl+I→导入窗口→选择地形文件→实际加载，含中文空格路径')
        w.dirty=False
        w.demo()
        before=copy.deepcopy(w.points)
        key(QtCore.Qt.Key_P)
        xy=np.asarray(w.line[0])+[0,30]
        w.picked(np.r_[xy-w.terrain.origin[:2],w.terrain.height([xy])[0]])
        assert w.history.undo_count==1 and len(w.points)==len(before)+1
        key(QtCore.Qt.Key_V)
        key(QtCore.Qt.Key_Z,ctrl)
        assert w.points==before and not w.dirty
        key(QtCore.Qt.Key_Y,ctrl)
        assert len(w.points)==len(before)+1 and w.dirty
        checks.append('布点一步撤销/重做，恢复干净状态')
        saved=root/'.cache'/'history-save.json'
        w.save_project_to(saved)
        key(QtCore.Qt.Key_Z,ctrl)
        assert w.dirty
        key(QtCore.Qt.Key_Y,ctrl)
        assert not w.dirty
        checks.append('保存后撤销/重做到保存位置，脏状态正确')
        w.select_point(len(w.points)-1)
        key(QtCore.Qt.Key_Delete)
        assert w.points==before
        key(QtCore.Qt.Key_Z,ctrl)
        assert len(w.points)==len(before)+1
        key(QtCore.Qt.Key_Z,ctrl)
        w.set_mode('point')
        w.picked(np.r_[xy-w.terrain.origin[:2],w.terrain.height([xy])[0]])
        assert w.history.redo_count==0
        checks.append('Delete 测点可撤销 / 新编辑清空重做分支')
        w.dirty=False
        w.demo()
        original=copy.deepcopy(w.line)
        original_mesh=w.model.points.copy()
        key(QtCore.Qt.Key_L)
        points_before=copy.deepcopy(w.points)
        w.generate_points()
        with patch.object(Q.QFileDialog,'getOpenFileName') as picker:
            w.import_points()
            assert not picker.called
        key(QtCore.Qt.Key_S,ctrl)
        assert w.points==points_before and w.history.undo_count==0
        key(QtCore.Qt.Key_Escape)
        assert w.points==points_before and not w.dirty
        checks.append('绘线首点前也保护事务，禁止布点、CSV 导入和保存交叉修改')
        key(QtCore.Qt.Key_L)
        for p in np.asarray(original):
            w.picked(np.r_[p-w.terrain.origin[:2],w.terrain.height([p])[0]])
        assert w.history.undo_count==0 and not w.dirty
        key(QtCore.Qt.Key_L)
        p=np.asarray(original[0])+[0,45]
        w.picked(np.r_[p-w.terrain.origin[:2],w.terrain.height([p])[0]])
        key(QtCore.Qt.Key_Z,ctrl)
        assert w.line==original and not w.dirty and w.history.undo_count==0
        checks.append('相同测线不产生历史；Ctrl+Z 取消未完成绘线')
        key(QtCore.Qt.Key_L)
        for p in np.asarray(original)+[0,45]:
            w.picked(np.r_[p-w.terrain.origin[:2],w.terrain.height([p])[0]])
        assert w.history.undo_count==1 and w.result_stale
        key(QtCore.Qt.Key_Z,ctrl)
        np.testing.assert_allclose(w.line,original)
        assert not w.result_stale
        key(QtCore.Qt.Key_Y,ctrl)
        assert w.result_stale
        np.testing.assert_allclose(w.model.points,original_mesh)
        checks.append('完整绘线只占一步，撤销恢复结果有效性且不挪动模型')
        key(QtCore.Qt.Key_Z,ctrl)
        key(QtCore.Qt.Key_E)
        w.view_top()
        w.plotter.render()
        xy=np.asarray(w.line[0])
        start,_=w._screen(np.r_[xy,w.terrain.height([xy])[0]+3]-w.terrain.origin)
        end_xy=xy+[0,70]
        end,_=w._screen(np.r_[end_xy,w.terrain.height([end_xy])[0]]-w.terrain.origin)
        p1,p2=QtCore.QPoint(*np.rint(start).astype(int)),QtCore.QPoint(*np.rint(end).astype(int))
        view=w.plotter.interactor
        QtTest.QTest.mousePress(view,QtCore.Qt.LeftButton,pos=p1)
        for fraction in (.25,.5,.75,1):
            p=p1+(p2-p1)*fraction
            QtTest.QTest.mouseMove(view,p,delay=20)
        app.processEvents()
        QtTest.QTest.mouseRelease(view,QtCore.Qt.LeftButton,pos=p2)
        assert w.history.undo_count==1,'一次拖动被记录成多步'
        key(QtCore.Qt.Key_Z,ctrl)
        np.testing.assert_allclose(w.line,original)
        checks.append('实际鼠标多次移动只记录一次端点拖动')
        w.select_line()
        oldpoints=copy.deepcopy(w.points)
        key(QtCore.Qt.Key_Delete)
        assert w.line==[] and w.points==[] and w.result_stale
        key(QtCore.Qt.Key_Z,ctrl)
        np.testing.assert_allclose(w.line,original)
        assert w.points==oldpoints
        checks.append('删除测线及其自动布点可一步恢复')
        w.select_result_point(0)
        count=w.history.undo_count
        key(QtCore.Qt.Key_Delete)
        assert w.history.undo_count==count and w.line
        checks.append('原反演电极/地下模型只读，不受 Delete 影响')
        text=Q.QLineEdit(w)
        text.show()
        text.setFocus()
        QtTest.QTest.keyClicks(text,'abc')
        key(QtCore.Qt.Key_Z,ctrl,text)
        assert w.history.undo_count==count
        text.selectAll()
        key(QtCore.Qt.Key_Delete,widget=text)
        assert text.text()=='' and w.history.undo_count==count
        text.hide()
        text.deleteLater()
        checks.append('文本 Ctrl+Z/Y/Delete 不误改几何历史')
        key(QtCore.Qt.Key_V)
        w.select_line()
        w.grab().save(str(root/'history-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
        print(failures[-1],flush=True)
    (root/'history-verification.json').write_text(json.dumps(dict(passed=not failures,
        checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    w.dirty=False
    w.close()
    app.exit(0 if not failures else 1)
