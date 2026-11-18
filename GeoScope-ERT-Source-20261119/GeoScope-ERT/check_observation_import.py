"""Exercise generic import in an actual workbench, including frozen builds."""
import copy
import json
from unittest.mock import patch
from PySide6 import QtCore, QtTest, QtWidgets as Q
from observation_import_dialog import ObservationImportDialog
from observation_workflow import ObservationDialog


def run_checks(w,app,root):
    checks=[];failures=[]
    def finish():
        (root/'observation-import-verification.json').write_text(json.dumps(
            dict(passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
        w.dirty=False;w.close();app.exit(1 if failures else 0)
    try:
        w.load_campus()
        w.commit_point(dict(id='KEEP',x=300.,y=300.,z=0.,elevation_source='地形插值'))
        previous=copy.deepcopy(w.points);terrain=w.terrain.source
        windows=len(w.manager.windows)
        path=root/'data'/'observation-samples'/'温纳观测模板.xlsx'
        stages=[]
        def handle():
            try:
                modal=Q.QApplication.activeModalWidget()
                if isinstance(modal,ObservationImportDialog):
                    stages.append('mapping');modal.spacing.setValue(2);modal.confirm.setChecked(True)
                    assert modal.ok.isEnabled()
                    modal.grab().save(str(root/'observation-import-preview.png'))
                    modal.accept()
                    QtCore.QTimer.singleShot(80,handle)
                elif isinstance(modal,ObservationDialog):
                    stages.append('confirm')
                    assert modal.ok.isEnabled() and not modal.confirm.isChecked()
                    before=copy.deepcopy(modal.packet)
                    QtTest.QTest.mouseClick(modal.ok,QtCore.Qt.LeftButton)
                    assert modal.result()!=Q.QDialog.Accepted and modal.packet==before
                    assert modal.confirm_error.isVisible() and modal.focusWidget()==modal.confirm
                    modal.grab().save(str(root/'observation-confirmation-preview.png'))
                    QtTest.QTest.mouseClick(modal.confirm,QtCore.Qt.LeftButton,
                        pos=QtCore.QPoint(10,modal.confirm.height()//2))
                    assert modal.confirm.isChecked()
                    QtTest.QTest.mouseClick(modal.ok,QtCore.Qt.LeftButton)
                    checks.append('最终确认按钮可点击；未勾选时解释并聚焦，实际点击确认后导入成功')
                else:raise AssertionError('未打开预期导入窗口')
            except Exception:
                import traceback
                failures.append(traceback.format_exc())
                if Q.QApplication.activeModalWidget():Q.QApplication.activeModalWidget().reject()
        QtCore.QTimer.singleShot(120,handle)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(path),'')):
            w.import_observations()
        assert stages==['mapping','confirm'] and not failures
        assert len(w.manager.windows)==windows and w.terrain.source==terrain and w.points==previous
        packet=copy.deepcopy(w.ert_packet)
        assert packet['import_format']=='generic_table' and len(packet['rows'])==8
        assert packet['source_table']=='观测表' and packet['rows'][0]['source_row']==3
        assert packet['rows'][0]['abmn']==[0,3,1,2] and packet['rows'][0]['rhoa']==82.5
        assert packet['spacing']==2 and packet['binding']['mode']=='local'
        checks.append('XLSX真实字段映射与最终确认，8条观测进入同一窗口；地形与测点保持')
        w.undo_edit();assert w.ert_packet is None
        w.redo_edit();assert w.ert_packet==packet
        checks.append('通用表导入一次撤销与重做，保留来源工作表、行号、单位和字段映射')
        project=root/'.cache'/'generic-import-project.geoscope.json'
        w.save_project_to(project);w.load_project_from(project)
        assert w.ert_packet==packet and w.points==previous
        checks.append('通用观测保存与重开恢复，未自动启动反演或合成观测')
        bad=ObservationImportDialog(w,root/'data'/'observation-samples'/'错误示例.csv')
        bad.spacing.setValue(2);bad.confirm.setChecked(True)
        assert not bad.ok.isEnabled() and any(r['error'] for r in bad.records)
        bad.reject();assert w.ert_packet==packet
        checks.append('错误表逐行提示并阻止继续，取消不修改原观测')
        w.grab().save(str(root/'generic-observations-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
    finish()
