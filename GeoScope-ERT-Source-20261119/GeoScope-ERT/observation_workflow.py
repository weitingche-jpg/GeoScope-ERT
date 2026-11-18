"""Preview observed data and apply it to the current terrain project."""
import copy
import json
from pathlib import Path
import sys
from uuid import uuid4
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from core import write_json
from observed_ert import read_observations,validate_packet,geometry_signature
from observation_import_dialog import ObservationImportDialog
from observation_tables import is_instrument_csv
from ert_arrays import array_label,normalize_array
from ert_protocol import is_r2_protocol,read_r2_protocol
from workflow_geometry import validate_binding,observation_signature,physical_signature
from shortcuts import edit_snapshot
from line_import import LineImportMixin,is_local_import
from workflow_geometry import electrode_ids, fit_map_line


ROOT=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent


class ObservationDialog(Q.QDialog):
    def __init__(self,owner,packet,line_import=False):
        super().__init__(owner);self.packet=copy.deepcopy(packet);self.line_import=bool(line_import)
        has_map_line = getattr(owner, 'has_map_import_line', None)
        self.on_map = not self.line_import and callable(has_map_line) and has_map_line()
        array=normalize_array(packet.get('array','wenner'))
        self.setWindowTitle((f'导入测线数据 · {array_label(array)}' if self.line_import
                             else f'导入电法观测 · {array_label(array)}'));self.resize(940,640)
        layout=Q.QVBoxLayout(self)
        extra=''
        if array=='dipole-dipole':
            negative=sum(1 for row in packet['rows'] if (row.get('resistance') or 0)<0)
            negative_k=sum(1 for row in packet['rows'] if (row.get('k') or 0)<0)
            extra=(f'\n原始电阻负值 {negative} 条、带符号 K 负值 {negative_k} 条；'
                   'rhoa=K×R 保持为正，未排序电极、未取绝对值。')
        files=packet.get('source_files') if isinstance(packet.get('source_files'),dict) else {}
        file_text='、'.join(f'{role}={Path(value).name}' for role,value in files.items())
        source_text=(f'来源文件：{file_text}' if file_text else
                     f"文件：{packet.get('source_name','手动录入观测')}")
        if self.line_import:
            length=(packet['electrodes']-1)*packet['spacing']
            local=(f'\n{source_text}\n将新建一条局部测线：{packet["electrodes"]} 个输入电极 · '
                   f'间距 {packet["spacing"]:.6g} m · 长 {length:g} m · {len(packet["rows"])} 条观测{extra}\n'
                   '工作台坐标仅为局部距离/平地参考值，未配准；不混入默认24点，不叠加到地形。'
                   '确认后整次导入可一步撤销；取消不改变当前项目。')
        elif self.on_map:
            length=(packet['electrodes']-1)*packet['spacing']
            local=(f"{source_text}\n{packet['electrodes']} 个电极 · 间距 {packet['spacing']:g} m · "
                   f"需测线长至少 {length:g} m · {len(packet['rows'])} 条观测{extra}\n"
                   "导入后从当前测线起点按实际间距布点，更新该线的电极与观测。")
        else:
            local=(f"{source_text}\n{array_label(array)} · {packet['electrodes']} 电极 · "
                   f"间距 {packet['spacing']:.6g} m · {len(packet['rows'])} 条观测{extra}\n"
                   '观测进入当前项目，地形及测点保持；可随后配准关联。')
        label=Q.QLabel(local)
        label.setWordWrap(True);self.description=label;layout.addWidget(label)
        self.destination=Q.QComboBox();self.destination.addItems(['仅做局部反演（保留地形，结果只在二维显示）','导入后关联当前地形（端点配准 / 同编号电极）'])
        if self.line_import:
            self.destination.setCurrentIndex(0);self.destination.setEnabled(False)
            self.destination.setToolTip('测线导入先建立局部未配准工作区；请在导入后使用“一键校核与配准”。')
        if not self.on_map:
            layout.addWidget(self.destination)
        else:
            self.destination.hide()
        columns=(['源行','A 供电','B 供电','M 测量','N 测量','原始电阻 / Ω',
                  '几何因子 K / m','视电阻率 / Ω·m'] if array=='dipole-dipole'
                 else ['源行','A 供电','B 供电','M 测量','N 测量','视电阻率 / Ω·m'])
        table=Q.QTableWidget(len(packet['rows']),len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        ids=electrode_ids(packet)
        for i,row in enumerate(packet['rows']):
            base=[row.get('source_row',i+1),*[ids[j] for j in row['abmn']]]
            if array=='dipole-dipole':
                values=[*base,row.get('resistance',''),row.get('k',''),row['rhoa']]
            else:
                values=[*base,row['rhoa']]
            for j,v in enumerate(values):table.setItem(i,j,Q.QTableWidgetItem(str(v)))
        self.table=table
        table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        # The table is the flexible part of the dialog: on small/high-DPI screens it
        # yields height so the confirmation panel and buttons stay visible.
        table.setMinimumHeight(60)
        table.setSizePolicy(Q.QSizePolicy.Expanding,Q.QSizePolicy.Ignored)
        layout.addWidget(table,1);layout.setStretchFactor(table,1)
        form=Q.QFormLayout();layout.addLayout(form)
        self.error=Q.QDoubleSpinBox();self.error.setRange(.1,30);self.error.setValue(packet['relative_error']*100);self.error.setSuffix(' %')
        self.lam=Q.QDoubleSpinBox();self.lam.setRange(.1,1000);self.lam.setValue(packet['lam'])
        self.iterations=Q.QSpinBox();self.iterations.setRange(1,30);self.iterations.setValue(packet['max_iter'])
        form.addRow('假设相对误差（文件未提供）',self.error);form.addRow('平滑正则化 λ',self.lam);form.addRow('最大迭代次数',self.iterations)
        note=Q.QLabel('本阶段采用相对高程0的平地假设，地面是参考面。剖面深度为反演模型坐标，不等同于可信探测深度。')
        note.setWordWrap(True);layout.addWidget(note)
        panel=Q.QFrame();panel.setObjectName('confirmationPanel')
        panel.setStyleSheet('QFrame#confirmationPanel {border:1px solid #5d7fa6; border-radius:6px; background:#15202c;}'
            'QFrame#confirmationPanel QLabel {border:0; background:transparent;}'
            'QFrame#confirmationPanel QCheckBox {border:0; background:transparent; color:#eaf2fb; spacing:9px;}'
            'QFrame#confirmationPanel QCheckBox::indicator {width:20px; height:20px; border:2px solid #a5b8ce; border-radius:3px; background:#0b121b;}'
            'QFrame#confirmationPanel QCheckBox::indicator:checked {background:#428af0; border-color:#c6ddff;}'
            'QFrame#confirmationPanel QCheckBox:focus {color:#ffffff;}')
        panel_layout=Q.QVBoxLayout(panel)
        self.confirm_status=Q.QLabel();self.confirm_status.setWordWrap(True);panel_layout.addWidget(self.confirm_status)
        self.confirm=Q.QCheckBox('我已核对电极与间距，并接受以上假设')
        panel_layout.addWidget(self.confirm)
        self.confirm_error=Q.QLabel('请先勾选确认')
        self.confirm_error.setWordWrap(True);self.confirm_error.setStyleSheet('color:#ff8b8b; font-weight:600;')
        self.confirm_error.hide();panel_layout.addWidget(self.confirm_error)
        layout.addWidget(panel)
        self.confirm.toggled.connect(self._confirmation_changed)
        self._confirmation_changed(False)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel);layout.addWidget(buttons)
        self.ok=buttons.button(Q.QDialogButtonBox.Ok);self.ok.setText(self.import_action())
        self.ok.setObjectName('primary');self.ok.setDefault(True);self.ok.setEnabled(True)
        self.ok.setStyleSheet('QPushButton {background:#428af0; color:white; border:0; font-weight:600;}'
            'QPushButton:hover {background:#5a9cf6;}')
        self.cancel=buttons.button(Q.QDialogButtonBox.Cancel);self.cancel.setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)

    def import_action(self):
        if self.on_map:
            return '导入并沿测线布点'
        return '导入为局部测线' if self.line_import else '导入当前项目'

    def _confirmation_changed(self,checked):
        action=self.import_action()
        frame=('地图电极使用当前测线，反演采用相对高程 0 的平地假设。' if self.on_map
               else '采用相对高程 0 的平地假设，观测尚未地理定位。')
        if checked:
            self.confirm_status.setStyleSheet('color:#7ee2a8; font-weight:600;')
            self.confirm_status.setText(f'已确认，可以{action}。\n相对误差仍为人为假设。{frame}')
            self.confirm_error.hide()
        else:
            self.confirm_status.setStyleSheet('color:#ffd166; font-weight:600;')
            self.confirm_status.setText(f'尚未确认，还差勾选确认。\n相对误差是人为假设值。{frame}请勾选下方确认后方可{action}。')

    def accept(self):
        if not self.confirm.isChecked():
            self.confirm_error.setText('请先勾选确认')
            self.confirm_error.show();self.confirm_error.raise_()
            self.confirm.setFocus(QtCore.Qt.OtherFocusReason)
            return
        self.packet.update(relative_error=self.error.value()/100,lam=self.lam.value(),max_iter=self.iterations.value())
        self.packet['confirmed']=True
        self.packet['request_registration']=(False if self.line_import or self.on_map
            else self.destination.currentIndex()==1)
        super().accept()


