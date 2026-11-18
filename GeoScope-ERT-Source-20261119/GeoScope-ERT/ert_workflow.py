"""Quality and diagnostic actions for the existing three-step workbench."""
import copy
import json
from PySide6 import QtWidgets as Q
from observed_ert import validate_packet
from shortcuts import edit_snapshot
from quality_dialog import QualityDialog
from diagnostics_dialog import DiagnosticsDialog


class ERTQualityWorkflow:
    def open_quality_dialog(self):
        if not self.ert_packet:self.say('请先导入电法观测。');return
        if self.process is not None or not self.require_committed_edit():
            self.say('请结束当前编辑或等待计算完成后再做质检。');return
        dialog=QualityDialog(self,self.ert_packet)
        if dialog.exec()==Q.QDialog.Accepted:
            try:self.apply_quality_packet(dialog.packet)
            except (ValueError,KeyError,TypeError) as error:self.error(error)

    def apply_quality_packet(self,packet):
        if self.process is not None:raise ValueError('计算过程中不能改变观测启用状态。')
        validate_packet(packet,allow_empty=True)
        before=edit_snapshot(self);self.ert_packet=copy.deepcopy(packet)
        self.record_edit(before,'应用电法观测质检');self.redraw();self.refresh_workflow()
        self.say('质检设置已应用；原始测量值保留，启用集改变会标记旧结果过期。Ctrl+Z 可整次撤销。')

    def open_diagnostics_dialog(self):
        if self.process is not None:self.say('请等待计算完成后查看诊断。');return
        if not self.case_arrays or 'observed' not in self.case_arrays:
            self.say('请先完成反演或加载已有结果。');return
        try:
            def load(name):
                path=self.case_path/name
                return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
            metadata=load('metadata.json');details=load('diagnostics.json')
            packet=load('observations.json') or None
            dialog=DiagnosticsDialog(self,self.case_arrays,metadata,details,packet,self.result_stale)
            dialog.exec()
        except (OSError,ValueError,KeyError,TypeError) as error:self.error(error)
