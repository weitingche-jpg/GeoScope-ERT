"""Native coordinate-entry acceptance, independent of implementation tests."""
import copy
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from core import write_json


def run_checks(w,app,root):
    checks=[];failures=[];dialog_errors=[]
    def settle():
        for _ in range(3):app.processEvents()
    def write(field,value):
        if hasattr(field,'setText'):field.setText(str(value))
        else:field.setValue(float(value))
    def edit(values,mode=1,cancel=False,invalid=False):
        def fill():
            dialog=Q.QApplication.activeModalWidget()
            try:
                assert isinstance(dialog,Q.QDialog),'未打开端点坐标编辑窗口'
                combo=dialog.findChild(Q.QComboBox,'endpoint-height-mode');assert combo is not None
                combo.setCurrentIndex(mode)
                for key,value in values.items():
                    field=dialog.findChild(Q.QWidget,'endpoint-'+key);assert field is not None,key
                    write(field,value)
                settle()
                buttons=dialog.findChild(Q.QDialogButtonBox);assert buttons is not None
                if cancel:buttons.button(Q.QDialogButtonBox.Cancel).click()
                else:
                    buttons.button(Q.QDialogButtonBox.Ok).click();settle()
                    if invalid:
                        assert dialog.isVisible(),'无效坐标竟然提交'
                        buttons.button(Q.QDialogButtonBox.Cancel).click()
            except Exception:
                import traceback
                dialog_errors.append(traceback.format_exc())
                if isinstance(dialog,Q.QDialog):dialog.reject()
        QtCore.QTimer.singleShot(100,fill)
        candidates=[b for b in w.findChildren(Q.QPushButton) if '编辑终点坐标' in b.text()]
        assert candidates,'缺少编辑终点坐标按钮'
        candidates[0].click();settle()
        assert not dialog_errors,dialog_errors
    try:
        w.load_campus();cx=float(np.mean(w.terrain.x));cy=float(np.mean(w.terrain.y))
        w.points=[dict(id='E01',x=cx-20,y=cy,z=0.,elevation_source='原测点'),
                  dict(id='E02',x=cx+20,y=cy,z=0.,elevation_source='原测点')]
        w.line=[[cx-20,cy],[cx+20,cy]];w.points_follow_line=False;w.redraw();settle()
        points=copy.deepcopy(w.points);old=copy.deepcopy(w.line);count=w.history.undo_count
        target=dict(x=cx+25,y=cy+15,z=5.5)
        edit(target)
        np.testing.assert_allclose(w.line[-1],[target['x'],target['y']])
        assert w.line[0]==old[0] and w.points==points and w.history.undo_count==count+1
        end=w.line_endpoint_records()[1];assert end['z']==target['z']
        marker=w.plotter.actors['line-endpoint-markers'].mapper.dataset.points[-1]+w.terrain.origin
        np.testing.assert_allclose(marker[:2],[target['x'],target['y']]);assert marker[2]>=target['z']
        mesh=w.line_actor.mapper.dataset.points[-1]+w.terrain.origin
        np.testing.assert_allclose(mesh[:2],[target['x'],target['y']]);assert mesh[2]>=target['z'],'测线未跟随手动高程'
        checks.append('实际对话框输入终点XYZ，地图端点球/坐标/连线同步，原测点与起点不变')
        w.undo_edit();settle();assert w.line==old and not w.line_endpoint_overrides
        w.redo_edit();settle();assert w.line_endpoint_records()[1]['z']==5.5
        checks.append('一次确认一次撤销，重做恢复手动高程及地图')
        path=root/'.cache'/'endpoint-manual.geoscope.json';w.save_project_to(path)
        w.load_project_from(path);settle();assert w.line_endpoint_records()[1]['z']==5.5
        assert w.points==points
        checks.append('项目保存重开保留手动XYZ，不靠地形重算覆盖Z')
        before=copy.deepcopy(w.line_endpoint_overrides);n=w.history.undo_count
        edit(dict(x=cx+35,y=cy+25,z=10),cancel=True)
        assert w.line_endpoint_overrides==before and w.history.undo_count==n
        edit(dict(x=1e12,y=1e12,z=10),invalid=True)
        assert w.line_endpoint_overrides==before and w.history.undo_count==n
        checks.append('取消与越界拒绝不改坐标、地图或历史')
        edit(dict(x=cx+30,y=cy+20),mode=0)
        end=w.line_endpoint_records()[1]
        assert np.isclose(end['z'],w.terrain.height([[cx+30,cy+20]])[0])
        checks.append('贴地模式修改XY后自动更新终点高程')
        w.add_survey_line();settle();assert len(w.survey_state['lines'])==2
        first=w.survey_state['lines'][0]['id'];other=copy.deepcopy(w.survey_state['lines'][1])
        w.activate_survey_line(first);settle();edit(dict(x=cx+35,y=cy+25,z=3))
        assert w.survey_state['lines'][1]==other and w.points==points
        checks.append('多测线只更新当前线，另线与电极数据保持完整')
        w.save_project_to(path)
        from method_shell import MethodProject
        project=MethodProject.load(path)
        try:
            widget=project.modules['ert']
            assert widget.line_endpoint_records()[1]['z']==3.
            portable=root/'.cache'/'endpoint-portable.geoscope'
            project.save(portable)
        finally:project.shutdown()
        project=MethodProject.load(portable)
        try:
            widget=project.modules['ert']
            assert widget.line_endpoint_records()[1]['z']==3.
            assert len(widget.survey_state['lines'])==2
        finally:project.shutdown()
        checks.append('标准桌面方法入口的完整便携项目包保存重开保留终点高程与多线')
        w.focus_survey_view();settle();w.grab().save(str(root/'endpoint-edit-preview.png'))

        from ert_protocol import read_r2_protocol
        from shortcuts import edit_snapshot
        source=root/'data/borth'
        if not source.is_dir():
            source=root/'output/literature_ert_GJI2021/author_original/2D_Borth'
        packet=read_r2_protocol(source/'protocol.dat',source/'electrodes.dat')
        w.line=[[cx-60,cy],[cx+60,cy]]
        w.line_endpoint_overrides={}
        w.open_observation_packet(packet)
        assert len(w.points)==48 and len(w.ert_packet['rows'])==397
        assert w.line==[[cx-60,cy],[cx+60,cy]]
        np.testing.assert_allclose([p['x'] for p in w.points],cx-60+2*np.arange(48))
        original=edit_snapshot(w)
        history_count=w.history.undo_count
        edit(dict(x=cx, y=cy),mode=0,invalid=True)
        assert edit_snapshot(w)==original and w.history.undo_count==history_count
        edit(dict(x=cx-60,y=cy+120),mode=0)
        np.testing.assert_allclose([p['y'] for p in w.points],cy+2*np.arange(48))
        assert w.observation_geometry_matches()
        w.undo_edit();assert w.line==original['line'] and w.points==original['points']
        w.redo_edit();assert w.line[-1]==[cx-60,cy+120]
        w.focus_survey_view();settle();w.grab().save(str(root/'ert-file-spacing-preview.png'))
        checks.append('Borth 48 电极保持 2 米间距；120 米地图线保留尾段，过短坐标修改拒绝，旋转后可撤销和重做')
        w.error=lambda error:failures.append(str(error))
        w.run_inversion()
        assert w.process is not None,failures
        loop=QtCore.QEventLoop()
        timer=QtCore.QTimer()
        elapsed=QtCore.QElapsedTimer()
        elapsed.start()
        timer.timeout.connect(lambda:loop.quit() if w.process is None or elapsed.elapsed()>180000 else None)
        timer.start(200)
        loop.exec()
        timer.stop()
        assert w.process is None and not failures,failures
        assert w.case_arrays is not None and not w.model_local_only and not w.result_stale
        np.testing.assert_allclose(w.section_data['xy'][[0,-1]],[[cx-60,cy],[cx-60,cy+94]])
        case=w.case_path
        w.survey_dock.hide()
        w.survey_state['lines']=[];w.survey_state['active_id']=None
        w.redraw()
        assert not w.survey_active()
        w.survey_dock.show();settle()
        assert w.survey_state['active_id']=='L01'
        assert w.survey_table.item(0,3).text()=='397' and w.survey_table.item(0,4).text()=='当前'
        assert w.case_path==case and w.survey_state['lines'][0]['workspace']['case']==str(case)
        checks.append('打开右侧列表会自动收录原单线反演，保留已计算剖面与原始观测')
        w.grab().save(str(root/'ert-file-spacing-result-preview.png'))
        checks.append('实际 Borth 反演按电极的 94 米跨度定位剖面，保留 120 米地图线')
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    write_json(root/'endpoint-edit-verification.json',dict(passed=not failures,checks=checks,failures=failures))
    for window in list(w.manager.windows):window.dirty=False;window.close()
    app.exit(1 if failures else 0)