class ObservationWorkflow(LineImportMixin):
    def import_observations(self):
        if (self.terrain is None and not getattr(self,'allow_unlocated_processing',False)) or self.process is not None:self.say('请先加载地形，并等待计算结束。');return
        if not self.require_committed_edit():return
        path,_=Q.QFileDialog.getOpenFileName(self,'导入电法观测（R2 protocol.dat、原始仪器CSV/DAT或通用表格）','',
            '电法观测 (*.csv *.dat *.xlsx);;R2 protocol (protocol.dat);;原始仪器 (*.csv *.dat);;通用表格 (*.xlsx *.csv)')
        if not path:return
        try:
            if is_r2_protocol(path):
                # R2 protocol.dat reserves every .dat suffix in the generic
                # instrument detector.  Ask for its electrode file before
                # touching project state; cancellation is atomic.
                electrode_path,_=Q.QFileDialog.getOpenFileName(
                    self,'选择R2电极坐标文件 electrodes.dat',
                    str(Path(path).with_name('electrodes.dat')),
                    'R2 电极文件 (electrodes.dat *.dat);;所有文件 (*)')
                if not electrode_path:
                    self.say('未导入：缺少电极坐标文件electrodes.dat，原观测保持')
                    return
                packet=read_r2_protocol(path,electrode_path)
            elif is_instrument_csv(path):
                # Recognised instrument exports must pass the original strict parser;
                # a failure is reported and never retried as a generic table.
                packet=read_observations(path)
            else:
                mapping=ObservationImportDialog(self,path)
                if mapping.exec()!=Q.QDialog.Accepted:return
                packet=mapping.packet
            dialog=ObservationDialog(self,packet)
            if dialog.exec()==Q.QDialog.Accepted:self.open_observation_packet(dialog.packet)
        except Exception as error:self.error(error)

    def open_observation_packet(self,packet):
        packet=validate_packet(copy.deepcopy(packet))
        if (self.terrain is None and not getattr(self,'allow_unlocated_processing',False)) or self.process is not None:raise ValueError('请先加载地形，并等待计算结束。')
        if not self.require_committed_edit():return self
        if self.has_map_import_line():
            binding, points = fit_map_line(packet, self.line, self.terrain)
            before = edit_snapshot(self)
            packet.pop('request_registration', None)
            packet.update(binding=binding, confirmed=True)
            self.points = points
            self.points_follow_line = False
            self.ert_packet = packet
            self.simulation_enabled = False
            self.record_edit(before, '导入观测并沿测线布点')
            self.redraw()
            self.set_workflow_step(1)
            self.say(f"已布置 {len(points)} 个电极，间距 {packet['spacing']:g} m，电极跨度 {binding['length']:g} m。")
            return self
        before=edit_snapshot(self);register=packet.pop('request_registration',False)
        packet['binding']={'mode':'local'};packet['confirmed']=True
        self.ert_packet=packet;self.simulation_enabled=False
        self.record_edit(before,'导入观测');self.redraw();self.set_workflow_step(1);self.data_tabs.setCurrentIndex(1)
        self.say('观测已进入当前项目，原地形和测点保持。可关联到地形，或明确保留局部二维反演。')
        if register:self.calibrate_observations()
        return self

    def local_observation_labels(self):
        if not self.ert_packet:return
        packet=self.ert_packet
        if is_local_import(packet):
            meta=packet.get('line_import') or {}
            length=(packet['electrodes']-1)*packet['spacing']
            self.title.setText(f"{meta.get('source_name','导入测线')} · 局部测线")
            self.scene_badge.setText('局部测线导入 / 未配准 / 不叠加地形')
            self.info.setText(f"局部距离 m · {packet['electrodes']} 个输入电极\n"
                f"长 {length:g} m · {len(packet['rows'])} 条观测\n"
                "局部距离/平地参考值，未配准\n"
                f"误差假设 {packet['relative_error']*100:.3g}%\n"
                f"{meta.get('source_name','导入测线')}")
            self.setWindowTitle(f"GeoScope · 窗口 {self.window_number:02} · {meta.get('source_name','导入测线')} · 局部未配准")
            return
        if 'binding' in packet:
            self.scene_badge.setText('观测已关联当前地形 / 平地假设' if packet['binding'].get('mode')=='registered' else '观测未配准 / 仅局部二维反演')
            return
        array=normalize_array(packet.get('array','wenner'))
        self.title.setText(f'{array_label(array)}实验观测 · 局部测线')
        self.scene_badge.setText('导入观测 / 平地假设 / 未地理定位')
        self.info.setText(f"局部距离 m · {packet['electrodes']} 电极\n间距 {packet['spacing']:.6g} m · {len(packet['rows'])} 观测\n高程0为假设值，不是实测\n误差假设 {packet['relative_error']*100:.3g}%\n{packet.get('source_name','手动录入观测')}")
        self.setWindowTitle(f"GeoScope · 窗口 {self.window_number:02} · {packet.get('source_name','手动录入观测')} · 局部测线")

    def observation_geometry_matches(self):
        if not self.ert_packet:return False
        p=self.ert_packet
        if 'binding' in p:return not validate_binding(p,p['binding'],self.points,self.line,self.terrain)
        expected=[dict(id=f'E{i+1:02}',x=float(i*p['spacing']),y=0.,z=0.) for i in range(p['electrodes'])]
        line=[[0.,0.],[float((p['electrodes']-1)*p['spacing']),0.]]
        return geometry_signature(self.points,self.line)==geometry_signature(expected,line)

    def run_observed_inversion(self):
        if self.process is not None:self.say('反演正在运行。');return
        if not self.require_committed_edit():return
        try:
            self.commit_workflow_parameters()
            packet=validate_packet(self.ert_packet)
            local=is_local_import(packet)
            if not self.observation_geometry_matches():raise ValueError('电极坐标/编号或测线已改变。请撤销恢复原始布设，或重新导入观测；本阶段不支持修改采集几何。')
            if not self.terrain and not local:raise ValueError('请先加载地形。')
            if any(i['level']=='错误' for i in self.workflow_checks()):raise ValueError('请先处理数据检查中的红色错误。')
            self.set_workflow_step(2)
            if 'binding' in packet:
                station=np.arange(packet['electrodes'])*packet['spacing']
                xy=np.column_stack([station,np.zeros(len(station))]);elevation=np.zeros(len(station))
                line=[[0.,0.],[float(station[-1]),0.]]
                signature=physical_signature([dict(x=float(s),y=0.,z=0.) for s in station],line)
            else:
                if not np.allclose(self.terrain.height([[p['x'],p['y']] for p in self.points]),0,atol=.001):raise ValueError('参考面不符合本次平地假设。')
                station=[p['x'] for p in self.points];xy=[[p['x'],p['y']] for p in self.points];elevation=[p['z'] for p in self.points]
                line=self.line;signature=geometry_signature(self.points,self.line)
            output=ROOT/'runs'/('observed-result-'+uuid4().hex[:12])
            request=dict(mode='observed',station=np.asarray(station).tolist(),xy=np.asarray(xy).tolist(),
                elevation=np.asarray(elevation).tolist(),
                crs=(self.terrain.crs if self.terrain is not None else 'LOCAL_METRE'),
                terrain_source=(self.terrain.source if self.terrain is not None else ''),
                line_vertices=line,output=str(output),observations=copy.deepcopy(packet),geometry_signature=signature)
            write_json(output/'request.json',request);self.current_request=output
            self.process=QtCore.QProcess(self);self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.worker_log);self.process.finished.connect(self.worker_done)
            self.process.errorOccurred.connect(self.worker_error)
            self.run_btn.setEnabled(False);self.run_btn.setText('正在计算观测反演')
            args=['--worker',str(output/'request.json')] if getattr(sys,'frozen',False) else [str(ROOT/'app.py'),'--worker',str(output/'request.json')]
            self.process.start(sys.executable,args);self.update_history_actions();self.refresh_workflow()
            if self.process is not None:
                from ert_quality import active_indices
                self.say(f"已启动导入观测反演：{len(active_indices(packet))}/{len(packet['rows'])} 条启用观测，停用行保留在项目中。")
        except Exception as error:self.error(error)
