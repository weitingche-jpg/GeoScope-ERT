"""Geometry command entry points, shared by menus and keyboard shortcuts."""
import json
import numpy as np
import pyvista as pv
from shortcuts import edit_snapshot,restore_edit
from core import stations_along


class HistoryActions:
    def setup_history_actions(self):
        menu=self.menuBar().addMenu('编辑')
        self.edit_menu=menu
        self.undo_action=menu.addAction('撤销\tCtrl+Z',self.undo_edit)
        self.redo_action=menu.addAction('重做\tCtrl+Y',self.redo_edit)
        menu.addSeparator()
        menu.addAction('选择当前测线',self.select_line)
        self.delete_action=menu.addAction('删除选中测点 / 测线\tDelete',self.delete_selected)
        self.update_history_actions()

    def document_key(self):
        return json.dumps(dict(terrain=self.terrain.source if self.terrain else None,
            line=self.line,points=self.points,points_follow_line=self.points_follow_line,
            line_endpoint_overrides=getattr(self,'line_endpoint_overrides',{}) or {},
            case=str(self.case_path) if self.case_path else None,observations=self.ert_packet,simulation_enabled=self.simulation_enabled,
            region=getattr(self,'region_state',None),survey=getattr(self,'survey_state',None)),sort_keys=True,ensure_ascii=False)

    def mark_saved(self):
        self.saved_geometry_key=self.document_key()
        self.dirty=False

    def reset_history(self):
        self.history.clear()
        self.mark_saved()
        self.update_history_actions()

    def record_edit(self,before,label):
        if hasattr(self,'sync_survey_line'):self.sync_survey_line()
        after=edit_snapshot(self)
        if before is not None:
            self.history.record(before,after,label)
        self.dirty=self.document_key()!=self.saved_geometry_key
        self.update_history_actions()

    def update_history_actions(self):
        if not hasattr(self,'undo_action'):
            return
        ready=self.process is None and self.drag_endpoint is None and self.mode!='line' and not getattr(self,'line_drag_active',lambda:False)() and not (hasattr(self,'survey_busy') and self.survey_busy())
        self.undo_action.setEnabled(ready and bool(self.history.past))
        self.redo_action.setEnabled(ready and bool(self.history.future))
        self.undo_action.setText(('撤销 '+self.history.past[-1][2] if self.history.past else '撤销')+'\tCtrl+Z')
        self.redo_action.setText(('重做 '+self.history.future[-1][2] if self.history.future else '重做')+'\tCtrl+Y')
        self.delete_action.setEnabled(ready and self.selected is not None and self.selected[0] in ('point','line'))

    def undo_edit(self):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，暂不撤销几何编辑。')
            return
        if self.drag_endpoint is not None or self.mode=='line':
            self.cancel_current_action()
            return
        result=self.history.undo()
        self._apply_history(result,'撤销')

    def redo_edit(self):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，暂不重做几何编辑。')
            return
        if not self.require_committed_edit():
            return
        self._apply_history(self.history.redo(),'重做')

    def _apply_history(self,result,verb):
        if result is None:
            self.say('没有可以'+verb+'的几何编辑。')
            return
        state,label=result
        restore_edit(self,state)
        self.line_snapshot=None
        self.mode='browse'
        self.dirty=self.document_key()!=self.saved_geometry_key
        self.set_mode('browse')
        self.say(verb+'：'+label+'；原反演模型未移动。')

    def select_line(self):
        if self.terrain is None or len(self.line)<2:
            self.say('当前没有可选择的完整测线。')
            return
        self.clear_selection()
        s,xy=stations_along(self.line,150)
        points=np.column_stack([xy,self.terrain.height(xy)+2])-self.terrain.origin
        self.plotter.add_mesh(pv.lines_from_points(points),color='#fff1ad',line_width=7,
            name='selection',pickable=False,reset_camera=False)
        self.selected=('line',0)
        summary=self.line_summary_text() if hasattr(self,'line_summary_text') else ''
        self.inspector.setText((summary+'\n' if summary else f'可编辑测线  |  长度 {s[-1]:.2f} m  |  ')+
            'Delete 删除，Ctrl+Z 恢复\n删除测线会同时移除随线自动生成的测点，保留独立测点和原反演模型。')
        self.update_history_actions()
        if hasattr(self,'show_coordinate_selection'):
            self.show_coordinate_selection('line',0)

    def delete_selected(self):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or not self.require_committed_edit():
            self.say('请先结束计算或完成当前编辑，再删除。')
            return
        if self.selected is None or self.selected[0] not in ('point','line'):
            self.say('请选择工作台测点或测线；原反演电极和地下模型只读。')
            return
        before=edit_snapshot(self)
        kind,index=self.selected
        if kind=='point':
            if self.ert_packet and self.ert_packet.get('binding',{}).get('mode')=='registered':
                from workflow_geometry import electrode_ids
                from PySide6 import QtWidgets as Q
                ids=electrode_ids(self.ert_packet);name=self.points[index]['id']
                if name in ids:
                    electrode=ids.index(name);refs=[r for r in self.ert_packet['rows'] if electrode in r['abmn']]
                    if refs and Q.QMessageBox.question(self,'电极正在被观测引用',f'{name} 被 {len(refs)} 条观测引用。删除后须修复关联才能计算，是否继续？')!=Q.QMessageBox.Yes:return
            label='删除测点 '+self.points[index]['id']
            del self.points[index]
            self.points_follow_line=False
        else:
            label='删除测线'
            self.line=[]
            self.line_endpoint_overrides={}
            if self.points_follow_line:
                self.points=[]
            self.points_follow_line=False
        self.line_snapshot=None
        self.mode='browse'
        self.record_edit(before,label)
        self.set_mode('browse')
        self.say(label+'；Ctrl+Z 可恢复。原反演案例数据保持不变。')
