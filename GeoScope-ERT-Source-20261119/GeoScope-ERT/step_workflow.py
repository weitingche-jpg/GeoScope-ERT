"""Three-step project workflow, validation, station association and result pose."""
import copy
import json
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Q
from core import place_section, load_result_arrays
from shortcuts import edit_snapshot
from observed_ert import validate_packet
from workflow_geometry import electrode_ids,observation_signature,physical_signature,validate_binding,generate_bound_points
from ert_quality import active_indices,quality_report
from ert_arrays import array_label
from line_import import is_local_import


class StepWorkflow:
    def setup_sidebar_workflow(self,layout):
        self.step_stack=Q.QStackedWidget();layout.addWidget(self.step_stack)
        self.step_pages=[]
        for _ in range(3):
            page=Q.QWidget();box=Q.QVBoxLayout(page);box.setContentsMargins(0,0,0,0);box.setSpacing(12)
            self.step_pages.append(box);self.step_stack.addWidget(page)
        first,data,result=self.step_pages
        self.heading(first,'1  导入地形')
        self.button(first,'导入地形 GeoTIFF / DEM',self.import_terrain)
        self.button(first,'加载虚拟校园示意',self.load_campus)
        self.button(first,'加载完整合成演示',self.demo)
        self.info=Q.QLabel('请先加载地形或校园场景。',objectName='hint');self.info.setWordWrap(True);first.addWidget(self.info)
        self.button(first,'下一步：测点与数据',lambda:self.set_workflow_step(1))
        self.heading(data,'2  测点与观测')
        self.import_line_btn=self.button(data,'导入测线数据（含测点与观测）',self.import_line_data)
        self.import_line_btn.setObjectName('primary')
        self.import_line_btn.setToolTip('已有地图测线时按数据间距自动布点，未绘线时导入为局部二维测线。'
            '支持 R2 配对文件、原始仪器 CSV/DAT 与通用表格。')
        self.point_btn=self.button(data,'地图布点 · 点击地面 [P]',lambda:self.set_mode('browse' if self.mode=='point' else 'point'),True)
        self.line_btn=self.button(data,'拖动绘线 · 起点拖至终点 [L]',lambda:self.set_mode('browse' if self.mode=='line' else 'line'),True)
        self.edit_btn=self.button(data,'拖动测线端点 [E]',lambda:self.set_mode('browse' if self.mode=='edit' else 'edit'),True)
        self.endpoint_btn=self.button(data,'编辑终点坐标',lambda _checked=False:self.edit_endpoint_coordinates(1))
        self.endpoint_btn.setObjectName('endpoint-edit-button')
        self.button(data,'生成测线',self.generate_line_from_coordinates)
        self.calibration_notice=Q.QLabel(objectName='hint');self.calibration_notice.setWordWrap(True);data.addWidget(self.calibration_notice)
        self.button(data,'一键校核与配准',self.calibrate_observations)
        self.button(data,'切换为仅局部反演',self.use_local_observations)
        self.workflow_summary=Q.QLabel('在下方页签完善测点和观测。',objectName='hint');self.workflow_summary.setWordWrap(True);data.addWidget(self.workflow_summary)
        self.button(data,'检查数据',self.show_checks)
        self.button(data,'下一步：反演与结果',lambda:self.set_workflow_step(2))
        self.heading(result,'3  反演与结果')
        self.run_summary=Q.QLabel(objectName='hint');self.run_summary.setWordWrap(True);result.addWidget(self.run_summary)
        self.simulation_check=Q.QCheckBox('明确使用合成演示观测');self.simulation_check.toggled.connect(self.set_simulation_enabled);result.addWidget(self.simulation_check)
        advanced=Q.QWidget();form=Q.QFormLayout(advanced)
        advanced_toggle=Q.QPushButton('高级参数（导入观测） ▸');advanced_toggle.setCheckable(True);advanced_toggle.toggled.connect(advanced.setVisible)
        advanced_toggle.setFixedHeight(42)
        advanced_toggle.toggled.connect(lambda _:QtCore.QTimer.singleShot(0,self.refresh_sidebar_geometry))
        result.addWidget(advanced_toggle);advanced.setVisible(False)
        self.workflow_error=Q.QDoubleSpinBox();self.workflow_error.setRange(.1,30);self.workflow_error.setSuffix(' %')
        self.workflow_lam=Q.QDoubleSpinBox();self.workflow_lam.setRange(.1,1000)
        self.workflow_iterations=Q.QSpinBox();self.workflow_iterations.setRange(1,30)
        for title,field in [('假设相对误差',self.workflow_error),('平滑正则化 λ',self.workflow_lam),('最大迭代',self.workflow_iterations)]:
            form.addRow(title,field);field.editingFinished.connect(self.commit_workflow_parameters)
        result.addWidget(advanced)
        self.run_btn=self.button(result,'开始反演',self.run_inversion);self.run_btn.setObjectName('primary')
        self.case_info=Q.QLabel('尚无反演结果。',objectName='hint');self.case_info.setWordWrap(True)
        self.case_info.setStyleSheet('QLabel {background:#151b24; border:1px solid #2b3746; border-radius:6px; padding:10px; color:#b6c5d8;}')
        result.addWidget(self.case_info)
        self.button(result,'定位地下剖面',self.focus_result_view)
        self.button(result,'加载已有案例',self.open_case)
        self.button(result,'返回完善数据',lambda:self.set_workflow_step(1))

    def setup_step_bar(self,center):
        bar=Q.QHBoxLayout();self.step_buttons=[]
        for index,title in enumerate(['1  导入地形','2  测点与数据','3  反演与结果']):
            button=Q.QPushButton(title);button.setCheckable(True)
            button.clicked.connect(lambda checked=False,i=index:self.set_workflow_step(i))
            bar.addWidget(button);self.step_buttons.append(button)
        center.addLayout(bar)

    def setup_data_tabs(self,center,point_tools):
        self.data_tabs=Q.QTabWidget();self.data_tabs.setObjectName('workbenchDataTabs')
        self.data_tabs.setSizePolicy(Q.QSizePolicy.Expanding,Q.QSizePolicy.Fixed)
        tab_bar=self.data_tabs.tabBar();tab_bar.setExpanding(False)
        tab_bar.setElideMode(QtCore.Qt.ElideNone);tab_bar.setUsesScrollButtons(True)
        self.data_tabs.setStyleSheet(
            'QTabWidget#workbenchDataTabs::pane {border:1px solid #2d3949; border-radius:6px;}'
            'QTabBar::tab {background:#171e28; color:#b7c6d8; border:1px solid #303d4e;'
            'border-radius:6px; min-width:76px; padding:9px 12px; margin-right:8px; margin-bottom:6px;}'
            'QTabBar::tab:selected {background:#1d3855; color:#e2f1ff; border-color:#5a9fe8;}'
            'QTabBar::tab:hover {background:#243348;}'
        )
        def create_page(text,toolbar):
            page=Q.QWidget();box=Q.QVBoxLayout(page)
            box.setContentsMargins(10,8,10,10);box.setSpacing(8)
            hint=Q.QLabel(text,objectName='hint');hint.setWordWrap(True);hint.setFixedHeight(36)
            box.addWidget(hint)
            band=Q.QWidget();band.setFixedHeight(40);toolbar.setContentsMargins(0,0,0,0)
            toolbar.setSpacing(8);band.setLayout(toolbar);box.addWidget(band)
            return page,box,hint
        point_page,box,self.point_binding_note=create_page('',point_tools)
        self.table.setMaximumHeight(16777215);self.table.setMinimumHeight(56)
        box.addWidget(self.table,1);self.data_tabs.addTab(point_page,'测点 / 电极')
        tools=Q.QHBoxLayout()
        for text,callback in [('导入观测',self.import_observations),('新增观测',self.add_observation),('编辑观测',self.edit_observation),('删除观测',self.delete_observation)]:
            b=Q.QPushButton(text);b.clicked.connect(callback);tools.addWidget(b)
        tools.addStretch()
        obs_page,box,_=create_page('每条观测对应 A/B/M/N 四个电极与视电阻率；可导入或手动编辑。',tools)
        self.observation_table=Q.QTableWidget(0,7);self.observation_table.setHorizontalHeaderLabels(['序号','A 供电','B 供电','M 测量','N 测量','视电阻率 Ω·m','参与反演'])
        self.observation_table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers);self.observation_table.setSelectionBehavior(Q.QAbstractItemView.SelectRows)
        self.observation_table.setSelectionMode(Q.QAbstractItemView.SingleSelection)
        self.observation_table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch);box.addWidget(self.observation_table,1)
        self.data_tabs.addTab(obs_page,'观测数据')
        self.check_table=Q.QTableWidget(0,2);self.check_table.setHorizontalHeaderLabels(['状态','问题 / 点击定位'])
        self.check_table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers);self.check_table.horizontalHeader().setSectionResizeMode(1,Q.QHeaderView.Stretch)
        self.check_table.cellClicked.connect(self.jump_workflow_issue)
        check_tools=Q.QHBoxLayout();check_tools.addWidget(Q.QLabel('检查结果：错误 / 提示 / 通过',objectName='hint'));check_tools.addStretch()
        self.quality_button=Q.QPushButton('观测质检');self.quality_button.clicked.connect(self.open_quality_dialog);check_tools.addWidget(self.quality_button)
        check_page,box,_=create_page('点击问题行可定位到对应测点或观测；先处理错误再运行反演。',check_tools)
        box.addWidget(self.check_table,1);self.data_tabs.addTab(check_page,'数据检查')
        self.response_table=Q.QTableWidget(0,4);self.response_table.setHorizontalHeaderLabels(['原观测序号','观测 Ω·m','计算 Ω·m','相对残差 %'])
        self.response_table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers);self.response_table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        response_tools=Q.QHBoxLayout();response_tools.addWidget(Q.QLabel('观测与计算响应对比',objectName='hint'));response_tools.addStretch()
        self.diagnostics_button=Q.QPushButton('反演诊断');self.diagnostics_button.clicked.connect(self.open_diagnostics_dialog);response_tools.addWidget(self.diagnostics_button)
        response_page,box,_=create_page('反演完成后显示观测值、计算值与相对残差。',response_tools)
        box.addWidget(self.response_table,1);self.data_tabs.addTab(response_page,'计算响应')
        for table in (self.observation_table,self.check_table,self.response_table):table.setMinimumHeight(56)
        center.addWidget(self.data_tabs);self.resize_data_panel()

    def resize_data_panel(self):
        if not hasattr(self,'data_tabs'):return
        # Reserve one height for all pages. Only an actual window resize changes it.
        height=max(232,min(290,round(self.height()*.30)))
        if self.data_tabs.minimumHeight()!=height or self.data_tabs.maximumHeight()!=height:
            self.data_tabs.setFixedHeight(height)

    def set_workflow_step(self,index):
        if not hasattr(self,'data_tabs'):return
        if not self.require_committed_edit():return
        local_acquisition=is_local_import(getattr(self,'ert_packet',None))
        if index>1 and self.terrain is None and not local_acquisition:
            self.say('请先导入地形，或导入一条局部测线后再进入反演。');index=1
        if index!=1 and self.mode!='browse':self.set_mode('browse')
        self.workflow_step=index;self.step_stack.setCurrentIndex(index)
        self.data_tabs.setVisible(index>0 and not (getattr(self,'region_state',{}).get('sections') and self.model is None and not self.ert_packet and not self.points))
        if index==1:self.data_tabs.setCurrentIndex(0)
        if index==2:self.data_tabs.setCurrentIndex(2 if any(p['level']=='错误' for p in self.workflow_checks()) else 3)
        self.refresh_workflow()

    def workflow_checks(self):
        issues=[]
        def add(level,text,tab=2,row=None):issues.append(dict(level=level,text=text,tab=tab,row=row))
        local_acquisition=is_local_import(getattr(self,'ert_packet',None))
        if self.terrain is None and not local_acquisition:
            add('错误','请先导入地形，或使用“导入测线数据（含测点与观测）”建立局部未配准测线。');return issues
        if self.terrain is None:
            add('提示','未加载地表：当前仅为局部测线采集预览，坐标是局部距离/平地参考值，未配准，不叠加地形。')
        seen=set()
        for index,p in enumerate(self.points):
            try:
                if not p['id'] or p['id'] in seen:raise ValueError('编号为空或重复')
                seen.add(p['id'])
                if not all(np.isfinite(float(p[k])) for k in ('x','y','z')):raise ValueError('坐标无效')
                if self.terrain is not None:
                    if not np.isfinite(self.terrain.height([[p['x'],p['y']]])[0]):raise ValueError('超出地形范围')
                elif not p.get('local_frame'):
                    raise ValueError('无地形时只允许局部导入的只读电极')
            except (ValueError,KeyError,TypeError) as e:add('错误',f'测点第{index+1}行：{e}',0,index)
        if self.ert_packet:
            try:
                packet=validate_packet(self.ert_packet)
                if not .001<=float(packet['relative_error'])<=.3 or not .1<=float(packet['lam'])<=1000 or not 1<=int(packet['max_iter'])<=30:
                    raise ValueError('反演参数超出范围')
                binding=packet.get('binding')
                if binding:
                    for text in validate_binding(packet,binding,self.points,self.line,self.terrain):add('错误',text,0)
                    if is_local_import(packet):
                        add('提示','当前为局部未配准导入测线：电极坐标为只读局部距离/平地参考值，不叠加到地形；配准后才能进入三维。',1)
                    elif binding.get('mode')=='local':add('提示','仅局部反演：地图上的测点尚未与本组观测关联，结果仅在二维显示。',1)
                    elif binding.get('mode')=='registered':
                        ids=electrode_ids(packet);available={p['id'] for p in self.points}
                        for r,row in enumerate(packet['rows']):
                            missing=[ids[i] for i in row['abmn'] if ids[i] not in available]
                            if missing:add('错误',f"观测第{r+1}条引用未匹配电极：{', '.join(missing)}",1,r)
                elif not self.observation_geometry_matches():add('错误','原采集几何已改变，请恢复或配准。',0)
                if not packet.get('confirmed',True):add('错误','尚未接受高程与误差假设。',1)
                add('提示',f"平地高程与 {packet['relative_error']*100:g}% 相对误差为计算假设，非实测精度。",1)
                active_count=len(active_indices(packet))
                if active_count<packet['electrodes']:add('提示','启用观测较少，可能不足以约束完整二维剖面。',1)
                if active_count<len(packet['rows']):add('提示',f"质检停用 {len(packet['rows'])-active_count} 条；原始记录保留，求解器只接收启用行。",1)
                report=quality_report(packet,**packet.get('quality_settings',{}))
                suspect=sum(r['enabled'] and any(f not in ('missing_voltage','missing_current') for f in r['flags']) for r in report['records'])
                if suspect:add('提示',f'启用数据中 {suspect} 条有质检提示，请在观测质检窗口核实；提示不等于坏数据。',2)
                if packet.get('modified'):add('提示','观测已人工修改，已保留原始来源；结果需按当前数据计算。',1)
            except (ValueError,KeyError,TypeError) as error:add('错误',str(error),1)
        elif self.simulation_enabled:
            if len(self.line)!=2:add('错误','合成演示需要一条直测线。',0)
            elif not 50<=np.linalg.norm(np.subtract(self.line[-1],self.line[0]))<=2000:add('错误','合成演示测线长度须50–2000m。',0)
            add('提示','当前明确选择合成演示，观测由程序生成，不代表现场。',1)
        else:add('错误','尚无观测：请在观测页导入/新增，或在第三步明确选择合成演示。',1)
        if self.campus_scene:add('提示','当前为虚拟校园，定位仅为示意。')
        if not any(i['level']=='错误' for i in issues):add('通过','必需字段及电极关联检查通过；反演后仍需检查拟合质量。')
        return issues

    def refresh_workflow(self):
        if not hasattr(self,'data_tabs'):return
        self.refresh_calibration_notice()
        index=self.workflow_step
        for i,b in enumerate(self.step_buttons):b.setChecked(i==index)
        survey_mode=bool(getattr(self,'survey_active',lambda:False)())
        survey_volume_mode=bool(survey_mode and getattr(self,'survey_state',{}).get('view')=='volume' and getattr(self,'survey_volume_current',lambda:False)())
        region_only=bool(getattr(self,'region_state',{}).get('sections') and self.model is None and not self.ert_packet and not self.points and not survey_mode)
        local_acquisition=is_local_import(self.ert_packet)
        self.step_stack.setCurrentIndex(index);self.data_tabs.setVisible(index>0 and not region_only)
        self.section_view.setVisible(False if survey_volume_mode else (
            (index==2 or self.model is not None or local_acquisition) and not region_only))
        issues=self.workflow_checks();self._workflow_issues=issues
        errors=sum(i['level']=='错误' for i in issues);warnings=sum(i['level']=='提示' for i in issues)
        self.workflow_summary.setText(f'地图测点 {len(self.points)} 个\n错误 {errors} 项 · 提示 {warnings} 项\n点击数据检查可定位问题。')
        self.check_table.setRowCount(len(issues))
        for r,issue in enumerate(issues):
            for col,value in enumerate([issue['level'],issue['text']]):
                item=Q.QTableWidgetItem(value);item.setForeground(QtGui.QColor({'错误':'#f39c91','提示':'#ffc46b','通过':'#80d3a2'}[issue['level']]))
                self.check_table.setItem(r,col,item)
        packet=self.ert_packet
        self.observation_table.setRowCount(len(packet['rows']) if packet else 0)
        if packet:
            ids=electrode_ids(packet)
            for r,row in enumerate(packet['rows']):
                enabled=row.get('enabled',True)
                values=[r+1,*[ids[i] for i in row['abmn']],row['rhoa'],'启用' if enabled else '停用']
                for col,value in enumerate(values):
                    item=Q.QTableWidgetItem(str(value));item.setToolTip(row.get('qc_note',''))
                    if not enabled:item.setForeground(QtGui.QColor('#8492a3'))
                    self.observation_table.setItem(r,col,item)
        local=packet and packet.get('binding',{}).get('mode')=='local'
        if is_local_import(packet):
            self.point_binding_note.setText(
                f'当前表是导入测线的 {packet["electrodes"]} 个输入电极：局部距离/平地参考值，未配准，只读；'
                '配准后再编辑坐标。观测记录仍可编辑。')
        else:
            self.point_binding_note.setText('当前表是地形上的测点，尚未关联观测电极；可配准关联或仅做局部二维反演。' if local else '地图与测点表同步；观测通过电极编号关联。')
        self.simulation_check.blockSignals(True);self.simulation_check.setChecked(self.simulation_enabled);self.simulation_check.setEnabled(packet is None and self.process is None);self.simulation_check.blockSignals(False)
        for widget,value in [(self.workflow_error,packet['relative_error']*100 if packet else 3),(self.workflow_lam,packet['lam'] if packet else 20),(self.workflow_iterations,packet['max_iter'] if packet else 6)]:
            widget.blockSignals(True);widget.setValue(value);widget.setEnabled(packet is not None and self.process is None);widget.blockSignals(False)
        label=array_label(packet.get('array','wenner')) if packet else ''
        self.run_summary.setText((f"{label}观测 {len(packet['rows'])} 条\n{'仅局部二维' if local else '已关联测线'} · 平地假设\n相对误差假设 {packet['relative_error']*100:g}%\n错误 {errors} 项，提示 {warnings} 项" if packet else f"{'合成演示' if self.simulation_enabled else '请先完善真实观测'}\n错误 {errors} 项"))
        if packet:
            self.run_summary.setText(self.run_summary.text().replace(f"{label}观测 {len(packet['rows'])} 条",f"{label}观测 {len(active_indices(packet))}/{len(packet['rows'])} 条启用"))
        if is_local_import(packet):
            self.run_summary.setText(self.run_summary.text().replace(
                '仅局部二维', '局部未配准 · 仅局部二维'))
        if region_only:
            self.run_summary.setText('当前展示多测线区域快照。\n请打开“区域插值工具”管理剖面、生成区域体或切片。')
            self.case_info.setText('区域模型来自独立二维剖面的线间插值。\n新建测线工作台可继续布点和导入观测。')
        self.simulation_check.setVisible(not region_only)
        self.quality_button.setEnabled(bool(packet) and self.process is None)
        self.diagnostics_button.setEnabled(bool(self.case_arrays and 'observed' in self.case_arrays) and self.process is None)
        self.run_btn.setEnabled(self.process is None and errors==0)
        if hasattr(self,'endpoint_btn'):
            local_lock=is_local_import(self.ert_packet)
            complete=self.terrain is not None and len(getattr(self,'line',[]) or [])>=2 and not local_lock
            busy=self.process is not None or bool(getattr(self,'survey_busy',lambda:False)())
            self.endpoint_btn.setEnabled(complete and not busy)
            self.endpoint_btn.setToolTip('局部导入测线未配准：源电极只读，请先配准再编辑端点。' if local_lock
                else ('编辑当前测线终点的 X/Y/Z 与高程模式' if complete
                      else '当前没有完整测线：请先拖动绘线或导入含至少两个测点的测线。'))
        if self.process is None:self.run_btn.setText('开始观测反演（Ctrl+Enter）' if packet else '开始合成演示' if self.simulation_enabled else '请先完善数据')
        data=self.case_arrays
        if data:
            self.response_table.setRowCount(len(data.get('observed',[])))
            indices=data.get('source_row_indices',np.arange(len(data.get('observed',[]))))
            for r,(obs,pred) in enumerate(zip(data.get('observed',[]),data.get('response',[]))):
                for col,value in enumerate([int(indices[r])+1,float(obs),float(pred),float((pred-obs)/obs*100)]):self.response_table.setItem(r,col,Q.QTableWidgetItem(str(value)))
        else:self.response_table.setRowCount(0)
        if survey_volume_mode:self.section_view.setVisible(False)
        if hasattr(self,'refresh_survey_ui'):self.refresh_survey_ui()
        self.refresh_sidebar_geometry()

    def show_checks(self):
        self.set_workflow_step(1);self.data_tabs.setCurrentIndex(2)

    def jump_workflow_issue(self,row,col):
        issue=self._workflow_issues[row];self.data_tabs.setCurrentIndex(issue['tab'])
        if issue['row'] is not None:
            if issue['tab']==0:self.select_point(issue['row'])
            elif issue['tab']==1:
                self.observation_table.selectRow(issue['row']);self.observation_table.scrollToItem(self.observation_table.item(issue['row'],0))

    def set_simulation_enabled(self,value):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):return
        self.simulation_enabled=value
        if self.terrain is not None:self.dirty=self.document_key()!=self.saved_geometry_key
        self.refresh_workflow()

    def commit_workflow_parameters(self):
        if not self.ert_packet or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy() and not getattr(self,'_survey_batch_running',False)):return
        for field in (self.workflow_error,self.workflow_lam,self.workflow_iterations):field.interpretText()
        values=dict(relative_error=self.workflow_error.value()/100,lam=self.workflow_lam.value(),max_iter=self.workflow_iterations.value())
        if all(self.ert_packet[k]==v for k,v in values.items()):return
        before=edit_snapshot(self)
        self.ert_packet.update(values)
        self.record_edit(before,'修改反演参数');self.redraw()

    def edit_observation_record(self,index=None):
        if ((self.terrain is None and not getattr(self,'allow_unlocated_processing',False)
                and not is_local_import(self.ert_packet))
                or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy())
                or not self.require_committed_edit()):return
        from observation_editor import ObservationRowDialog,make_manual_packet
        packet=self.ert_packet or make_manual_packet(self.points,self.line)
        dialog=ObservationRowDialog(self,packet,index)
        if dialog.exec()!=Q.QDialog.Accepted:return
        before=edit_snapshot(self);self.ert_packet=dialog.packet;self.simulation_enabled=False
        self.record_edit(before,'新增观测' if index is None else '编辑观测');self.redraw();self.set_workflow_step(1);self.data_tabs.setCurrentIndex(1)

    def add_observation(self):self.edit_observation_record()

    def edit_observation(self):
        index=self.observation_table.currentRow()
        if self.ert_packet and index>=0:self.edit_observation_record(index)
        else:self.say('请先选择一条观测。')

    def delete_observation(self):
        index=self.observation_table.currentRow()
        if not self.ert_packet or index<0 or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or not self.require_committed_edit():return
        before=edit_snapshot(self);del self.ert_packet['rows'][index];self.ert_packet['modified']=True
        self.record_edit(before,'删除观测');self.redraw()

    def register_observations(self):
        if self.terrain is None or not self.ert_packet:self.say('先加载地形并导入/新增观测。');return
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or not self.require_committed_edit():return
        from workflow_registration import RegistrationDialog
        dialog=RegistrationDialog(self)
        if dialog.exec()==Q.QDialog.Accepted:self.apply_observation_registration(dialog.binding,dialog.points,dialog.line)

    def focus_result_view(self):
        if self.model is None:self.say('请先完成反演或加载结果。');return
        if self.model_local_only:self.section_view.reset_view();self.say('已恢复二维局部剖面，三维地形保持。');return
        self.view_section();self.plotter.reset_camera(bounds=self.model.bounds);self.plotter.render()

    def apply_observation_registration(self,binding,points,line):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):raise ValueError('计算或测线批次中不能修改配准。')
        problems=validate_binding(self.ert_packet,binding,points,line,self.terrain)
        if problems:raise ValueError('\n'.join(problems))
        before=edit_snapshot(self);self.points=copy.deepcopy(points);self.line=copy.deepcopy(line);self.points_follow_line=False
        self.line_endpoint_overrides={}
        self.ert_packet['binding']=copy.deepcopy(binding)
        self.record_edit(before,'关联测点与地形');self.redraw();self.refresh_workflow()

    def use_local_observations(self):
        if not self.ert_packet or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or not self.require_committed_edit():return
        before=edit_snapshot(self);self.ert_packet['binding']={'mode':'local'}
        self.record_edit(before,'切换局部反演');self.redraw()

    def sync_observation_point_label(self,index,old,new):
        if self.ert_packet and self.ert_packet.get('binding',{}).get('mode')=='registered' and old!=new:
            ids=electrode_ids(self.ert_packet)
            if index<len(ids) and ids[index]==old:ids[index]=new;self.ert_packet['electrode_ids']=ids

    def update_workflow_result_placement(self):
        if not self.case_arrays or self.case_kind!='imported_observations' or self.result_profile_packet is None:return
        data=self.case_arrays
        try:
            valid=bool(self.ert_packet and 'binding' in self.ert_packet and observation_signature(self.ert_packet)==self.result_observation_signature and not validate_binding(self.ert_packet,self.ert_packet['binding'],self.points,self.line,self.terrain))
        except (ValueError,KeyError,TypeError):valid=False
        if (valid and self.ert_packet['binding'].get('mode')=='local'
                and self.ert_packet['binding'].get('endpoint_edit_preserve_pose') is True
                and isinstance(self.result_binding,dict) and self.result_binding.get('mode')=='registered'):
            # Switching the editable binding to local must not pull an older
            # registered inversion to the local 2-D frame; keep the old result
            # at its original (registered) position.
            valid=False
        binding=self.ert_packet['binding'] if valid else self.result_binding
        if not binding:binding={'mode':'local'}
        if valid:self.result_binding=copy.deepcopy(binding)
        key=json.dumps(binding,sort_keys=True)+str(self.case_path)
        if self._placement_key==key:return
        registered=binding.get('mode')=='registered'
        if registered:
            points,_=generate_bound_points(self.result_profile_packet,binding)
            xy=np.array([[p['x'],p['y']] for p in points]);offset=float(binding['origin'][2]);origin=self.terrain.origin
        else:
            xy=np.column_stack([data['station'],np.zeros(len(data['station']))]);offset=0.;origin=np.zeros(3)
        self.model=place_section(data['nodes'],data['cells'],data['resistivity'],xy,data['station'],origin,float(data['z_reference'])+offset)
        self.section_data={key:np.array(data[key],copy=True) for key in ('nodes','cells','resistivity','station','xy','elevation','z_reference')}
        self.section_data['xy']=xy;self.section_data['elevation']=data['elevation']+offset;self.section_data['z_reference']=data['z_reference']+offset
        self.model_local_only=not registered;self._placement_key=key

    def load_workflow_case(self,path,meta):
        if meta.get('data_kind')!='imported_observations' or not (meta.get('workflow_version') or self.ert_packet and 'binding' in self.ert_packet):return False
        names=('case_arrays','case_kind','case_path','_placement_key','result_observation_signature','result_binding',
            'result_profile_packet','result_geometry_signature','fit_warning','model','section_data','model_local_only')
        previous={name:getattr(self,name) for name in names};previous_info=self.case_info.text()
        try:return self._load_workflow_case(path,meta)
        except Exception:
            for name,value in previous.items():setattr(self,name,value)
            self.case_info.setText(previous_info)
            raise

    def _load_workflow_case(self,path,meta):
        self.case_arrays=load_result_arrays(path/'model.npz')
        self.case_kind='imported_observations';self.case_path=path.resolve();self._placement_key=None
        self.result_observation_signature=meta.get('observation_signature')
        # A source hash cannot prove the active subset, ABMN or solver settings.
        # Legacy results without a recorded input signature remain unverified.
        self.result_binding=copy.deepcopy(meta.get('binding') or (self.ert_packet.get('binding') if self.ert_packet else None) or {'mode':'local'})
        if self.result_binding.get('mode')=='registered' and self.result_binding.get('terrain_source')!=self.terrain.source:
            raise ValueError('已配准案例属于另一地形，请打开原项目或先切回局部展示。')
        station=self.case_arrays['station']
        self.result_profile_packet=dict(electrodes=len(station),spacing=float(station[1]-station[0]),
            electrode_ids=meta.get('electrode_ids',[f'E{i+1:02}' for i in range(len(station))]))
        self.result_geometry_signature=None;self.fit_warning=(f"拟合偏差较大：χ² {meta['chi2']:.2f} / RRMS {meta['rrms_percent']:.2f}%，请复核数据和假设。" if meta['chi2']>2 else '')
        label=array_label(meta.get('array','wenner'))
        self.case_info.setText(f"{label}观测 · {meta['observations']} 条\n{meta['cells']} 单元 · RRMS {meta['rrms_percent']:.2f}%\n计算时误差假设 {meta.get('assumed_relative_error',.03)*100:g}% · λ {meta.get('lam',20):g}\n平地假设，定位与拟合质量分别检查")
        hypothesis=bool(meta.get('geometry_hypothesis'))
        if not hypothesis and (path/'observations.json').is_file():
            hypothesis=bool(json.loads((path/'observations.json').read_text(encoding='utf8')).get('geometry_hypothesis'))
        if hypothesis:
            warning='假设测网定位；原观测为同线重复测量，非现场多线结论。'
            self.case_info.setText(self.case_info.text()+'\n'+warning)
            self.fit_warning=(self.fit_warning+'\n'+warning).strip()
        self.update_workflow_result_placement();return True
