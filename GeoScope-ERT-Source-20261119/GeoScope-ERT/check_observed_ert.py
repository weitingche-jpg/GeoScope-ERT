"""Exercise the actual file import, worker, section, stale guards and save path."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from observation_workflow import ObservationDialog


def run_checks(owner,app,root):
    checks=[];failures=[];manager=owner.manager
    filename=root/'data'/'field-experiments'/'26102812708.csv'
    if not filename.exists():filename=root/'电法实验数据'/'解压数据'/'26102812708'/'26102812708.csv'
    def finish():
        (root/'observed-ert-verification.json').write_text(json.dumps(dict(passed=not failures,
            checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
        for window in list(manager.windows):window.dirty=False;window.close()
        app.exit(1 if failures else 0)
    try:
        def accept():
            modal=Q.QApplication.activeModalWidget()
            assert isinstance(modal,ObservationDialog)
            assert not modal.ok.isEnabled();modal.confirm.setChecked(True);modal.accept()
        QtCore.QTimer.singleShot(100,accept)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(filename),'')):
            owner.import_observations()
        w=manager.windows[-1]
        assert w is not owner and len(w.ert_packet['rows'])==84 and len(w.points)==24
        assert w.ert_packet['rows'][0]['abmn']==[0,3,1,2]
        assert w.observation_geometry_matches() and w.ert_packet['spacing']==2
        assert '局部' in w.info.text() and '未地理定位' in w.scene_badge.text()
        checks.append('实际选择仪器CSV、预览确认、A/M/N/B重排、独立46m局部测线')
        with patch.object(sys,'executable',str(root/'.cache'/'missing-observed-worker.exe')):
            w.run_inversion()
            for _ in range(5):app.processEvents()
            if w.process is not None:w.process.waitForStarted(1000);app.processEvents()
        assert w.process is None and w.run_btn.isEnabled()
        checks.append('真实FailedToStart错误后恢复按钮、释放进程状态，可重试及关闭')
        w.run_inversion();assert w.process is not None
        process=w.process;w.run_inversion();assert w.process is process
        timer=QtCore.QTimer(owner);elapsed=QtCore.QElapsedTimer();elapsed.start()
        def poll():
            if w.process is not None:
                if elapsed.elapsed()>90000:
                    timer.stop();w.process.kill();w.process.waitForFinished(3000)
                    failures.append('观测反演超时');finish()
                return
            timer.stop()
            try:
                assert w.model is not None and w.case_kind=='imported_observations'
                folder=w.case_path
                expected=[r['rhoa'] for r in w.ert_packet['rows']]
                with np.load(folder/'model.npz') as model:
                    np.testing.assert_array_equal(model['observed'],expected)
                    assert len(model['response'])==84
                    np.testing.assert_allclose(model['station'],np.arange(24)*2.)
                metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
                assert metadata['data_kind']=='imported_observations' and metadata['noise_percent'] is None
                assert metadata['source_sha256']==w.ert_packet['source_sha256']
                assert (folder/'imported_observations.dat').exists()
                assert not (folder/'synthetic_observations.dat').exists()
                checks.append('独立进程真实反演：84条输入逐值不变，无合成观测替换，保留来源hash与误差假设')
                assert not w.result_stale and w.section_view.cell_collection is not None
                w.select_cell(0);assert w.selected==('cell',0)
                assert '导入观测' in w.inspector.text() and '合成 ERT' not in w.inspector.text()
                if metadata['chi2']>2:assert '拟合偏差' in w.result_status.text()
                checks.append('三维/二维剖面可显示和查询，正确标明观测来源及拟合偏差')
                project=folder/'first-group.geoscope.json';w.save_project_to(project)
                original=copy.deepcopy(w.points)
                changed=copy.deepcopy(w.points[0]);changed['x']=.5
                assert w.commit_point(changed,0) and w.result_stale
                with patch.object(Q.QMessageBox,'warning') as warning:
                    w.run_inversion();assert warning.called and w.process is None
                w.undo_edit();assert w.points==original and not w.result_stale
                checks.append('编辑电极标记旧结果过期，阻止使用错误几何；撤销恢复有效状态')
                w.load_project_from(project)
                assert len(w.ert_packet['rows'])==84 and w.model is not None and not w.result_stale
                w.view_section();w.plotter.render();w.grab().save(str(root/'observed-ert-preview.png'))
                (root/'observed-ert-latest-project.txt').write_text(str(project),encoding='utf-8')
                checks.append('保存重开恢复观测包、参数、局部参考面及已计算剖面')
            except Exception:
                import traceback
                failures.append(traceback.format_exc());print(failures[-1],flush=True)
            finish()
        timer.timeout.connect(poll);timer.start(150);owner._observed_check_timer=timer
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True);finish()
