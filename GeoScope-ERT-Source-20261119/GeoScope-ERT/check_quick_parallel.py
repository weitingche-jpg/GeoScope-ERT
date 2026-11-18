"""Native baseline/number/gap layout acceptance; no instrument observations."""
import copy
import numpy as np
from PySide6 import QtCore,QtWidgets as Q,QtTest
from core import write_json


def run_checks(w,app,root):
    checks=[];failures=[];callback_errors=[]
    def settle():
        for _ in range(3):app.processEvents()
    def open_plan(count=3,gap=2.,side='left',cancel=False,invalid=False,capture=False,locate=False):
        def fill():
            dialog=Q.QApplication.activeModalWidget()
            try:
                assert isinstance(dialog,Q.QDialog),'未打开平行线布置窗口'
                assert callable(dialog.window),'对话框覆盖了Qt窗口方法，快捷键无法可靠处理'
                count_field=dialog.findChild(Q.QSpinBox,'parallel-total-count')
                gap_field=dialog.findChild(Q.QDoubleSpinBox,'parallel-line-gap')
                side_field=dialog.findChild(Q.QComboBox,'parallel-side')
                assert count_field is not None and gap_field is not None and side_field is not None
                count_field.setValue(count);gap_field.setValue(gap)
                index=side_field.findData(side);assert index>=0
                side_field.setCurrentIndex(index);QtTest.QTest.qWait(200);settle()
                if locate:
                    dialog.findChild(Q.QPushButton,'quick-parallel-focus').click();settle()
                buttons=dialog.findChild(Q.QDialogButtonBox);assert buttons is not None
                ok=buttons.button(Q.QDialogButtonBox.Ok)
                if capture:
                    dialog.grab().save(str(root/'quick-parallel-dialog-preview.png'))
                    w.grab().save(str(root/'quick-parallel-live-preview.png'))
                if invalid:assert not ok.isEnabled(),'无效方案仍可确认生成'
                if cancel and locate:QtTest.QTest.keyClick(dialog,QtCore.Qt.Key_Escape)
                elif cancel or invalid:buttons.button(Q.QDialogButtonBox.Cancel).click()
                else:
                    assert ok.isEnabled(),'有效方案未启用确认'
                    ok.click()
            except Exception:
                import traceback
                callback_errors.append(traceback.format_exc())
                if isinstance(dialog,Q.QDialog):dialog.reject()
        QtCore.QTimer.singleShot(150,fill)
        buttons=[b for b in w.findChildren(Q.QPushButton) if b.text()=='快速布置平行线']
        assert buttons,'缺少快速布置平行线入口'
        buttons[0].click();settle()
        assert not callback_errors,callback_errors
    try:
        w.load_campus();cx=float(np.mean(w.terrain.x));cy=float(np.mean(w.terrain.y))-10
        folder=root/'.cache'/'quick-parallel-inputs';folder.mkdir(parents=True,exist_ok=True)
        source=folder/'planning-baseline.csv'
        source.write_text('id,x,y,z\n'+''.join(f'E{i+1:02},{cx-23+2*i},{cy},0\n' for i in range(24)),encoding='utf-8')
        w.points=[dict(id=f'E{i+1:02}',x=cx-23+2*i,y=cy,z=0.,elevation_source='验收文件坐标',source_file=str(source)) for i in range(24)]
        w.line=[[cx-23,cy],[cx+23,cy]];w.points_follow_line=False;w.redraw();settle()
        points=copy.deepcopy(w.points);line=copy.deepcopy(w.line)
        state=copy.deepcopy(w.survey_state);n=w.history.undo_count;dirty=w.dirty
        w.plotter.enable_parallel_projection();w.plotter.camera.parallel_scale=70.
        camera=copy.deepcopy(w.plotter.camera_position);terrain_actor=w.terrain_actor
        open_plan(cancel=True,locate=True)
        assert w.survey_state==state and w.points==points and w.line==line and w.history.undo_count==n and w.dirty==dirty
        np.testing.assert_allclose(list(w.plotter.camera_position),list(camera))
        assert np.isclose(w.plotter.camera.parallel_scale,70.),'定位预览后取消没有恢复缩放'
        assert w.terrain_actor is terrain_actor,'预览重建了地形场景'
        checks.append('预览取消不修改基准/数据/历史/相机，不重建地形')
        open_plan(capture=True)
        assert len(w.survey_state['lines'])==3 and w.history.undo_count==n+1
        assert w.points==points and w.line==line
        for index,item in enumerate(w.survey_state['lines']):
            ws=item['workspace'];assert len(ws['points'])==24
            np.testing.assert_allclose(ws['line'],np.array(line)+[0,2*index])
            np.testing.assert_allclose(np.linalg.norm(np.diff(ws['line'],axis=0),axis=1),[46.])
            if index:
                assert ws['ert_packet'] is None and ws['case'] is None and not ws['simulation_enabled']
                assert all('source_file' not in p and '规划' in p['elevation_source'] for p in ws['points'])
                np.testing.assert_allclose([p['x'] for p in ws['points']],[p['x'] for p in points])
        assert w.survey_state['lines'][0]['workspace']['points']==points
        checks.append('总3条含基准，左侧2米平移，每线24点/2米/46米；基准保留，新线无观测/案例/测量来源')
        generated=copy.deepcopy(w.survey_state)
        w.undo_edit();settle();assert w.survey_state['lines']==[] and w.points==points and w.line==line
        w.redo_edit();settle();assert w.survey_state==generated
        checks.append('整组生成一次撤销/重做，旧单线完整恢复')
        open_plan(invalid=True)
        assert w.survey_state==generated,'重合检查修改了项目'
        open_plan(gap=10000.,invalid=True)
        assert w.survey_state==generated,'越界预览修改了项目'
        checks.append('重复位置及越界均禁确认，不裁短/覆盖已有线')
        existing=copy.deepcopy(w.survey_state['lines'])
        open_plan(side='right')
        assert len(w.survey_state['lines'])==5 and w.survey_state['lines'][:3]==existing
        for index,item in enumerate(w.survey_state['lines'][3:],1):
            np.testing.assert_allclose(item['workspace']['line'],np.array(line)+[0,-2*index])
        checks.append('右侧方向正确，向另一侧追加时原有三线保持不变')
        path=root/'.cache'/'quick-parallel-layout.geoscope.json';w.save_project_to(path)
        w.load_project_from(path);settle();assert len(w.survey_state['lines'])==5
        from method_shell import MethodProject
        project=MethodProject.load(path)
        try:
            portable=root/'.cache'/'quick-parallel-layout.geoscope';project.save(portable)
        finally:project.shutdown()
        project=MethodProject.load(portable)
        try:
            widget=project.modules['ert'];assert len(widget.survey_state['lines'])==5
            assert all(l['workspace']['ert_packet'] is None for l in widget.survey_state['lines'][1:])
        finally:project.shutdown()
        checks.append('单线转测网、JSON与标准便携项目包重开保留全部布局')
        w.survey_dock.show();w.focus_survey_view();settle();w.grab().save(str(root/'quick-parallel-preview.png'))
        previous=root/'.cache'/'nine-line-hypothetical.geoscope.json'
        if previous.is_file():
            w.load_project_from(previous);settle()
            assert w.survey_volume_current(),'旧验收三维项目未完整恢复'
            w.activate_survey_line(w.survey_state['lines'][-1]['id']);settle()
            old_case=w.survey_state['volume_case'];rho=w.volume_arrays['resistivity'].copy()
            old_count=len(w.survey_state['lines'])
            open_plan(gap=3.)
            assert len(w.survey_state['lines'])==old_count+2 and not w.survey_volume_current()
            assert not w.survey_volume_btn.isEnabled() and w.survey_state['volume_case']==old_case
            np.testing.assert_array_equal(w.volume_arrays['resistivity'],rho)
            w.undo_edit();settle();assert w.survey_volume_current()
            checks.append('已有实际三维案例新增空观测测线后结果过期，旧体不改，撤销恢复')
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    write_json(root/'quick-parallel-verification.json',dict(passed=not failures,checks=checks,failures=failures))
    for window in list(w.manager.windows):window.dirty=False;window.close()
    app.exit(1 if failures else 0)
