"""Station edits share the same geometry history as mouse placement."""
import copy
import math
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from shortcuts import edit_snapshot
from point_import_dialog import PointImportDialog
from line_import import is_local_import


class PointManagement:
    def points_ready(self):
        return self.terrain is not None and self.process is None and self.require_committed_edit()

    def local_point_frame(self):
        return (is_local_import(getattr(self,'ert_packet',None))
                or any(p.get('local_frame') for p in getattr(self,'points',[]) or []))

    def select_point(self,index):
        """Local source electrodes never highlight geographic DEM positions."""
        if not 0<=index<len(getattr(self,'points',[]) or []):
            return
        point=self.points[index]
        if not (point.get('local_frame') or is_local_import(getattr(self,'ert_packet',None))):
            return super().select_point(index)
        self.clear_selection()
        self.selected=('point',index)
        self.update_history_actions()
        self.table.selectRow(index)
        if self.table.item(index,0) is not None:
            self.table.scrollToItem(self.table.item(index,0))
        if hasattr(self.section_view,'highlight_acquisition_point'):
            self.section_view.highlight_acquisition_point(index)
        self.inspector.setText(
            f"局部导入电极 {point['id']} | 里程 {point['x']:.3f} m · Y {point['y']:.1f} m · Z {point['z']:.1f} m\n"
            '局部距离/平地参考值，未配准；不在地形上高亮，配准后才可编辑地理坐标。')
        return

    def import_points(self):
        if self.local_point_frame():
            self.say('局部导入源电极只读；请先配准，配准后再导入或编辑测点。');return
        if not self.points_ready():
            self.say('请先加载地面，并结束计算或当前编辑。');return
        path,_=Q.QFileDialog.getOpenFileName(self,'导入测点：Excel/Word/CSV；R2 DAT 请用“导入观测”','',
            '测点表 (*.xlsx *.docx *.csv);;旧版 Office（需另存新版） (*.xls *.doc)')
        if not path:return
        try:
            survey_active=getattr(self,'survey_active',lambda:False)
            active=getattr(self,'active_survey_line',lambda:None)() if survey_active() else None
            target=f"当前测线 {active['name']}（原点数 {len(self.points)}）" if active is not None else ''
            dialog=PointImportDialog(self,path,target_label=target)
            if dialog.exec()==Q.QDialog.Accepted:self.confirm_point_import(dialog)
        except Exception as error:self.error(error)

    def confirm_point_import(self,dialog):
        """Interactive update confirmation; tests use apply_point_import directly."""
        survey_active=getattr(self,'survey_active',lambda:False)
        if not survey_active():return self.apply_point_import(dialog)
        line=getattr(self,'active_survey_line',lambda:None)()
        if line is None:return self.apply_point_import(dialog)
        mode=dialog.mode.currentIndex()
        if mode not in (1,2):return self.apply_point_import(dialog)
        incoming=list(dialog.imported_points)
        current_count=len(self.points)
        if mode==1:
            existing={p['id'] for p in self.points}
            final_count=current_count+sum(1 for p in incoming if p['id'] not in existing)
            mode_name='按编号更新 / 新增'
        else:
            final_count=len(incoming);mode_name='替换当前测线测点'
        answer=Q.QMessageBox.question(self,'确认更新当前测线',
            f"目标测线：{line['name']}\n当前测点：{current_count} 个 → 导入后：{final_count} 个\n"
            f"方式：{mode_name}\n仅更新当前测线，其他测线保持不变。是否继续？",
            Q.QMessageBox.Yes|Q.QMessageBox.No,Q.QMessageBox.No)
        if answer!=Q.QMessageBox.Yes:
            self.say(f'已取消更新 {line["name"]}；当前测点与历史未改变。');return False
        return self.apply_point_import(dialog)

    def apply_point_import(self,dialog):
        if self.local_point_frame():
            raise ValueError('局部导入源电极只读；请先配准，配准后再导入或编辑测点。')
        if not self.points_ready():return
        incoming=copy.deepcopy(dialog.imported_points)
        if not incoming:raise ValueError('没有可导入的测点。')
        mode=dialog.mode.currentIndex();points=copy.deepcopy(self.points) if mode!=2 else []
        positions={p['id']:i for i,p in enumerate(points)}
        for point in incoming:
            self.validate_point(point,None,check_duplicate=False)
            if point['id'] in positions:
                if mode==0:raise ValueError('追加模式存在重复编号。')
                points[positions[point['id']]]=point
            else:positions[point['id']]=len(points);points.append(point)
        line=copy.deepcopy(self.line)
        if dialog.connect_line.isChecked():
            if len(incoming)<2:raise ValueError('连线至少需要两个测点。')
            line=[[p['x'],p['y']] for p in incoming]
            if any(np.linalg.norm(np.subtract(a,b))<1e-6 for a,b in zip(line,line[1:])):
                raise ValueError('连线存在相邻重合测点，请取消连线或修改文件。')
        before=edit_snapshot(self)
        self.points=points;self.line=line;self.points_follow_line=False
        self.record_edit(before,'智能导入测点');self.redraw()
        survey_active=getattr(self,'survey_active',lambda:False)
        active=getattr(self,'active_survey_line',lambda:None)() if survey_active() else None
        target=f"当前测线 {active['name']}：" if active is not None else ''
        self.say(f'已导入 {len(incoming)} 个测点；{target}编号与坐标按预览确认值保存。测线仅在明确勾选连线时改变。')

    def validate_point(self,point,index,check_duplicate=True):
        if not str(point['id']).strip():raise ValueError('编号不可为空。')
        if check_duplicate and any(p['id']==point['id'] and i!=index for i,p in enumerate(self.points)):
            raise ValueError('编号重复。')
        if not all(math.isfinite(float(point[k])) for k in ('x','y','z')):
            raise ValueError('XYZ 必须是有限数值。')
        if not np.isfinite(self.terrain.height([[point['x'],point['y']]])[0]):
            raise ValueError('坐标超出有效地面范围。')

    def select_result_point(self,index):
        if self.terrain is not None:
            return super().select_result_point(index)
        if self.section_data is None:
            return
        self.clear_selection()
        self.section_view.highlight_point(index)
        self.selected=('result_point',index)
        self.update_history_actions()
        name=f'E{index+1:02}'
        if self.ert_packet and not self.result_stale:
            from workflow_geometry import electrode_ids
            ids=electrode_ids(self.ert_packet)
            if index<len(ids):name=ids[index]
        self.inspector.setText(f'局部反演电极 {name} | 未配准，仅二维显示；平地参考。')

    def select_line(self):
        if self.local_point_frame():
            self.say('局部导入测线未配准：不在地形上高亮或选择；请先配准。');return
        return super().select_line()

    def focus_selected(self):
        selected=getattr(self,'selected',None)
        if selected and selected[0] in ('point','line') and self.local_point_frame():
            self.say('局部导入测线未配准：不在地形上聚焦局部坐标；请先配准。');return
        return super().focus_selected()

    def delete_selected(self):
        selected=getattr(self,'selected',None)
        if selected and selected[0]=='point' and 0<=selected[1]<len(self.points):
            if self.points[selected[1]].get('local_frame'):
                self.say('局部导入源电极只读；请先配准，配准后再删除或编辑。');return
        return super().delete_selected()

    def commit_point(self,point,index=None):
        if not self.points_ready():return False
        if self.local_point_frame():
            raise ValueError('局部导入源电极只读；请先配准，配准后再编辑地理坐标。')
        self.validate_point(point,index)
        old=self.points[index] if index is not None else None
        if old==point:return True
        detach=self.points_follow_line and (old is None or any(old[k]!=point[k] for k in ('x','y','z')))
        if detach and Q.QMessageBox.question(self,'转为独立布点',
            '修改后整组测点将转为独立布点，不再随测线移动。是否继续？')!=Q.QMessageBox.Yes:return False
        before=edit_snapshot(self)
        if index is None:self.points.append(point);index=len(self.points)-1
        else:self.points[index]=point
        if old is not None:self.sync_observation_point_label(index,old['id'],point['id'])
        if detach:self.points_follow_line=False
        self.record_edit(before,'新增测点' if old is None else '编辑测点 '+point['id'])
        self.redraw();self.select_point(index)
        self.say('测点已更新；地图位置同步，Ctrl+Z 可撤销。原反演电极与模型保留。')
        return True

    def point_cell_changed(self,item):
        row,col=item.row(),item.column()
        if not 0<=row<len(self.points) or col>=4:return
        if self.points[row].get('local_frame'):
            self.say('局部导入源电极坐标为只读；请先配准，配准后再编辑地理坐标。')
            self.redraw();return
        try:
            if not self.points_ready():raise ValueError('请先结束计算或当前编辑。')
            point=copy.deepcopy(self.points[row]);field=('id','x','y','z')[col]
            point[field]=item.text().strip() if col==0 else float(item.text())
            if col==3:point['elevation_source']='手动高程'
            if col in (1,2) and point['elevation_source'].startswith('地形插值'):
                point['z']=float(self.terrain.height([[point['x'],point['y']]])[0])
            if not self.commit_point(point,row):self.redraw();self.select_point(row)
        except Exception as error:
            self.redraw();self.select_point(row);self.error(error)

    def edit_point_dialog(self,index=None):
        if self.local_point_frame():
            self.say('局部导入源电极只读；请先配准，配准后再编辑或新增测点。');return
        if not self.points_ready():self.say('请先加载地面，并结束计算或当前编辑。');return
        if index is not None and 0<=index<len(self.points) and self.points[index].get('local_frame'):
            self.say('局部导入源电极坐标为只读；请先配准，配准后再编辑地理坐标。');return
        if index is None:
            number=1;ids={p['id'] for p in self.points}
            while f'P{number:03}' in ids:number+=1
            xy=[float(self.terrain.x.mean()),float(self.terrain.y.mean())]
            point=dict(id=f'P{number:03}',x=xy[0],y=xy[1],z=0,elevation_source='地形插值')
        else:point=copy.deepcopy(self.points[index])
        dialog=Q.QDialog(self);dialog.setWindowTitle('新增测点' if index is None else '编辑测点')
        layout=Q.QFormLayout(dialog);fields={}
        for field,title in [('id','编号'),('x','X / m'),('y','Y / m'),('z','Z / m')]:
            fields[field]=Q.QLineEdit(str(point[field]));layout.addRow(title,fields[field])
        mode=Q.QComboBox();mode.addItems(['贴地高程','手动高程'])
        mode.setCurrentIndex(0 if point['elevation_source'].startswith('地形插值') else 1)
        fields['z'].setEnabled(mode.currentIndex()==1)
        mode.currentIndexChanged.connect(lambda i:fields['z'].setEnabled(i==1));layout.addRow('高程模式',mode)
        error_label=Q.QLabel();error_label.setWordWrap(True);layout.addRow(error_label)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel);layout.addRow(buttons)
        def save():
            try:
                edited=copy.deepcopy(point);edited['id']=fields['id'].text().strip()
                edited.update({k:float(fields[k].text()) for k in ('x','y')})
                edited['z']=float(fields['z'].text()) if mode.currentIndex() else float(self.terrain.height([[edited['x'],edited['y']]])[0])
                edited['elevation_source']='手动高程' if mode.currentIndex() else '地形插值'
                if self.commit_point(edited,index):dialog.accept()
            except Exception as error:error_label.setText(str(error))
        buttons.accepted.connect(save);buttons.rejected.connect(dialog.reject);dialog.exec()

    def edit_selected_point(self):
        if self.selected and self.selected[0]=='point':self.edit_point_dialog(self.selected[1])
        else:self.say('请先选中一个工作台测点。')
