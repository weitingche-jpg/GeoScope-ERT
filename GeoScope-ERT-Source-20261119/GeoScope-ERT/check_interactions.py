"""Qt/VTK/Matplotlib integration checks, also executable inside the frozen app."""
import json
from types import SimpleNamespace
import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets
from matplotlib.backend_bases import MouseEvent


def run_checks(window, app, root):
    print('INTERACTION_CHECK_START', flush=True)
    failures = []
    checks = []
    def finish():
        window.section_view.canvas.draw()
        window.plotter.render()
        window.grab().save(str(root/'interaction-preview.png'))
        result = dict(passed=not failures, failures=failures, checks=checks)
        (root/'interaction-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        window.dirty = False
        window.close()
        app.exit(0 if not failures else 1)
    try:
        assert window.model is not None and window.section_data is not None
        cid = window.model.n_cells//2
        window.select_cell(cid)
        assert window.selected == ('cell',cid) and window.section_view.selected_cell == cid
        assert 'Ω·m' in window.inspector.text()
        checks.append('三维选择→二维同编号单元高亮/查询')
        window.clear_selection()
        point = window.model.get_cell(cid).points.mean(axis=0)
        screen,_ = window._screen(point)
        window.query_at(QtCore.QPointF(*screen))
        assert window.selected == ('cell',cid), '三维真实拾取未命中预期单元'
        checks.append('三维 VTK 拾取命中对应单元')
        canvas = window.section_view.canvas
        canvas.draw()
        center = window.section_view.polygons[cid].mean(axis=0)
        pixel = window.section_view.axes.transData.transform(center)
        window.select_cell((cid+1)%window.model.n_cells)
        event = MouseEvent('button_press_event', canvas, *pixel, button=1)
        canvas.callbacks.process('button_press_event', event)
        assert window.selected == ('cell',cid)
        checks.append('二维真实 Matplotlib 点击→三维同编号高亮')
        window.table.cellClicked.emit(3,0)
        print('POINT_SELECTION', window.selected, window.section_view.selected_point, flush=True)
        assert window.selected == ('point',3) and window.section_view.selected_point == 3
        window.section_view.point_selected.emit(7)
        assert window.selected == ('result_point',7)
        checks.append('表格/三维测点/二维电极联动')
        print('SELECTIONS_OK', flush=True)
        original_line = np.asarray(window.line).copy()
        original_mesh = window.model.points.copy()
        window.set_mode('edit')
        window.view_top()
        window.plotter.render()
        app.processEvents()
        source_xy = original_line[0]
        target_xy = source_xy+[0,85]
        source_world = np.r_[source_xy,window.terrain.height([source_xy])[0]+3]-window.terrain.origin
        target_world = np.r_[target_xy,window.terrain.height([target_xy])[0]]-window.terrain.origin
        source_screen,_ = window._screen(source_world)
        target_screen,_ = window._screen(target_world)
        p1 = QtCore.QPoint(*np.rint(source_screen).astype(int))
        p2 = QtCore.QPoint(*np.rint(target_screen).astype(int))
        widget = window.plotter.interactor
        QtTest.QTest.mousePress(widget, QtCore.Qt.LeftButton, pos=p1)
        print('DRAG_PRESS', window.drag_endpoint, flush=True)
        assert window.drag_endpoint == 0, '端点拾取失败'
        QtTest.QTest.mouseMove(widget,p2,delay=30)
        app.processEvents()
        QtTest.QTest.mouseRelease(widget,QtCore.Qt.LeftButton,pos=p2)
        print('DRAG_RELEASE', window.result_stale, flush=True)
        assert window.drag_endpoint is None
        assert window.result_stale and np.linalg.norm(np.asarray(window.line[0])-source_xy)>10
        np.testing.assert_allclose(window.model.points,original_mesh)
        assert '过期' in window.result_status.text()
        window.grab().save(str(root/'interaction-stale-preview.png'))
        checks.append('Qt 鼠标按下/拖动/松开→贴地端点更新/旧结果不动/过期提示')
        xy = np.asarray(window.line[0])
        current_world = np.r_[xy,window.terrain.height([xy])[0]+3]-window.terrain.origin
        current_screen,_ = window._screen(current_world)
        current_position = QtCore.QPoint(*np.rint(current_screen).astype(int))
        QtTest.QTest.mousePress(widget, QtCore.Qt.LeftButton, pos=current_position)
        assert window.drag_endpoint == 0
        QtTest.QTest.keyClick(widget,QtCore.Qt.Key_Escape)
        assert QtWidgets.QWidget.mouseGrabber() is not widget, 'Esc 取消拖动后未释放鼠标捕获'
        assert window.press_position is None
        QtTest.QTest.mouseRelease(widget,QtCore.Qt.LeftButton,pos=current_position)
        checks.append('Esc 取消拖动释放鼠标捕获，其他面板仍可交互')
        changed_line = np.asarray(window.line).copy()
        project_file = root/'.cache'/'interaction-project.json'
        window.save_project_to(project_file)
        window.load_project_from(project_file)
        np.testing.assert_allclose(window.line,changed_line)
        np.testing.assert_allclose(window.model.points,original_mesh)
        assert window.result_stale
        checks.append('保存/重开项目保留新测线与原位旧模型及过期状态')
        assert window.move_endpoint(0, original_line[0])
        assert not window.result_stale
        assert not window.move_endpoint(0,[1e10,1e10])
        checks.append('恢复原测线解除过期/拒绝地形外端点')
        window.points_follow_line = False
        saved_points = [p.copy() for p in window.points]
        assert window.move_endpoint(0,target_xy)
        assert saved_points == window.points
        checks.append('文件/独立测点保留原坐标')
        window.move_endpoint(0,original_line[0])
        window.points_follow_line = True
        window.set_mode('browse')
        window.view_iso()
        window.select_cell(cid)
        checks.append('选择和视角切换稳定')
        # Re-run the existing synthetic inversion through the actual worker.
        window.run_inversion()
        print('WORKER_START', flush=True)
        assert window.process is not None
        timer = QtCore.QTimer(window)
        timer.setInterval(200)
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
        def poll():
            if window.process is None:
                timer.stop()
                try:
                    assert window.model is not None and not window.result_stale
                    window.select_cell(window.model.n_cells//2)
                    checks.append('独立进程重算后结果更新/解除过期/双视图可选')
                except Exception as e:
                    failures.append(repr(e))
                finish()
            elif elapsed.elapsed()>90000:
                timer.stop()
                failures.append('计算进程超时')
                window.process.kill()
                window.process.waitForFinished(3000)
                finish()
        timer.timeout.connect(poll)
        timer.start()
    except Exception as e:
        import traceback
        print(traceback.format_exc(), flush=True)
        failures.append(traceback.format_exc())
        finish()
