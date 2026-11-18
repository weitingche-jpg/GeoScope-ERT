"""Native acceptance of the explicit nine-line assumed survey, not field 3-D truth."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q,QtTest
from core import write_json
from survey_project import survey_signature
from ert_quality import set_row_state


def run_checks(w,app,root):
    checks=[];failures=[];timers=[];progress=QtCore.QElapsedTimer();heartbeat=[0]
    pulse=QtCore.QTimer(w);pulse.timeout.connect(lambda:heartbeat.__setitem__(0,heartbeat[0]+1));pulse.start(50);timers.append(pulse)
    def finish():
        for timer in timers:timer.stop()
        write_json(root/'survey3d-verification.json',dict(passed=not failures,checks=checks,failures=failures,heartbeat=heartbeat[0]))
        for window in list(w.manager.windows):window.dirty=False;window.close()
        app.exit(1 if failures else 0)
    def fail():
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True);finish()
    def settle():app.processEvents();app.processEvents();w.scalar_legend.update_layout();app.processEvents()
    def wait_job(callback,seconds=600):
        timer=QtCore.QTimer(w);clock=QtCore.QElapsedTimer();clock.start();timers.append(timer)
        def poll():
            if w.process is not None or w.survey_queue:
                if clock.elapsed()>seconds*1000:
                    w.survey_queue=[]
                    if w.process is not None:w.process.kill();w.process.waitForFinished(3000)
                    failures.append('计算超时');timer.stop();finish()
                return
            timer.stop()
            try:callback()
            except Exception:fail()
        timer.timeout.connect(poll);timer.start(200)
    try:
        w.load_campus();paths=sorted((root/'data'/'hypothetical-survey').glob('*.csv'))
        assert len(paths)==9
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        w.load_hypothetical_survey(paths);settle()
        assert len(w.manager.windows)==1 and len(w.survey_state['lines'])==9 and w.survey_table.rowCount()==9
        assert w.survey_state['hypothesis'] and not w.survey_volume_btn.isEnabled()
        disabled_image=w.survey_volume_btn.grab().toImage()
        assert disabled_image.pixelColor(disabled_image.width()-20,disabled_image.height()//2).lightness()<100,'不可用的三维按钮应呈暗色'
        starts=np.array([line['workspace']['points'][0]['y'] for line in w.survey_state['lines']])
        np.testing.assert_allclose(np.diff(starts),2.)
        for line in w.survey_state['lines']:
            ws=line['workspace'];assert len(ws['points'])==24 and len(ws['ert_packet']['rows'])==84
            assert np.isclose(np.linalg.norm(np.subtract(*np.asarray(ws['line'])[::-1])),46.)
        first_packet=copy.deepcopy(w.ert_packet)
        w.activate_survey_line(w.survey_state['lines'][3]['id']);assert w.survey_state['active_id']==w.survey_state['lines'][3]['id']
        w.activate_survey_line(w.survey_state['lines'][0]['id']);assert w.ert_packet==first_packet
        checks.append('9条假设平行测线在同一窗口编辑切换；每线24电极/2m/46m，共756条观测；未生成剖面时三维按钮禁用')
        before=copy.deepcopy(w.survey_state)
        def accept_preview():
            modal=Q.QApplication.activeModalWidget()
            assert isinstance(modal,Q.QDialog)
            table=modal.findChild(Q.QTableWidget);assert table is not None and table.rowCount()==9
            modal.grab().save(str(root/'survey-parallel-preview.png'));modal.accept()
        QtCore.QTimer.singleShot(100,accept_preview);w.preview_parallel_calibration();settle()
        assert Q.QApplication.activeModalWidget() is None and len(w.manager.windows)==1
        assert [l['workspace']['ert_packet']['rows'] for l in w.survey_state['lines']]==[l['workspace']['ert_packet']['rows'] for l in before['lines']]
        w.grab().save(str(root/'survey-lines-preview.png'))
        checks.append('九线平行校准有预览且不修改观测值，确认后未遗留模态框或创建新主窗口')
        w.run_survey_sections();assert w.process is not None,w.log.toPlainText();progress.start()
        def after_sections():
            assert all(w.survey_line_current(line) for line in w.survey_state['lines']),w.log.toPlainText()
            for line in w.survey_state['lines']:
                meta=json.loads((Path(line['workspace']['case'])/'metadata.json').read_text(encoding='utf8'))
                assert meta.get('geometry_hypothesis') is True,'二维案例也必须保留假设几何标记'
            assert len([s for s in w.region_state['sections'] if s.get('survey_line_id')])==9
            w.refresh_survey_ui();assert w.survey_volume_btn.isEnabled(),w.survey_gate_note.text()
            w.survey_state['view']='sections';w.redraw();settle();w.focus_survey_view()
            w.grab().save(str(root/'survey-sections-preview.png'))
            checks.append('9条线分别完成实际二维反演、同屏保留9个剖面，数据条件通过后三维按钮启用')
            if hasattr(w,'commit_line_endpoint'):
                end=w.line_endpoint_records()[1];original_model=w.model.points.copy();original_points=copy.deepcopy(w.points)
                w.commit_line_endpoint(1,dict(x=end['x']+1,y=end['y'],z=end['z'],height_mode='manual'))
                settle()
                assert w.result_stale and not w.survey_volume_btn.isEnabled()
                np.testing.assert_allclose(w.model.points,original_model)
                w.undo_edit();settle()
                assert not w.result_stale and w.survey_volume_btn.isEnabled()
                w.commit_line_endpoint(1,dict(x=end['x'],y=end['y'],z=end['z']+3,height_mode='manual'))
                settle()
                assert w.result_stale,'仅改变终点Z后旧二维结果未标过期'
                assert not w.survey_line_current(w.active_survey_line()) and not w.survey_volume_btn.isEnabled()
                np.testing.assert_allclose(w.model.points,original_model)
                assert w.points==original_points
                w.undo_edit();settle()
                assert not w.result_stale and w.survey_volume_btn.isEnabled(),'撤销终点Z未恢复反演状态'
            w.run_survey_volume();assert w.process is not None,w.log.toPlainText()
            pulses=heartbeat[0]
            def after_volume():
                assert heartbeat[0]>pulses+5,'后台计算时主界面事件循环未保持响应'
                assert w.volume_arrays is not None and w.survey_volume_current(),w.log.toPlainText()
                a=w.volume_arrays;meta=w.volume_metadata
                assert a['sensors'].shape==(216,3) and len(a['observed'])==len(a['response'])==756
                assert a['nodes'].shape[1]==3 and a['cells'].shape[1]==6
                assert len(meta['source_mapping'])==756 and meta['geometry_hypothesis'] is True
                assert meta['dimension'].startswith('3D ERT') and np.isfinite(a['resistivity']).all()
                rrms=np.sqrt(np.mean(((a['response']-a['observed'])/a['observed'])**2))*100
                assert np.isclose(rrms,meta['rrms_percent'],rtol=1e-5)
                assert w.survey_volume_actor is not None
                settle();w.focus_survey_view();settle();w.grab().save(str(root/'survey-volume-preview.png'))
                w.plotter.screenshot(str(root/'survey-volume-scene.png'))
                w.survey_view.setCurrentIndex(0);settle();assert w.survey_state['view']=='sections' and w.survey_volume_actor is None
                w.survey_view.setCurrentIndex(1);settle();assert w.survey_volume_actor is not None
                checks.append('真正三维有限元反演使用216个全局电极和756条原始观测，保存3D单元/响应/来源映射；后台计算保持响应，三维与多剖面可切换')
                original=copy.deepcopy(w.ert_packet)
                w.apply_quality_packet(set_row_state(w.ert_packet,[0],False,'验收：暂时停用用于检查结果过期'))
                assert not w.survey_volume_current() and not w.survey_volume_btn.isEnabled() and w.survey_volume_actor is None
                w.undo_edit();settle();assert w.ert_packet==original and w.survey_volume_current()
                project=root/'.cache'/'nine-line-hypothetical.geoscope.json';w.save_project_to(project)
                previous=copy.deepcopy(w.survey_state);w.load_project_from(project);settle()
                assert w.survey_state==previous and w.survey_volume_current()
                assert w.volume_metadata['geometry_hypothesis'] is True
                assert '假设' in w.result_status.text()
                assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p.name] for p in paths)
                checks.append('质检修改使三维结果过期并禁用重新计算，撤销可恢复；项目保存重开保留九线/三维/假设标记，原数据哈希不变')
                w.open_region_dialog();app.processEvents()
                assert Q.QApplication.activeModalWidget() is None,'旧区域管理仍应用模态'
                dialog=getattr(w,'_region_dialog',None)
                assert dialog is not None
                child=w.new_region_workbench();app.processEvents()
                assert Q.QApplication.activeModalWidget() is None
                QtTest.QTest.mouseClick(child.step_buttons[0],QtCore.Qt.LeftButton);app.processEvents()
                assert child.workflow_step==0
                child.dirty=False;child.close();dialog.close()
                checks.append('旧区域管理改为非模态；即使打开独立窗口也能点击操作，不再被遮住的原管理框占用输入')
                w.focus_survey_view();settle();w.grab().save(str(root/'survey-final-preview.png'))
                finish()
            wait_job(after_volume,600)
        wait_job(after_sections,600)
    except Exception:fail()


def run_reopen_checks(w,app,root):
    """Fast lifecycle/portable-project verification against already computed data."""
    checks=[];failures=[];projects=[]
    try:
        from method_shell import MethodProject
        source=root/'.cache'/'nine-line-hypothetical.geoscope.json'
        w.load_project_from(source);app.processEvents()
        assert len(w.survey_state['lines'])==9 and w.survey_volume_current()
        protected=copy.deepcopy(w.survey_state)
        with patch.object(Q.QMessageBox,'question',return_value=Q.QMessageBox.Cancel):w.load_hypothetical_survey()
        assert w.survey_state==protected
        assert w.volume_arrays['sensors'].shape==(216,3)
        w.survey_view.setCurrentIndex(0);app.processEvents();assert w.survey_volume_actor is None
        w.survey_view.setCurrentIndex(1);app.processEvents();assert w.survey_volume_actor is not None
        project=MethodProject.load(source);projects.append(project)
        ert=project.modules['ert'];assert len(ert.survey_state['lines'])==9 and ert.survey_volume_current()
        out=root/'.cache'/'nine-line-portable.geoscope'
        assert project.save(out)
        restored=MethodProject.load(out);projects.append(restored)
        widget=restored.modules['ert']
        assert len(widget.survey_state['lines'])==9 and widget.survey_volume_current()
        assert len(widget.volume_arrays['observed'])==756 and widget.volume_metadata['geometry_hypothesis']
        for line in widget.survey_state['lines']:
            ws=line['workspace']
            assert Path(ws['case']).is_dir() and Path(ws['ert_packet']['source_file']).is_file()
            assert str(ws['ert_packet']['source_file']).startswith(str(restored.runtime))
        checks.append('标准方法标签完整项目包保存重开保留九线、原始观测副本、二维案例及三维结果，资源引用均恢复至解包运行目录')
        other=project.new_host();project.move_module('ert',other)
        assert project.host_for('ert') is other
        assert ert.survey_volume_current()
        project.close_modules(['ert'],decisions={'ert':'discard'},save=False)
        project.open_module('ert',other);assert project.modules['ert'] is ert
        checks.append('电法标签移动、隐藏重开保持测网状态，Qt重挂载未发生原生异常')
        w.opacity.setValue(25);w.focus_survey_view();app.processEvents();w.scalar_legend.update_layout()
        w.grab().save(str(root/'survey-final-preview.png'))
        for p in projects:p.shutdown()
        projects=[]
        checks.append('项目关闭清理可正常完成；保留Qt辅助功能原状态，仅在控件重挂载期间使用临时保护')
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    finally:
        for p in projects:
            try:p.shutdown()
            except Exception:pass
        write_json(root/'survey3d-reopen-verification.json',dict(passed=not failures,checks=checks,failures=failures))
        w.dirty=False;w.close();app.exit(1 if failures else 0)
