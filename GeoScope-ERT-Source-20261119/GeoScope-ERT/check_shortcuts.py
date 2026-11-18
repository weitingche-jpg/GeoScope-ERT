"""Real Qt key-event integration checks, also run by the portable executable."""
import json
import copy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore, QtWidgets as Q, QtTest


def run_checks(window, app, root):
    checks, failures = [], []
    def finish():
        window.section_view.canvas.draw()
        window.grab().save(str(root/'shortcut-preview.png'))
        (root/'shortcut-verification.json').write_text(json.dumps(dict(
            passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
        window.dirty = False
        window.close()
        app.exit(0 if not failures else 1)
    def key(widget, code, modifiers=QtCore.Qt.NoModifier):
        widget.setFocus()
        QtTest.QTest.keyClick(widget,code,modifiers)
        app.processEvents()
    try:
        assert hasattr(window,'shortcut_controller'), '尚未接入快捷键控制器'
        view = window.plotter.interactor
        for code,mode in [(QtCore.Qt.Key_P,'point'),(QtCore.Qt.Key_L,'line'),
                          (QtCore.Qt.Key_E,'edit'),(QtCore.Qt.Key_V,'browse')]:
            key(view,code)
            assert window.mode == mode
        checks.append('P/L/E/V 模式切换')
        original_line = copy.deepcopy(window.line)
        key(view,QtCore.Qt.Key_L)
        xy = np.asarray(original_line[0])+[0,40]
        window.picked(np.r_[xy-window.terrain.origin[:2],window.terrain.height([xy])[0]])
        assert len(window.line)==1
        draft_path=root/'.cache'/'should-not-save-draft.json'
        with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(draft_path),'')) as dialog:
            menu=window.project_menu
            for action in menu.actions():
                if action.text().startswith(('保存项目','另存为')):
                    action.trigger()
            assert not dialog.called, '菜单保存绕过未完成编辑检查'
        key(view,QtCore.Qt.Key_Escape)
        np.testing.assert_allclose(window.line,original_line)
        checks.append('Esc 取消未完成测线恢复原布局')
        checks.append('菜单保存/另存为与快捷键共用未完成编辑保护')
        key(view,QtCore.Qt.Key_2)
        np.testing.assert_allclose(window.plotter.camera.GetDirectionOfProjection(),[0,0,-1],atol=1e-6)
        key(view,QtCore.Qt.Key_3)
        key(view,QtCore.Qt.Key_1)
        checks.append('1/2/3 视角切换')
        window.select_cell(window.model.n_cells//2)
        center = window.model.get_cell(window.model.n_cells//2).points.mean(axis=0)
        key(view,QtCore.Qt.Key_F)
        np.testing.assert_allclose(window.plotter.camera.focal_point,center)
        key(view,QtCore.Qt.Key_Home)
        assert np.linalg.norm(np.asarray(window.plotter.camera.focal_point)-center)>1
        checks.append('F 聚焦选中单元 / Home 完整场景')
        def camera_values():
            c=window.plotter.camera
            return np.array([c.position,c.focal_point,c.up])
        camera = camera_values()
        canvas = window.section_view.canvas
        limits = window.section_view.home_limits
        window.section_view.axes.set_xlim(300,400)
        key(canvas,QtCore.Qt.Key_Home)
        np.testing.assert_allclose(window.section_view.axes.get_xlim(),limits[0])
        np.testing.assert_allclose(camera_values(),camera)
        key(canvas,QtCore.Qt.Key_P)
        assert window.mode=='browse' and not window.section_view.toolbar.mode
        checks.append('二维 Home 独立复位 / 不误触三维工具或默认平移')
        edit = Q.QLineEdit(window)
        edit.show()
        edit.setFocus()
        QtTest.QTest.keyClicks(edit,'ple123f')
        key(edit,QtCore.Qt.Key_Home)
        assert edit.text()=='ple123f' and edit.cursorPosition()==0 and window.mode=='browse'
        np.testing.assert_allclose(camera_values(),camera)
        edit.hide()
        edit.deleteLater()
        checks.append('文本输入优先，字母/数字/Home 不劫持')
        file1 = root/'.cache'/'shortcut-save.json'
        file2 = root/'.cache'/'shortcut-save-as.json'
        window.save_project_to(file1)
        window.points[0]['id']='KEYBOARD-SAVED'
        window.dirty=True
        with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(file2),'')) as dialog:
            key(view,QtCore.Qt.Key_S,QtCore.Qt.ControlModifier)
            assert not dialog.called
            assert json.loads(file1.read_text(encoding='utf-8'))['points'][0]['id']=='KEYBOARD-SAVED'
            key(view,QtCore.Qt.Key_S,QtCore.Qt.ControlModifier|QtCore.Qt.ShiftModifier)
            assert dialog.call_count==1 and window.project_path==file2.resolve()
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(file1),'')) as dialog:
            key(view,QtCore.Qt.Key_O,QtCore.Qt.ControlModifier)
            assert dialog.call_count==1 and window.project_path==file1.resolve()
        checks.append('Ctrl+S 直接保存 / Ctrl+Shift+S 另存为 / Ctrl+O 记忆项目路径')
        image = root/'shortcut-export.png'
        with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(image),'')):
            key(view,QtCore.Qt.Key_E,QtCore.Qt.ControlModifier|QtCore.Qt.ShiftModifier)
        assert image.is_file() and image.stat().st_size>10000
        key(view,QtCore.Qt.Key_I,QtCore.Qt.ControlModifier)
        assert window.shortcut_controller.import_dialog.isVisible()
        window.shortcut_controller.import_dialog.close()
        key(view,QtCore.Qt.Key_F1)
        assert window.shortcut_controller.help_dialog.isVisible()
        window.shortcut_controller.help_dialog.grab().save(str(root/'shortcut-help-preview.png'))
        window.shortcut_controller.help_dialog.close()
        checks.append('导入菜单 / 截图导出 / F1 帮助')
        # Press/move/Escape through the same widget handlers used by a person.
        key(view,QtCore.Qt.Key_E)
        window.view_top()
        window.plotter.render()
        old_line,old_points,old_dirty = copy.deepcopy(window.line),copy.deepcopy(window.points),window.dirty
        xy=np.asarray(window.line[0])
        source,_=window._screen(np.r_[xy,window.terrain.height([xy])[0]+3]-window.terrain.origin)
        target_xy=xy+[0,65]
        target,_=window._screen(np.r_[target_xy,window.terrain.height([target_xy])[0]]-window.terrain.origin)
        p1,p2=QtCore.QPoint(*np.rint(source).astype(int)),QtCore.QPoint(*np.rint(target).astype(int))
        QtTest.QTest.mousePress(view,QtCore.Qt.LeftButton,pos=p1)
        QtTest.QTest.mouseMove(view,p2,delay=30)
        app.processEvents()
        assert window.result_stale
        committed=json.loads(file1.read_text(encoding='utf-8'))
        key(view,QtCore.Qt.Key_S,QtCore.Qt.ControlModifier)
        assert json.loads(file1.read_text(encoding='utf-8'))==committed, '拖动尚未完成却写入了临时几何'
        key(view,QtCore.Qt.Key_Escape)
        np.testing.assert_allclose(window.line,old_line)
        assert window.points==old_points and window.dirty==old_dirty
        assert Q.QWidget.mouseGrabber() is not view and window.drag_endpoint is None
        QtTest.QTest.mouseRelease(view,QtCore.Qt.LeftButton,pos=p2)
        assert not window.result_stale
        checks.append('拖动中 Esc 恢复原测线/测点/脏状态并释放鼠标')
        key(view,QtCore.Qt.Key_Q)
        assert window.isVisible()
        checks.append('阻止 VTK 默认 Q 退出')
        key(view,QtCore.Qt.Key_Return,QtCore.Qt.ControlModifier)
        process=window.process
        request=window.current_request
        assert process is not None
        key(view,QtCore.Qt.Key_Return,QtCore.Qt.ControlModifier)
        key(view,QtCore.Qt.Key_Escape)
        assert window.process is process and window.current_request==request
        checks.append('Ctrl+Enter 启动；重复按键不排队；Esc 不终止计算')
        timer=QtCore.QTimer(window)
        elapsed=QtCore.QElapsedTimer()
        elapsed.start()
        def poll():
            if window.process is None:
                timer.stop()
                if window.model is None or window.result_stale:
                    failures.append('快捷键启动的反演未完成有效结果')
                finish()
            elif elapsed.elapsed()>90000:
                timer.stop()
                failures.append('计算超时')
                window.process.kill()
                window.process.waitForFinished(3000)
                finish()
        timer.timeout.connect(poll)
        timer.start(200)
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
        print(failures[-1],flush=True)
        finish()
