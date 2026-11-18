"""Verify rebuilding a manual line and placing an already computed profile."""
import copy
import json
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from observed_ert import read_observations
from calibration_workflow import CalibrationDialog


def run_checks(w,app,root):
    checks=[];failures=[]
    timer=QtCore.QTimer(w);clock=QtCore.QElapsedTimer()
    def finish():
        timer.stop()
        (root/'calibration-verification.json').write_text(json.dumps(dict(
            passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf8')
        w.dirty=False;w.close();app.exit(1 if failures else 0)
    def fail():
        import traceback
        failures.append(traceback.format_exc());finish()
    try:
        w.load_campus()
        raw=root/'data'/'field-experiments'/'26102812708.csv'
        if not raw.exists():raw=root/'电法实验数据'/'解压数据'/'26102812708'/'26102812708.csv'
        w.open_observation_packet(read_observations(raw))
        original=[dict(id=f'E{i+1:02}',x=240.,y=100.+100.*i/29,z=0.,elevation_source='手动布点') for i in range(30)]
        w.points=copy.deepcopy(original);w.line=[[240.,100.],[240.,200.]];w.points_follow_line=True;w.redraw()
        w.line_endpoint_overrides={'0':dict(x=240.,y=100.,z=0.,height_mode='terrain'),
                                   '1':dict(x=240.,y=200.,z=0.,height_mode='terrain')}
        original_overrides=copy.deepcopy(w.line_endpoint_overrides)
        original_packet=copy.deepcopy(w.ert_packet)
        w.run_inversion();assert w.process is not None,w.log.toPlainText()
        clock.start()
        def after():
            if w.process is not None:
                if clock.elapsed()>90000:
                    timer.stop();w.process.kill();w.process.waitForFinished(3000)
                    failures.append('反演超时');finish()
                return
            timer.stop()
            try:
                assert w.model is not None and w.model_local_only
                old_values=w.model['resistivity'].copy()
                preview=CalibrationDialog(w)
                assert preview.calculate()
                assert len(preview.proposal['points'])==24
                preview.reject()
                assert w.points==original and w.ert_packet==original_packet
                checks.append('30点100m冲突预览可取消，地图和观测不变')
                def accept():
                    modal=Q.QApplication.activeModalWidget()
                    try:
                        assert isinstance(modal,CalibrationDialog)
                        modal.method.setCurrentIndex(0)
                        assert modal.calculate()
                        modal.grab().save(str(root/'calibration-preview.png'))
                        modal.confirm.setChecked(True);modal.accept()
                    except Exception:
                        import traceback
                        failures.append(traceback.format_exc())
                        if modal:modal.reject()
                QtCore.QTimer.singleShot(120,accept)
                w.calibrate_observations()
                assert not failures
                assert len(w.points)==24 and w.ert_packet['binding']['mode']=='registered'
                np.testing.assert_allclose(w.line,[[240.,100.],[240.,146.]],atol=1e-8)
                np.testing.assert_allclose(np.diff([p['y'] for p in w.points]),2,atol=1e-8)
                assert w.ert_packet['rows']==original_packet['rows'] and w.ert_packet.get('calibration_backup')
                checks.append('保持起点方向，30点100m调整为24点46m/2m，原布局随项目备份')
                assert not w.model_local_only and w.model_actor is not None and w.process is None
                np.testing.assert_allclose(w.model['resistivity'],old_values)
                np.testing.assert_allclose((w.model.points+w.terrain.origin)[:,0],240,atol=1e-7)
                checks.append('已有地下剖面同步显示到三维，观测和物性不变，无需重算')
                w.undo_edit();assert w.points==original and w.ert_packet==original_packet and w.model_local_only
                assert w.line_endpoint_overrides==original_overrides
                w.redo_edit();assert len(w.points)==24 and not w.model_local_only
                assert not w.line_endpoint_overrides
                checks.append('一次撤销与重做恢复整套测点、测线、观测绑定和剖面位置')
                project=root/'.cache'/'calibration-project.geoscope.json'
                w.save_project_to(project);w.load_project_from(project)
                assert len(w.points)==24 and not w.model_local_only and w.ert_packet.get('calibration_backup')
                w.focus_result_view();w.grab().save(str(root/'calibration-result-preview.png'))
                checks.append('保存重开保留校准布设、旧布局备份及三维地下剖面')
                finish()
            except Exception:fail()
        timer.timeout.connect(after);timer.start(150)
    except Exception:fail()
