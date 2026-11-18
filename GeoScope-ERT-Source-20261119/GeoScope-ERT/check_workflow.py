"""End-to-end checks for the single-document three-step workbench."""
import copy
import json
import shutil
import sys
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from observation_workflow import ObservationDialog
from workflow_registration import RegistrationDialog
from workflow_geometry import observation_signature


def run_checks(w,app,root):
    checks=[];failures=[];timers=[]
    def finish():
        for t in timers:t.stop()
        (root/'workflow-verification.json').write_text(json.dumps(dict(passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
        for window in list(w.manager.windows):window.dirty=False;window.close()
        app.exit(1 if failures else 0)
    def fail():
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True);finish()
    def wait_worker(callback):
        assert w.process is not None
        timer=QtCore.QTimer(w);clock=QtCore.QElapsedTimer();clock.start();timers.append(timer)
        def poll():
            if w.process is not None:
                if clock.elapsed()>90000:
                    timer.stop();w.process.kill();w.process.waitForFinished(3000)
                    failures.append('计算超时');finish()
                return
            timer.stop()
            try:callback()
            except Exception:fail()
        timer.timeout.connect(poll);timer.start(120)
    try:
        assert w.terrain is None and w.workflow_step==0
        w.grab().save(str(root/'workflow-start-preview.png'))
        w.run_inversion();assert w.process is None
        w.load_campus();assert w.workflow_step==1 and not w.simulation_enabled
        w.run_inversion();assert w.process is None
        checks.append('普通空白启动：地形→数据；未提供观测且未选演示时禁止自动生成合成数据')
        w.commit_point(dict(id='USER',x=450.,y=300.,z=0.,elevation_source='地形插值'))
        original=copy.deepcopy(w.points);terrain=w.terrain.source;windows=len(w.manager.windows)
        file=root/'data'/'field-experiments'/'26102812708.csv'
        if not file.exists():file=root/'电法实验数据'/'解压数据'/'26102812708'/'26102812708.csv'
        def accept_import():
            modal=Q.QApplication.activeModalWidget();assert isinstance(modal,ObservationDialog)
            modal.confirm.setChecked(True);modal.accept()
        QtCore.QTimer.singleShot(100,accept_import)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(file),'')):w.import_observations()
        assert len(w.manager.windows)==windows and w.terrain.source==terrain and w.points==original
        assert w.ert_packet['binding']['mode']=='local' and len(w.ert_packet['rows'])==84
        assert not any(i['level']=='错误' for i in w.workflow_checks())
        checks.append('观测导入当前项目：不新建窗口，不替换地形/原测点；显式仅局部模式')
        old=copy.deepcopy(w.ert_packet)
        def edit_first():
            modal=Q.QApplication.activeModalWidget();modal.rhoa.setText(str(old['rows'][0]['rhoa']+1));modal.confirm.setChecked(True);modal.accept()
        QtCore.QTimer.singleShot(100,edit_first);w.edit_observation_record(0)
        assert w.ert_packet['rows'][0]['edited'] and 'source_record' in w.ert_packet['rows'][0]
        w.undo_edit();assert w.ert_packet==old
        last=copy.deepcopy(old['rows'][-1]);w.observation_table.setCurrentCell(83,0);w.delete_observation()
        assert len(w.ert_packet['rows'])==83
        def add_last():
            modal=Q.QApplication.activeModalWidget()
            for widget,value in zip(modal.abmn,last['abmn']):widget.setValue(value+1)
            modal.rhoa.setText(str(last['rhoa']));modal.confirm.setChecked(True);modal.accept()
        QtCore.QTimer.singleShot(100,add_last);w.add_observation()
        assert len(w.ert_packet['rows'])==84
        w.undo_edit();w.undo_edit();assert w.ert_packet==old
        checks.append('观测手动编辑/删除/新增，保留原记录并支持整次撤销')
        w.set_workflow_step(2)
        with patch.object(sys,'executable',str(root/'.cache'/'missing-three-step-worker.exe')):
            w.run_inversion()
            for _ in range(5):app.processEvents()
            if w.process is not None:w.process.waitForStarted(1000);app.processEvents()
        assert w.process is None and w.run_btn.isEnabled() and w.workflow_error.isEnabled()
        checks.append('计算进程启动失败后按钮和高级参数恢复，可重试')
        w.run_inversion()
        def after_local():
            assert w.model is not None and w.model_local_only and w.model_actor is None
            assert w.terrain.source==terrain and w.points==original and w.response_table.rowCount()==84
            with np.load(w.case_path/'model.npz') as data:np.testing.assert_array_equal(data['observed'],[r['rhoa'] for r in old['rows']])
            w.select_cell(0);assert '局部剖面' in w.inspector.text()
            saved=root/'.cache'/'workflow-local.json';saved.parent.mkdir(exist_ok=True)
            w.save_project_to(saved);w.load_project_from(saved)
            assert w.model_local_only and w.points==original and len(w.ert_packet['rows'])==84
            checks.append('局部反演只显示二维剖面/响应，原三维地形不误叠加；保存重开恢复')
            dialog=RegistrationDialog(w)
            for edits,values in zip(dialog.fields,[[240,100,0],[240,146,0]]):
                for field,value in zip(edits,values):field.setText(str(value))
            dialog.confirm.setChecked(True);assert dialog.calculate();dialog.accept()
            w.apply_observation_registration(dialog.binding,dialog.points,dialog.line)
            assert not w.model_local_only and w.model_actor is not None and not w.result_stale
            local_case=w.case_path
            assert len(w.points)==24 and w.terrain.source==terrain
            world=w.model.points+w.terrain.origin
            np.testing.assert_allclose(world[:,0],240,atol=1e-7)
            np.testing.assert_allclose(w.section_data['xy'][[0,-1]],[[240,100],[240,146]])
            checks.append('46m端点刚性配准，保留比例并同步电极/测线/旧剖面定位；不重新生成观测')
            renamed=copy.deepcopy(w.points[0]);renamed['id']='RENAME';w.commit_point(renamed,0)
            assert w.observation_table.item(0,1).text()=='RENAME' and not w.result_stale
            w.undo_edit();assert w.observation_table.item(0,1).text()=='E01'
            moved=copy.deepcopy(w.points[0]);moved['x']+=1;w.commit_point(moved,0)
            assert w.result_stale and any(i['level']=='错误' for i in w.workflow_checks())
            with patch.object(Q.QMessageBox,'warning') as warning:w.run_inversion();assert warning.called and w.process is None
            w.undo_edit();assert not w.result_stale
            w.select_point(0);before_delete=copy.deepcopy(w.points)
            with patch.object(Q.QMessageBox,'question',return_value=Q.QMessageBox.No):w.delete_selected()
            assert w.points==before_delete
            with patch.object(Q.QMessageBox,'question',return_value=Q.QMessageBox.Yes):w.delete_selected()
            assert len(w.points)==23 and any('引用未匹配电极' in i['text'] for i in w.workflow_checks())
            w.undo_edit();assert w.points==before_delete and not w.result_stale
            checks.append('电极改名同步观测引用且可撤销；坐标错误标过期、定位问题并阻止计算')
            sig=observation_signature(w.ert_packet)
            w.workflow_error.setValue(4);w.commit_workflow_parameters();assert w.result_stale
            w.undo_edit();assert observation_signature(w.ert_packet)==sig and not w.result_stale
            w.save_project_to(root/'.cache'/'workflow-registered.json')
            w.load_project_from(root/'.cache'/'workflow-registered.json')
            assert not w.model_local_only and not w.result_stale and w.workflow_step==2
            checks.append('参数修改标过期、撤销恢复；已配准项目重开恢复同一地形与剖面')
            w.set_workflow_step(1);w.data_tabs.setCurrentIndex(1);w.grab().save(str(root/'workflow-data-preview.png'))
            w.set_workflow_step(2);w.grab().save(str(root/'workflow-result-preview.png'))
            w.run_inversion()
            def after_registered():
                assert not w.result_stale and not w.model_local_only and w.model_actor is not None
                with np.load(w.case_path/'model.npz') as data:np.testing.assert_array_equal(data['observed'],[r['rhoa'] for r in old['rows']])
                project=w.case_path/'three-step.geoscope.json';w.save_project_to(project)
                (root/'workflow-latest-project.txt').write_text(str(project),encoding='utf-8')
                checks.append('配准后再次使用原始84条观测计算，结果和项目均可保存')
                # A legal empty draft must survive save/reopen, but cannot run.
                w.ert_packet['rows']=[copy.deepcopy(w.ert_packet['rows'][0])];w.redraw()
                w.observation_table.setCurrentCell(0,0);w.delete_observation()
                draft=root/'.cache'/'workflow-empty-draft.json';w.save_project_to(draft);w.load_project_from(draft)
                assert w.ert_packet['rows']==[] and any(i['level']=='错误' for i in w.workflow_checks())
                with patch.object(Q.QMessageBox,'warning') as warning:w.run_inversion();assert warning.called and w.process is None
                w.load_project_from(project)
                checks.append('删除最后一条观测后可保存/重开空草稿，仍阻止反演')
                # A locally computed case can be registered after calculation;
                # its last valid world pose must survive a stale-data save.
                local_meta=json.loads((local_case/'metadata.json').read_text(encoding='utf-8'))
                w.load_workflow_case(local_case,local_meta);w.redraw()
                assert not w.model_local_only
                w.ert_packet['rows'][0]['rhoa']+=1;w.ert_packet['modified']=True;w.redraw()
                assert w.result_stale and not w.model_local_only
                stale=root/'.cache'/'workflow-stale-pose.json';w.save_project_to(stale);w.load_project_from(stale)
                assert w.result_stale and not w.model_local_only
                np.testing.assert_allclose((w.model.points+w.terrain.origin)[:,0],240,atol=1e-7)
                checks.append('配准后修改数据并保存，重开仍保留旧剖面世界位置和过期状态')
                old_path=w.case_path;old_model=w.model.points.copy();old_packet=copy.deepcopy(w.ert_packet)
                wrong=copy.deepcopy(local_meta);wrong['binding']=copy.deepcopy(w.result_binding)
                wrong['binding']['terrain_source']='another-terrain';wrong['binding']['origin'][0]=500
                try:w.load_workflow_case(local_case,wrong);raise AssertionError('未拒绝不匹配案例')
                except ValueError:pass
                w.redraw();assert w.case_path==old_path
                np.testing.assert_array_equal(w.model.points,old_model)
                bad_case=root/'.cache'/'workflow-bad-case';bad_case.mkdir(exist_ok=True)
                shutil.copy2(local_case/'model.npz',bad_case/'model.npz')
                (bad_case/'metadata.json').write_text(json.dumps(wrong),encoding='utf-8')
                bad_document=json.loads(stale.read_text(encoding='utf-8'));bad_document['case']=str(bad_case)
                bad_project=root/'.cache'/'workflow-bad-project.json';bad_project.write_text(json.dumps(bad_document),encoding='utf-8')
                try:w.load_project_from(bad_project);raise AssertionError('未拒绝损坏项目')
                except ValueError:pass
                assert w.case_path==old_path and w.ert_packet==old_packet
                np.testing.assert_array_equal(w.model.points,old_model)
                checks.append('案例/项目加载被拒后完整恢复当前状态，不会在下次重绘叠加拒绝模型')
                w.load_project_from(project);w.focus_result_view();w.grab().save(str(root/'workflow-final-preview.png'))
                finish()
            wait_worker(after_registered)
        wait_worker(after_local)
    except Exception:fail()
