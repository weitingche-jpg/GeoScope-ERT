"""Prove reversible quality selection and actual 83-of-84 ERT diagnostics."""
import copy
import hashlib
import json
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from observed_ert import read_observations
from ert_quality import active_indices,set_row_state
from calibration import make_proposal
from quality_dialog import QualityDialog
from diagnostics_dialog import DiagnosticsDialog


def run_checks(w,app,root):
    checks=[];failures=[];timer=QtCore.QTimer(w);clock=QtCore.QElapsedTimer()
    def finish():
        timer.stop()
        (root/'ert-quality-verification.json').write_text(json.dumps(dict(
            passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf8')
        w.dirty=False;w.close();app.exit(1 if failures else 0)
    def fail():
        import traceback
        failures.append(traceback.format_exc());finish()
    try:
        w.load_campus()
        source=root/'data'/'field-experiments'/'26102812708.csv'
        if not source.exists():source=root/'电法实验数据'/'解压数据'/'26102812708'/'26102812708.csv'
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        w.open_observation_packet(read_observations(source))
        w.apply_calibration_proposal(make_proposal(w.ert_packet,[240.,100.,0.],90.,w.terrain))
        original=copy.deepcopy(w.ert_packet)
        reason='流程验证暂时停用，不代表原观测存在质量问题'
        def handle_quality():
            modal=Q.QApplication.activeModalWidget()
            try:
                assert isinstance(modal,QualityDialog)
                modal.table.item(0,0).setCheckState(QtCore.Qt.Unchecked)
                modal.table.item(0,10).setText(reason)
                modal.grab().save(str(root/'ert-quality-preview.png'))
                with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(root/'.cache'/'quality-preview.csv'),'')):
                    modal.export_preview()
                assert w.ert_packet==original
                modal.accept()
            except Exception:
                import traceback
                failures.append(traceback.format_exc())
                if modal:modal.reject()
        QtCore.QTimer.singleShot(120,handle_quality);w.open_quality_dialog();assert not failures
        assert len(w.ert_packet['rows'])==84 and len(active_indices(w.ert_packet))==83
        assert [r['rhoa'] for r in w.ert_packet['rows']]==[r['rhoa'] for r in original['rows']]
        assert w.ert_packet['rows'][0]['qc_history'] and w.observation_table.item(0,6).text()=='停用'
        checks.append('真实质检窗口停用首条并记录原因，保留全部84条测量；预览导出不提前修改项目')
        w.undo_edit();assert w.ert_packet==original
        w.redo_edit();assert len(active_indices(w.ert_packet))==83
        checks.append('质检作为一次操作撤销/重做，状态与处理记录一起恢复')
        saved=root/'.cache'/'quality-selected.geoscope.json';w.save_project_to(saved);w.load_project_from(saved)
        assert len(active_indices(w.ert_packet))==83
        at_run=copy.deepcopy(w.ert_packet)
        w.run_inversion();assert w.process is not None,w.log.toPlainText()
        clock.start()
        def after_worker():
            if w.process is not None:
                if clock.elapsed()>90000:
                    timer.stop();w.process.kill();w.process.waitForFinished(3000)
                    failures.append('诊断反演超时');finish()
                return
            timer.stop()
            try:
                data=w.case_arrays;assert data is not None
                np.testing.assert_array_equal(data['observed'],[r['rhoa'] for r in original['rows'][1:]])
                np.testing.assert_array_equal(data['source_row_indices'],np.arange(1,84))
                assert w.response_table.rowCount()==83 and w.response_table.item(0,0).text()=='2'
                assert len(w.ert_packet['rows'])==84 and not w.model_local_only and not w.result_stale
                assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
                checks.append('实际反演仅使用83条启用观测，计算响应映射到原序号2–84；源文件校验值不变')
                meta=json.loads((w.case_path/'metadata.json').read_text(encoding='utf8'))
                details=json.loads((w.case_path/'diagnostics.json').read_text(encoding='utf8'))
                assert meta['observations']==83 and meta['total_observations']==84 and meta['disabled_observations']==1
                history=details['history'];assert len(history)>1
                assert np.isclose(history[-1]['chi2'],meta['chi2'],rtol=1e-5)
                assert np.isclose(history[-1]['rrms_percent'],meta['rrms_percent'],rtol=1e-5)
                assert details['coverage_available'] and len(data['coverage_log10'])==len(data['resistivity'])
                assert np.isfinite(data['coverage_log10']).any()
                checks.append('真实迭代chi2/RRMS与最终指标一致，覆盖度数组对应每个模型单元')
                diagnostic=DiagnosticsDialog(w,data,meta,details,at_run,False)
                diagnostic.show();app.processEvents()
                assert diagnostic.table.item(0,0).text()=='2' and not diagnostic.messages
                diagnostic.tabs.setCurrentIndex(2);app.processEvents();diagnostic.grab().save(str(root/'ert-convergence-preview.png'))
                diagnostic.tabs.setCurrentIndex(1);app.processEvents();diagnostic.grab().save(str(root/'ert-pseudosection-preview.png'))
                diagnostic.tabs.setCurrentIndex(3);app.processEvents();diagnostic.grab().save(str(root/'ert-coverage-preview.png'))
                with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(root/'.cache'/'diagnostic-record.json'),'')):
                    diagnostic.export_record()
                assert (root/'.cache'/'diagnostic-record.json').exists();diagnostic.reject()
                checks.append('诊断图、原行明细及导出记录可用；分布纵轴明确为装置间距而非深度')
                w.apply_quality_packet(set_row_state(w.ert_packet,[0],True,'恢复参与反演'))
                assert w.result_stale
                old=DiagnosticsDialog(w,data,meta,details,at_run,True);assert old.stale;old.reject()
                all_off=set_row_state(w.ert_packet,range(84),False,'全部待复核（流程测试）')
                w.apply_quality_packet(all_off);assert not w.run_btn.isEnabled()
                draft=root/'.cache'/'quality-all-disabled.geoscope.json';w.save_project_to(draft);w.load_project_from(draft)
                assert not active_indices(w.ert_packet) and not w.run_btn.isEnabled() and w.result_stale
                checks.append('恢复启用标记旧结果过期；全部停用可保存重开草稿，但禁止反演')
                w.apply_quality_packet(at_run);assert not w.result_stale
                legacy_meta=copy.deepcopy(meta);legacy_meta.pop('observation_signature',None)
                w._load_workflow_case(w.case_path,legacy_meta);w.redraw()
                assert w.result_stale and '签名' in w.result_status.text()
                w._load_workflow_case(w.case_path,meta);w.redraw();assert not w.result_stale
                checks.append('缺少输入签名的旧案例明确标记无法验证，不冒充当前启用集结果')
                demo=root/'.cache'/'ert-quality-demo.geoscope.json';w.save_project_to(demo)
                w.set_workflow_step(2);w.grab().save(str(root/'ert-quality-workbench-preview.png'))
                finish()
            except Exception:fail()
        timer.timeout.connect(after_worker);timer.start(150)
    except Exception:fail()
