"""Same-window editable survey lines, sequential sections and actual 3-D jobs."""
import copy
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import pyvista as pv
from PySide6 import QtCore,QtWidgets as Q
from core import write_json, load_result_arrays
from shortcuts import edit_snapshot
from region_project import workspace_document,capture_current
from survey_project import empty_survey,make_survey,validate_survey,parallel_calibrate,survey_signature,volume_inputs
from endpoint_edit import line_render_xyz,endpoint_overrides_conflict
from workflow_geometry import is_map_line_binding
from survey_interaction import (ensure_survey_visibility,survey_line_colors,survey_line_xy,
    electrode_xyz)
from line_import import is_local_import


class SurveyWorkflow:
    def setup_survey_ui(self):
        self._refreshing_survey=False
        self.survey_dock=Q.QDockWidget('同地表测线列表',self);self.survey_dock.setObjectName('survey-lines')
        body=Q.QWidget();box=Q.QVBoxLayout(body);box.setSpacing(10)
        self.survey_note=Q.QLabel();self.survey_note.setWordWrap(True);box.addWidget(self.survey_note)
        self.survey_current_label=Q.QLabel('当前测线：未选择');self.survey_current_label.setWordWrap(True)
        self.survey_current_label.setStyleSheet('font-weight:700; color:#ffe38a; font-size:13px;')
        box.addWidget(self.survey_current_label)
        self.survey_table=Q.QTableWidget(0,5);self.survey_table.setHorizontalHeaderLabels(['显示','参与','测线','观测','剖面'])
        self.survey_table.setSelectionBehavior(Q.QAbstractItemView.SelectRows);self.survey_table.setSelectionMode(Q.QAbstractItemView.SingleSelection)
        self.survey_table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeToContents)
        self.survey_table.horizontalHeader().setSectionResizeMode(2,Q.QHeaderView.Stretch)
        self.survey_table.setMinimumHeight(160);self.survey_table.verticalHeader().setDefaultSectionSize(32)
        self.survey_table.cellClicked.connect(self.survey_row_clicked);self.survey_table.itemChanged.connect(self.survey_item_changed)
        box.addWidget(self.survey_table,1)
        row=Q.QHBoxLayout();row.setSpacing(8)
        for text,callback in [('新增测线',self.add_survey_line),('移除测线',self.remove_survey_line)]:
            b=Q.QPushButton(text);b.setMinimumHeight(40);b.clicked.connect(callback);row.addWidget(b)
        box.addLayout(row)
        b=Q.QPushButton('新增测线并导入测点');b.setMinimumHeight(40);b.clicked.connect(self.add_survey_line_with_import);box.addWidget(b)
        b=Q.QPushButton('快速布置平行线');b.setMinimumHeight(40)
        b.setToolTip('以当前活动测线（或当前单线）为基准，按总条数、线距和左/右侧快速新增平行测线；只新增布局，不复制观测。')
        b.clicked.connect(self.open_quick_parallel_dialog);box.addWidget(b)
        row=Q.QHBoxLayout();row.setSpacing(8)
        for text,callback in [('定位当前',self.focus_current_survey_line),('查看全部',self.focus_all_survey_lines)]:
            b=Q.QPushButton(text);b.setMinimumHeight(36);b.clicked.connect(callback);row.addWidget(b)
        box.addLayout(row)
        b=Q.QPushButton('导入当前测线观测');b.setMinimumHeight(40);b.setToolTip('R2 数据从这里导入：先选 protocol.dat，再选 electrodes.dat；不用单独导入测点。');b.clicked.connect(self.import_observations);box.addWidget(b)
        self.parallel_fields=[];form=Q.QFormLayout()
        for name,minimum,maximum,value in [('首线起点 X',-1e9,1e9,220),('首线起点 Y',-1e9,1e9,100),('统一假设高程 Z',-1e5,1e5,0),('方向角 °',-360,360,0),('平行线距 m',.01,10000,2)]:
            field=Q.QDoubleSpinBox();field.setRange(minimum,maximum);field.setDecimals(3);field.setValue(value)
            form.addRow(name,field);self.parallel_fields.append(field)
        box.addLayout(form)
        b=Q.QPushButton('预览多测线平行校准');b.setMinimumHeight(40);b.clicked.connect(self.preview_parallel_calibration);box.addWidget(b)
        self.survey_gate_note=Q.QLabel();self.survey_gate_note.setWordWrap(True);box.addWidget(self.survey_gate_note)
        scroll=Q.QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(Q.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff);scroll.setWidget(body)
        self.survey_dock.setWidget(scroll);self.addDockWidget(QtCore.Qt.RightDockWidgetArea,self.survey_dock)
        self.survey_dock.setMinimumWidth(270);self.survey_dock.hide()
        self.survey_dock.visibilityChanged.connect(self._survey_dock_visibility_changed)
        data=self.step_pages[1];result=self.step_pages[2]
        self.button(data,'打开同地表测线列表',lambda:self.survey_dock.show())
        self.survey_sections_btn=self.button(result,'生成所选测线剖面',self.run_survey_sections)
        self.survey_volume_btn=self.button(result,'实际三维反演',self.run_survey_volume);self.survey_volume_btn.setObjectName('primary')
        self.survey_view=Q.QComboBox();self.survey_view.setMinimumHeight(40);self.survey_view.addItems(['查看多剖面','查看三维反演体'])
        self.survey_view.currentIndexChanged.connect(self.change_survey_view);result.addWidget(self.survey_view)
        self.survey_cancel=self.button(result,'停止当前计算并保留旧结果',self.cancel_survey_calculation)

    def survey_active(self):
        state=getattr(self,'survey_state',None)
        return isinstance(state,dict) and bool(state.get('lines'))

    def import_observations(self):
        if self.survey_busy():
            self.say('计算或测线批次进行中，暂不导入观测。');return
        return super().import_observations()

    def calibrate_observations(self):
        if self.survey_busy():
            self.say('计算或测线批次进行中，暂不校核布设。');return
        return super().calibrate_observations()

    def open_observation_packet(self,packet):
        if self.survey_busy():raise ValueError('计算或测线批次进行中，暂不导入观测。')
        if isinstance(packet,dict) and getattr(self,'survey_state',{}).get('hypothesis'):
            packet=copy.deepcopy(packet);packet['geometry_hypothesis']=True
        return super().open_observation_packet(packet)

    def survey_busy(self):
        return (getattr(self,'process',None) is not None or bool(getattr(self,'survey_queue',[]))
                or getattr(self,'_survey_batch_running',False) or getattr(self,'_loading_result',False))

    def sync_survey_line(self):
        state=getattr(self,'survey_state',None)
        if not state or getattr(self,'_activating_survey',False):return
        if not state['lines']:
            dock=getattr(self,'survey_dock',None)
            if getattr(self,'terrain',None) is not None and len(self.line)>=2 and dock is not None:
                is_visible=getattr(dock,'isVisible',None)
                if callable(is_visible) and is_visible():
                    self.ensure_current_workspace_line()
            return
        for line in state['lines']:
            if line['id']==state['active_id']:
                line['workspace']=workspace_document(self);return

    def _survey_dock_visibility_changed(self,visible):
        if visible and self.terrain is not None and len(self.line)>=2 and not self.survey_active():
            self.ensure_current_workspace_line()
            self.redraw()

    def active_survey_line(self):
        state=getattr(self,'survey_state',None)
        if not state:return None
        return next((line for line in state['lines'] if line['id']==state['active_id']),None)

    def _next_survey_line_id(self):
        used={line.get('id') for line in self.survey_state.get('lines') or []}
        number=1
        while f'L{number:02}' in used:number+=1
        return f'L{number:02}'

    def ensure_current_workspace_line(self):
        """Preserve a legacy single-line workspace as line L01 before adding another."""
        state=getattr(self,'survey_state',None)
        if not isinstance(state,dict) or state.get('lines'):return None
        if not (getattr(self,'points',None) or getattr(self,'line',None)
                or getattr(self,'ert_packet',None) is not None or getattr(self,'case_path',None)):
            return None
        sid='L01';workspace=workspace_document(self)
        state['lines']=[dict(id=sid,name=sid,selected=True,visible=True,workspace=workspace)]
        state['active_id']=sid
        return sid

    def survey_line_section_state(self,line):
        """Current / expired / not generated, without deleting archived files."""
        from workflow_geometry import observation_signature,validate_binding
        ws=line.get('workspace') or {};packet=ws.get('ert_packet')
        path=Path(ws.get('case') or '')
        if packet is None:return '已过期' if ws.get('case') else '待生成'
        if not (path/'metadata.json').is_file():return '已过期' if ws.get('case') else '待生成'
        try:
            meta=json.loads((path/'metadata.json').read_text(encoding='utf8'))
            if meta.get('observation_signature')!=observation_signature(packet):return '已过期'
            overrides=ws.get('line_endpoint_overrides') or {}
            if not is_map_line_binding(packet) and endpoint_overrides_conflict(ws['line'],overrides,ws['points']):return '已过期'
            if validate_binding(packet,packet.get('binding',{}),ws['points'],ws['line'],self.terrain):return '已过期'
            return '当前'
        except (ValueError,OSError,KeyError,TypeError,AttributeError):return '已过期'

    def survey_pick_at(self,position):
        """Return (line_id, kind, hit) for the topmost visible survey actor."""
        for actor,sid,kind in reversed(getattr(self,'survey_pick_entries',[])):
            hit=self._pick_actor(position,actor)
            if hit is not None:return sid,kind,hit
        return None

    def handle_survey_pick(self,sid,kind,hit=None):
        changed=sid!=self.survey_state.get('active_id')
        if changed:self.activate_survey_line(sid,clear_history=False)
        if kind=='electrode' and hit is not None:
            index=int(hit[0])
            if 0<=index<len(getattr(self,'points',[])):
                self.select_point(index);return 'point'
        if not changed:self.select_line()
        return 'activated' if changed else 'line'

    def current_survey_sections(self):
        """Return visible, current survey snapshots without changing storage."""
        if not self.survey_active():return []
        lines={line['id']:line for line in self.survey_state['lines']}
        current=[]
        for snapshot in getattr(self,'region_state',{}).get('sections',[]):
            sid=snapshot.get('survey_line_id')
            line=lines.get(sid)
            if line is None or not line.get('visible',True) or not self.survey_line_current(line):continue
            workspace=line.get('workspace') or {}
            packet=workspace.get('ert_packet')
            if is_local_import(packet):continue
            if is_map_line_binding(packet):
                points=workspace['points']
                ends=[[p['x'],p['y']] for p in (points[0],points[-1])]
                if not np.allclose(snapshot['endpoints'],ends,rtol=0,atol=.001):continue
            case=str(workspace.get('case') or '')
            provenance=snapshot.get('provenance',{}) or {}
            if case and str(provenance.get('case') or '')!=case:continue
            current.append(snapshot)
        return current

    def activate_survey_line(self,sid,sync=True,clear_history=True):
        if self.process is not None:raise ValueError('请先等待当前测线计算完成。')
        if not self.require_committed_edit():return False
        camera=None
        if getattr(self,'terrain',None) is not None and hasattr(self,'plotter'):
            try:camera=copy.deepcopy(self.plotter.camera_position)
            except Exception:camera=None
        if sync:self.sync_survey_line()
        line=next((l for l in self.survey_state['lines'] if l['id']==sid),None)
        if line is None:raise ValueError('测线不存在或已被移除。')
        ws=copy.deepcopy(line['workspace'])
        self._activating_survey=True
        try:
            self.survey_state['active_id']=sid;self.mode='browse';self.line_snapshot=None;self.drag_snapshot=None
            for key in ('model','case_arrays','case_path','section_data','result_line','result_observation_signature','result_binding','result_profile_packet','result_geometry_signature','_placement_key'):
                setattr(self,key,None)
            self.case_kind='synthetic';self.model_local_only=False;self.result_stale=False;self.fit_warning=''
            self.points=ws['points'];self.line=ws['line'];self.ert_packet=ws.get('ert_packet');self.points_follow_line=ws.get('points_follow_line',False)
            overrides=ws.get('line_endpoint_overrides')
            self.line_endpoint_overrides=copy.deepcopy(overrides if isinstance(overrides,dict) else {})
            self.simulation_enabled=ws.get('simulation_enabled',False)
            self.case_info.setText('此测线尚无剖面。')
            if ws.get('case'):
                try:self.load_case(ws['case'])
                except Exception as error:self.say(f"{line['name']} 的旧剖面无法读取：{error}")
            if ws.get('result_binding') and self.model is not None:
                self.result_binding=ws['result_binding'];self._placement_key=None
            if clear_history:self.history.clear()
            self.set_mode('browse');self.refresh_workflow();self.update_history_actions()
            return True
        finally:
            self._activating_survey=False
            if camera is not None and hasattr(self,'plotter'):
                try:
                    self.plotter.camera_position=camera;self.plotter.render()
                except Exception:pass

    def survey_row_clicked(self,row,column):
        if self._refreshing_survey or column<2:return
        if self.survey_busy():
            self.say('计算或排队进行中，暂不切换测线。')
            self.refresh_survey_ui();return
        if not 0<=row<len(self.survey_state['lines']):return
        try:self.activate_survey_line(self.survey_state['lines'][row]['id'],clear_history=False)
        except (ValueError,OSError,KeyError,RuntimeError) as error:self.error(error)

    def survey_item_changed(self,item):
        if self._refreshing_survey:return
        if self.survey_busy():
            self.say('计算或排队进行中，暂不修改测线列表。')
            self.refresh_survey_ui();return
        before=edit_snapshot(self);line=self.survey_state['lines'][item.row()]
        column=item.column()
        if column==0:line['visible']=item.checkState()==QtCore.Qt.Checked
        elif column==1:line['selected']=item.checkState()==QtCore.Qt.Checked
        elif column==2:line['name']=item.text().strip() or line['id']
        label={0:'修改测线显示状态',1:'修改测线参与状态',2:'修改测线名称'}.get(column,'修改测线列表')
        self.record_edit(before,label);self.redraw()

    def load_hypothetical_survey(self,paths=None):
        if self.survey_busy() or not self.require_committed_edit():return
        import app
        from observed_ert import read_observations
        if self.terrain is None:self.load_campus()
        if self.terrain is None:return
        if paths is None:
            replacement_confirmed=False
            if self.survey_state['lines'] or self.ert_packet is not None or self.points:
                answer=Q.QMessageBox.question(self,'建立独立假设演示',
                    '将替换当前电法工作区。原始文件和已计算目录保留。\n建议先保存当前完整项目；演示将使用新的保存路径。',
                    Q.QMessageBox.Save|Q.QMessageBox.Discard|Q.QMessageBox.Cancel,Q.QMessageBox.Cancel)
                if answer==Q.QMessageBox.Cancel:return
                replacement_confirmed=True
                if answer==Q.QMessageBox.Save:
                    saved=self.save_project()
                    if saved is False or self.dirty:return
            if not replacement_confirmed and not self.confirm_replace():return
            folder=app.DATA/'hypothetical-survey';paths=sorted(folder.glob('*.csv'))
            if len(paths)!=9:paths=sorted((app.ROOT/'电法实验数据'/'解压数据').glob('*/*.csv'))
        try:
            if len(paths)!=9:raise ValueError('未找到完整9组观测，请保留假设演示数据目录。')
            packets=[read_observations(p) for p in paths]
            origin=[float(np.mean(self.terrain.x)-23),float(np.mean(self.terrain.y)-8),0.]
            origin[2]=float(self.terrain.height([origin[:2]])[0])
            candidate=make_survey(packets,self.terrain,origin,0,2,True)
            self.region_state['sections']=[];self.region_state['generated_signature']=None
            self.survey_state=candidate;self.survey_queue=[];self._survey_batch_running=False
            self.volume_arrays=None;self.volume_metadata=None;self.project_path=None
            self.history.clear()
            self.activate_survey_line(candidate['active_id'],sync=False,clear_history=False)
            self.dirty=True;self.survey_dock.show();self.set_workflow_step(1)
            owner=getattr(self,'module_project',None)
            if owner is not None:owner.path=None;owner.mark_dirty()
            self.refresh_survey_ui();self.focus_survey_view()
            self.opacity.setValue(25)
            self.say('9组同线重复观测已按假设平行测网布置：每线46m、线距2m、总宽16m。默认同向起点对齐，可编辑。非现场三维采集。')
        except Exception as error:self.error(error)

    def _planned_start(self):
        cfg=self.survey_state['calibration']
        if not self.survey_state['lines'] and getattr(self,'parallel_fields',None):
            values=[field.value() for field in self.parallel_fields]
            cfg=dict(origin=[float(values[0]),float(values[1]),float(values[2])],
                     angle=float(values[3]),gap=float(values[4]))
        return cfg

    def add_survey_line(self):
        if self.terrain is None:
            self.say('请先加载地形再新增地图测线。也可直接导入测线数据做局部二维反演。');return
        if self.survey_busy() or not self.require_committed_edit():return
        if len(self.survey_state['lines'])>=30:
            self.say('最多支持30条测线；请先移除不需要的测线。');return
        cfg=self._planned_start()
        self.sync_survey_line();before=edit_snapshot(self)
        self.ensure_current_workspace_line()
        sid=self._next_survey_line_id()
        self.survey_state['calibration']=copy.deepcopy(cfg)
        ws=dict(schema=1,terrain=self.terrain.source,points=[],line=[],
            line_endpoint_overrides={},ert_packet=None,case=None,points_follow_line=False,
            simulation_enabled=False,result_binding=None,workflow_step=1,opacity=40)
        self.survey_state['lines'].append(dict(id=sid,name=sid,selected=True,visible=True,workspace=ws))
        self.activate_survey_line(sid,sync=False,clear_history=False);self.record_edit(before,'新增测线')
        self.set_workflow_step(1)
        self.survey_dock.show()
        self.say(f'已新增空测线 {sid}。请拖动绘线，或点击“生成测线”输入起点和终点。')

    def add_survey_line_with_import(self):
        """Preview station files first; only an accepted preview creates a line."""
        if self.terrain is None or self.survey_busy() or not self.require_committed_edit():return
        if hasattr(self,'points_ready') and not self.points_ready():return
        if len(self.survey_state['lines'])>=30:
            self.say('最多支持30条测线；请先移除不需要的测线。');return
        path,_=Q.QFileDialog.getOpenFileName(self,'新增测线并导入测点：Excel/Word/CSV；R2 DAT 请用“导入观测”','',
            '测点表 (*.xlsx *.docx *.csv);;旧版 Office（需另存新版） (*.xls *.doc)')
        if not path:return
        try:
            from point_import_dialog import PointImportDialog
            dialog=PointImportDialog(self,path,target_points=[],target_label='新建测线',new_line=True)
            if dialog.exec()==Q.QDialog.Accepted:self.apply_new_survey_import(dialog)
        except Exception as error:self.error(error)

    def apply_new_survey_import(self,dialog):
        """Non-interactive part of the new-line import, shared with tests."""
        incoming=copy.deepcopy(dialog.imported_points)
        if not incoming:raise ValueError('没有可导入的测点。')
        if len(self.survey_state['lines'])>=30:raise ValueError('最多支持30条测线；请先移除不需要的测线。')
        for point in incoming:self.validate_point(point,None,check_duplicate=False)
        line=[[float(p['x']),float(p['y'])] for p in incoming]
        if len(line)>=2 and any(np.linalg.norm(np.subtract(a,b))<1e-6 for a,b in zip(line,line[1:])):
            raise ValueError('新测线存在相邻重合测点，请修改文件。')
        self.sync_survey_line();before=edit_snapshot(self)
        cfg=self._planned_start();self.ensure_current_workspace_line();sid=self._next_survey_line_id()
        ws=dict(schema=1,terrain=self.terrain.source,points=incoming,line=line,line_endpoint_overrides={},ert_packet=None,
            case=None,points_follow_line=False,simulation_enabled=False,result_binding=None,workflow_step=1,opacity=40)
        self.survey_state['calibration']=copy.deepcopy(cfg)
        self.survey_state['lines'].append(dict(id=sid,name=sid,selected=True,visible=True,workspace=ws))
        self.activate_survey_line(sid,sync=False,clear_history=False)
        self.record_edit(before,'新增测线并导入测点')
        self.survey_dock.show();self.redraw()
        self.say(f'已新增 {sid} 并导入 {len(incoming)} 个测点；新线只使用导入点，未混入默认24点。Ctrl+Z 可撤销整个新增。')
        return sid

    def remove_survey_line(self):
        if self.survey_busy() or not self.require_committed_edit():return
        line=self.active_survey_line()
        if line is None:return
        if Q.QMessageBox.question(self,'移除测线',f"从本项目移除 {line['name']}？原始观测文件和计算目录保留。",Q.QMessageBox.Yes|Q.QMessageBox.No,Q.QMessageBox.No)!=Q.QMessageBox.Yes:return
        before=edit_snapshot(self);sid=line['id'];self.survey_state['lines']=[l for l in self.survey_state['lines'] if l['id']!=sid]
        self.region_state['sections']=[s for s in self.region_state['sections'] if s.get('survey_line_id')!=sid]
        if self.survey_state['lines']:self.activate_survey_line(self.survey_state['lines'][0]['id'],sync=False,clear_history=False)
        else:self._clear_survey_active_workspace()
        self.record_edit(before,'移除测线');self.redraw()

    def _clear_survey_active_workspace(self):
        self.survey_state['active_id']=None
        self.survey_state['volume_case']=None;self.survey_state['view']='sections'
        self.volume_arrays=None;self.volume_metadata=None
        for key in ('model','case_arrays','case_path','section_data','result_line','result_observation_signature','result_binding','result_profile_packet','result_geometry_signature','_placement_key'):
            setattr(self,key,None)
        self.points=[];self.line=[];self.ert_packet=None;self.points_follow_line=False
        self.line_endpoint_overrides={}
        self.simulation_enabled=False;self.case_kind='synthetic';self.model_local_only=False
        self.result_stale=False;self.fit_warning=''
        self.case_info.setText('尚无地下模型；请导入观测或运行合成演示。')

    def preview_parallel_calibration(self):
        if self.survey_busy() or not self.require_committed_edit():return
        if not self.survey_state['lines']:
            self.say('请先新增或建立测线。');return
        self.sync_survey_line()
        try:
            vals=[field.value() for field in self.parallel_fields]
            candidate=parallel_calibrate(self.survey_state,self.terrain,vals[:3],vals[3],vals[4])
            dialog=Q.QDialog(self);dialog.setWindowTitle('多测线平行校准预览');dialog.resize(800,480);box=Q.QVBoxLayout(dialog)
            note=Q.QLabel('统一方向与线距，保留每条线的观测电极数量及间距；旧结果不会被拉伸。\n'+('此项目使用假设几何，不代表真实采集位置。' if candidate['hypothesis'] else '请核对真实线距与起点。'))
            note.setWordWrap(True);box.addWidget(note)
            table=Q.QTableWidget(len(candidate['lines']),5);table.setHorizontalHeaderLabels(['测线','电极数','电极间距 m','起点 X/Y','总长 m'])
            table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers);table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
            for i,line in enumerate(candidate['lines']):
                ws=line['workspace'];packet=ws['ert_packet'];p=ws['points'][0]
                for j,val in enumerate([line['name'],packet['electrodes'],packet['spacing'],f"{p['x']:.2f} / {p['y']:.2f}",(packet['electrodes']-1)*packet['spacing']]):table.setItem(i,j,Q.QTableWidgetItem(str(val)))
            box.addWidget(table);buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);box.addWidget(buttons)
            if dialog.exec()==Q.QDialog.Accepted:self.apply_parallel_survey(candidate)
        except (ValueError,KeyError,TypeError) as error:self.error(error)

    def apply_parallel_survey(self,candidate):
        if self.survey_busy():raise ValueError('计算中不能校准测网。')
        before=edit_snapshot(self);self.survey_state=validate_survey(candidate,self.terrain)
        self.activate_survey_line(candidate['active_id'],sync=False,clear_history=False)
        self.record_edit(before,'多测线平行校准');self.redraw()

    def refresh_survey_ui(self):
        if not hasattr(self,'survey_table') or getattr(self,'_refreshing_survey',False):return
        self._refreshing_survey=True
        try:
            ensure_survey_visibility(self.survey_state)
            self.sync_survey_line()
            lines=self.survey_state['lines']
            active=self.active_survey_line()
            self.survey_current_label.setText(f"当前测线：{active['name']}" if active is not None else '当前测线：未选择')
            note=('假设测网演示｜原始9组是同一条线的重复观测。' if self.survey_state['hypothesis']
                  else '所有测线共用当前地形；“显示”只控制绘图，“参与”决定反演；点击行切换编辑。')
            expired=[line for line in lines if self.survey_line_section_state(line)=='已过期']
            if expired:note+=f"｜{len(expired)} 条旧剖面已过期，仅保留文件且不会作为当前结果显示。"
            local_lines=[line for line in lines if is_local_import((line.get('workspace') or {}).get('ert_packet'))]
            if local_lines:
                note+=f"｜{len(local_lines)} 条局部导入线未配准，只显示二维采集预览，不叠加到地形；配准后才可进入三维。"
            self.survey_note.setText(note)
            editing=self.survey_table.state()==Q.QAbstractItemView.EditingState
            if not editing:
                self.survey_table.setRowCount(len(lines))
                for i,line in enumerate(lines):
                    ws=line['workspace'];packet=ws.get('ert_packet');status=self.survey_line_section_state(line)
                    if is_local_import(packet):status=f'局部未配准 · {status}'
                    observation_status = str(len(packet['rows'])) if packet else (
                        '待导入' if len(ws['line'])>=2 else '待绘线')
                    values=['','',line['name'],observation_status,status]
                    for j,text in enumerate(values):
                        item=Q.QTableWidgetItem(text)
                        if j in (0,1):
                            item.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable|QtCore.Qt.ItemIsUserCheckable)
                            checked=line.get('visible',True) if j==0 else line['selected']
                            item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
                        elif j>=3:item.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable)
                        self.survey_table.setItem(i,j,item)
                    if line['id']==self.survey_state['active_id']:self.survey_table.selectRow(i)
            cfg=self.survey_state['calibration']
            if not any(field.hasFocus() for field in self.parallel_fields):
                for field,val in zip(self.parallel_fields,[*cfg['origin'],cfg['angle'],cfg['gap']]):field.setValue(val)
            gated=True;message='所选测线的剖面及原始观测检查通过，可以运行三维求解。'
            try:volume_inputs(self.survey_state,self.terrain)
            except (ValueError,KeyError,OSError,TypeError) as error:gated=False;message=str(error)
            if gated and self.volume_metadata is not None and not self.survey_volume_current():
                message='已有三维结果与当前输入签名不一致，旧结果已过期且不会显示；请重新运行实际三维反演。'
            elif self.volume_metadata is not None and not self.survey_volume_current():
                message='三维旧结果已过期，不会显示；'+message
            busy=self.survey_busy()
            self.survey_gate_note.setText(message);self.survey_volume_btn.setToolTip(message)
            self.survey_volume_btn.setEnabled(gated and not busy)
            self.survey_sections_btn.setEnabled(bool(lines) and not busy)
            self.survey_cancel.setEnabled(busy)
            self.survey_view.setEnabled(not busy)
            self.survey_table.setEnabled(not busy)
            for widget in (self.survey_sections_btn,self.survey_volume_btn,self.survey_view,self.survey_cancel):widget.setVisible(bool(lines))
            self.survey_view.blockSignals(True);self.survey_view.setCurrentIndex(1 if self.survey_state['view']=='volume' else 0);self.survey_view.blockSignals(False)
            self.survey_volume_btn.setText('实际三维反演（假设测网）' if self.survey_state['hypothesis'] else '实际三维反演')
        finally:self._refreshing_survey=False

    def survey_line_current(self,line):
        return self.survey_line_section_state(line)=='当前'

    def run_survey_sections(self):
        if self.survey_busy() or not self.require_committed_edit():return
        self.sync_survey_line();self.survey_queue=[l['id'] for l in self.survey_state['lines'] if l['selected']]
        if not self.survey_queue:
            self.say('请至少勾选一条测线。');return
        self._next_survey_section()

    def _next_survey_section(self):
        if not self.survey_queue:
            self._survey_batch_running=False;self.refresh_survey_ui();return
        sid=self.survey_queue.pop(0);self._survey_batch_running=True
        try:
            if not self.activate_survey_line(sid,sync=True,clear_history=False):
                raise ValueError('当前编辑未完成，剖面批次已停止。')
            self.set_workflow_step(2);self.run_observed_inversion()
            if self.process is None:
                self.survey_queue=[];self._survey_batch_running=False
                self.say('剖面未启动，已停止排队并保留已有结果。')
                self.refresh_workflow();self.refresh_survey_ui()
        except Exception as error:
            self.survey_queue=[];self._survey_batch_running=False
            self.error(error);self.refresh_survey_ui()

    def survey_section_finished(self,success):
        self._survey_batch_running=False
        if not self.survey_state['lines']:
            self.survey_queue=[];self.refresh_survey_ui();return
        self.sync_survey_line()
        if success and self.active_survey_line() is not None:
            try:
                line=self.active_survey_line();snapshot=capture_current(self,line['name']);snapshot['survey_line_id']=line['id'];snapshot['geometry_hypothesis']=self.survey_state['hypothesis']
                self.region_state['sections']=[s for s in self.region_state['sections'] if s.get('survey_line_id')!=line['id']]+[snapshot]
            except Exception as error:self.say('当前剖面未能归档：'+str(error));success=False
        if not success:self.survey_queue=[]
        self.redraw();self.refresh_survey_ui()
        if self.survey_queue and success:QtCore.QTimer.singleShot(100,self._next_survey_section)

    def run_survey_volume(self):
        if self.survey_busy() or not self.require_committed_edit():return
        import app,sys
        self.sync_survey_line()
        try:
            request=volume_inputs(self.survey_state,self.terrain);out=app.ROOT/'runs'/('survey3d-'+uuid4().hex[:12])
            request.update(mode='survey3d',output=str(out),depth=12.,cell_volume=120.,max_iter=4,lam=30.)
            write_json(out/'request.json',request);self.current_request=out
            self.process=QtCore.QProcess(self);self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.worker_log);self.process.finished.connect(self.survey_volume_done);self.process.errorOccurred.connect(self.worker_error)
            args=['--worker',str(out/'request.json')] if getattr(sys,'frozen',False) else [str(app.ROOT/'app.py'),'--worker',str(out/'request.json')]
            self.process.start(sys.executable,args);self.refresh_workflow();self.refresh_survey_ui()
            self.say('正在独立进程运行真正3D ERT；假设测网不作为现场结论，窗口可继续旋转浏览。')
        except Exception as error:self.error(error)

    def survey_volume_done(self,code,status):
        if self.process is None:return
        process=self.process
        from project_tasks import project_task_active
        if project_task_active():
            def retry():
                if self.process is process:self.survey_volume_done(code,status)
            QtCore.QTimer.singleShot(50,self,retry)
            return
        self._loading_result=True
        try:
            return self._finish_survey_volume(code,status)
        finally:
            if self.process is process:
                self.process=None;process.deleteLater()
            self._loading_result=False
            self.refresh_workflow();self.refresh_survey_ui()

    def _finish_survey_volume(self,code,status):
        if self.process is None:return
        try:self.worker_log()
        except Exception as error:self.say('读取三维进程日志失败：'+str(error))
        old_case=self.survey_state.get('volume_case');old_arrays=self.volume_arrays;old_meta=self.volume_metadata
        success=False
        if code==0:
            case=str(self.current_request)
            try:
                self.load_survey_volume(case)
                self.survey_state['volume_case']=case;self.survey_state['view']='volume'
                self.dirty=True;success=True
            except Exception as error:
                self.survey_state['volume_case']=old_case;self.volume_arrays=old_arrays;self.volume_metadata=old_meta
                self.say('三维结果文件缺失或无效，旧结果已保留：'+str(error))
        else:
            self.say('三维计算未完成，原结果已保留。详见 '+str(Path(self.current_request)/'worker.log'))
        self.process.deleteLater();self.process=None
        if success:
            self.redraw();self.focus_survey_view();self.say('三维求解完成。可切换多剖面/三维体；请检查实际残差及假设说明。')
        self.refresh_workflow();self.refresh_survey_ui()

    def cancel_survey_calculation(self):
        self.survey_queue=[]
        if self.process is not None:
            self.say('正在停止当前计算并保留旧结果。');self.process.kill()
        else:
            self._survey_batch_running=False
            self.say('已取消排队中的测线剖面计算。')
            self.refresh_workflow();self.refresh_survey_ui()

    def load_survey_volume(self,case=None):
        case=case or self.survey_state.get('volume_case')
        if not case:raise ValueError('没有可加载的三维结果目录。')
        path=Path(case)
        metadata=json.loads((path/'metadata.json').read_text(encoding='utf8'))
        if not isinstance(metadata,dict) or not metadata.get('input_signature'):raise ValueError('三维结果缺少输入签名。')
        arrays=load_result_arrays(path/'volume.npz')
        for key in ('nodes','cells','resistivity','sensors'):
            if key not in arrays:raise ValueError('三维结果缺少 '+key+'。')
        nodes=np.asarray(arrays['nodes']);cells=np.asarray(arrays['cells']);rho=np.asarray(arrays['resistivity']);sensors=np.asarray(arrays['sensors'])
        if nodes.ndim!=2 or nodes.shape[1]!=3 or cells.ndim!=2 or cells.shape[1]!=6 or len(cells)!=len(rho):raise ValueError('三维网格或电阻率数组形状无效。')
        if sensors.ndim!=2 or sensors.shape[1]!=3 or not np.isfinite(nodes).all() or not np.isfinite(sensors).all() or not np.isfinite(rho).all():raise ValueError('三维结果包含无效数值。')
        self.volume_metadata=metadata;self.volume_arrays=arrays
        return True

    def survey_volume_current(self):
        metadata=getattr(self,'volume_metadata',None)
        if not metadata or not self.survey_active():return False
        try:return metadata.get('input_signature')==survey_signature(self.survey_state)
        except (ValueError,KeyError,TypeError):return False

    def change_survey_view(self,index):
        if self._refreshing_survey:return
        if self.survey_busy():
            self.say('计算或排队进行中，暂不切换显示。');self.refresh_survey_ui();return
        if index==1 and not self.survey_volume_current():
            self.say('暂无与当前测网一致的三维结果，请先生成所选剖面并运行三维反演。');self.refresh_survey_ui();return
        self.survey_state['view']='volume' if index else 'sections';self.dirty=True;self.redraw()

    def draw_survey_layers(self):
        self.survey_line_actors=[];self.survey_electrode_actors=[];self.survey_pick_entries=[]
        self.survey_volume_actor=None;self.survey_volume_mesh=None
        if not self.survey_active():return
        ensure_survey_visibility(self.survey_state)
        self.sync_survey_line()
        colors=survey_line_colors(self.survey_state)
        for line in self.survey_state['lines']:
            if not line.get('visible',True):continue
            sid=line['id'];active=sid==self.survey_state.get('active_id');color=colors.get(sid,'#51bce1')
            ws=line['workspace']
            # Local unregistered acquisitions use local station coordinates and
            # must never be draped, picked, labelled or hovered on the DEM.
            if is_local_import(ws.get('ert_packet')):
                continue
            xy=survey_line_xy(ws)
            if len(xy)>=2:
                xyz=line_render_xyz(self.terrain,ws.get('line') or [],ws.get('line_endpoint_overrides') or {},
                    max(150,len(xy)))
                if xyz is not None:
                    mesh=pv.lines_from_points(np.column_stack([xyz[:,0],xyz[:,1],xyz[:,2]+.6])-self.terrain.origin)
                    actor=self.plotter.add_mesh(mesh,color=color,line_width=7 if active else 4,
                        pickable=True,reset_camera=False,name=f'survey-line-{sid}')
                    self.survey_line_actors.append((actor,sid))
                    self.survey_pick_entries.append((actor,sid,'line'))
            points=ws.get('points') or [];xyz=electrode_xyz(points)
            if not len(xyz):continue
            valid=np.flatnonzero(np.isfinite(xyz).all(axis=1))
            if not len(valid):continue
            shown=xyz[valid]-self.terrain.origin
            actor=self.plotter.add_points(shown,color=color,point_size=14 if active else 9,
                render_points_as_spheres=True,pickable=True,reset_camera=False,name=f'survey-electrodes-{sid}')
            self.survey_electrode_actors.append((actor,sid))
            self.survey_pick_entries.append((actor,sid,'electrode'))
            # Dense electrode IDs are shown on hover/selection only.
        show_volume=self.survey_state['view']=='volume' and self.survey_volume_current()
        if show_volume:
            try:
                data=self.volume_arrays;cells=np.asarray(data['cells']);nodes=np.asarray(data['nodes'])-self.terrain.origin
                mesh=pv.UnstructuredGrid(np.column_stack([np.full(len(cells),cells.shape[1]),cells]).ravel(),np.full(len(cells),13,dtype=np.uint8),nodes)
                mesh.cell_data['resistivity']=data['resistivity']
                if len(data.get('coverage_log10',[]))==len(cells):mesh.cell_data['coverage_log10']=data['coverage_log10']
                sensors=np.asarray(data['sensors'],float)
                first=next((l for l in self.survey_state['lines'] if l['selected'] and len(survey_line_xy(l['workspace']))>=2),None)
                if first is not None:
                    vertices=survey_line_xy(first['workspace'])
                    edge=vertices[-1]-vertices[0]
                else:edge=sensors[-1,:2]-sensors[0,:2]
                d=np.asarray(edge,float)[:2];length=float(np.linalg.norm(d))
                if length<=0:raise ValueError('无法从所选首线确定三维测线方向。')
                d/=length;n=np.array([-d[1],d[0]]);a=sensors[0,:2]
                centers=mesh.cell_centers().points+self.terrain.origin;s=(sensors[:,:2]-a)@d;t=(sensors[:,:2]-a)@n
                cs=(centers[:,:2]-a)@d;ct=(centers[:,:2]-a)@n
                mask=(cs>=s.min())&(cs<=s.max())&(ct>=t.min())&(ct<=t.max())
                if not mask.any():raise ValueError('三维结果不覆盖测线范围。')
                mesh=mesh.extract_cells(np.flatnonzero(mask));self.survey_volume_mesh=mesh
                self.survey_volume_actor=self.plotter.add_mesh(mesh.extract_surface(),scalars='resistivity',cmap='turbo_r',log_scale=True,opacity=.78,pickable=True,
                    scalar_bar_args=dict(title='',vertical=False,position_x=.14,position_y=.05,height=.08,width=.72,color='#dce8f7',label_font_size=9,fmt='%.3g'))
                if '' in self.plotter.scalar_bars:self.scalar_legend.attach(self.plotter.scalar_bars[''])
            except Exception as error:
                show_volume=False;self.survey_volume_actor=None;self.survey_volume_mesh=None
                self.say('三维结果无法显示：'+str(error))
        hypothesis=self.survey_state['hypothesis']
        if show_volume and hypothesis:note='假设测网下的实际三维求解，非现场结论'
        elif show_volume:note='实际三维反演体｜平行测线约束'
        elif hypothesis:note='假设测网｜原观测为同线重复测量｜多测线剖面'
        else:note='多测线测网｜多测线剖面'
        if self.volume_metadata:
            try:note+=f"｜3D χ² {float(self.volume_metadata['chi2']):.2f} / RRMS {float(self.volume_metadata['rrms_percent']):.2f}%"
            except (KeyError,TypeError,ValueError):pass
            if not self.survey_volume_current():note+='｜三维旧结果已过期'
        local_count=sum(1 for line in self.survey_state['lines']
            if is_local_import((line.get('workspace') or {}).get('ert_packet')))
        if local_count:note+=f'｜{local_count} 条局部导入线未配准，未叠加到DEM'
        self.region_status.setText(note);self.scene_badge.setText('假设测网下的实际三维求解，非现场结论' if hypothesis and show_volume else ('假设平行测网 / 非现场三维结论' if hypothesis else '同地表多测线'))
        self.result_status.setWordWrap(True);self.result_status.setText(note)
        if show_volume:self.result_status.setStyleSheet('')

    def _focus_survey_points(self,points):
        if not points:return
        xyz=np.array([[p['x'],p['y'],p['z']] for p in points])-self.terrain.origin
        low=xyz.min(axis=0)-[3,3,14];high=xyz.max(axis=0)+3
        self.plotter.view_isometric();self.plotter.reset_camera(bounds=tuple(np.column_stack([low,high]).ravel()));self.plotter.render()

    def focus_current_survey_line(self):
        line=self.active_survey_line()
        if line is None:
            self.say('当前没有可定位的测线测点。');return
        if self.terrain is None or is_local_import((line.get('workspace') or {}).get('ert_packet')):
            self.say('局部未配准测线没有地形位置，不能在地形上定位；请先配准。');return
        ws=line['workspace'];points=list(ws.get('points') or [])
        xyz=line_render_xyz(self.terrain,ws.get('line') or [],ws.get('line_endpoint_overrides') or {},2)
        if xyz is not None:points.extend(dict(x=float(p[0]),y=float(p[1]),z=float(p[2])) for p in xyz)
        if points:self._focus_survey_points(points)

    def focus_all_survey_lines(self):
        if self.terrain is None:
            self.say('局部未配准测线没有地形位置，不能在地形上定位；请先配准。');return
        lines=[line for line in self.survey_state.get('lines') or []
               if line.get('visible',True) and not is_local_import((line.get('workspace') or {}).get('ert_packet'))]
        if not lines:
            self.say('没有可在地形上定位的已配准测线。');return
        points=[p for line in lines for p in line['workspace'].get('points') or []]
        for line in lines:
            ws=line['workspace'];xyz=line_render_xyz(self.terrain,ws.get('line') or [],ws.get('line_endpoint_overrides') or {},2)
            if xyz is not None:points.extend(dict(x=float(p[0]),y=float(p[1]),z=float(p[2])) for p in xyz)
        if not points:points=[p for line in self.survey_state.get('lines') or [] for p in line['workspace'].get('points') or []]
        if not points:return
        self._focus_survey_points(points)

    def focus_survey_view(self):
        self.focus_all_survey_lines()
