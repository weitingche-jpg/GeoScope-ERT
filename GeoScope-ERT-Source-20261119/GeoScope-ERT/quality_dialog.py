"""Review measurements locally; changes are applied only on confirmation."""
import copy
import csv
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Q
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.colors import LogNorm
from ert_quality import quality_report,set_row_state
from ert_arrays import array_label, normalize_array


FLAG_TEXT=dict(invalid_abmn='电极编号无效',invalid_geometry='排列/间距无效',
    invalid_enabled='参与状态无效',missing_rhoa='未提供视电阻率',invalid_rhoa='视电阻率无效',
    missing_voltage='未提供电压',invalid_voltage='电压无效',voltage_below_threshold='低于电压筛查阈值',
    missing_current='未提供电流',invalid_current='电流无效',current_below_threshold='低于电流筛查阈值',
    duplicate_abmn='电极组合重复',outlier_rhoa='视电阻率统计离群')


def theme_axes(axes):
    axes.set_facecolor('#101720');axes.tick_params(colors='#aebed2',labelsize=8)
    for spine in axes.spines.values():spine.set_color('#3b4a5c')
    axes.xaxis.label.set_color('#bdccdd');axes.yaxis.label.set_color('#bdccdd')
    axes.title.set_color('#dbe8f7');axes.grid(alpha=.12)


class QualityDialog(Q.QDialog):
    def __init__(self,owner,packet):
        super().__init__(owner)
        self.original=copy.deepcopy(packet);self.pending=copy.deepcopy(packet);self.packet=None
        self._updating=False;self._picked_indices=[]
        self.array=normalize_array(packet.get('array','wenner'))
        self.setWindowTitle(f'电法观测质检 · {array_label(self.array)}')
        screen=Q.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1200,screen.width()-40),min(840,screen.height()-60))
        layout=Q.QVBoxLayout(self);layout.setSpacing(8)
        if self.array=='dipole-dipole':
            note_text=('统计离群可能是真实地质变化，仅供人工核实，不自动停用。'
                '分布图纵轴为偶极隔离系数 n=gap/a，不是地下深度；负原始电阻与负K可得到正视电阻率。')
        else:
            note_text='统计离群可能是真实地质变化，仅供人工核实，不自动停用。分布图纵轴为装置间距，不是地下深度。'
        note=Q.QLabel(note_text)
        self.note=note
        note.setWordWrap(True);layout.addWidget(note)
        controls=Q.QHBoxLayout();options=packet.get('quality_settings',{})
        self.voltage_min=Q.QDoubleSpinBox();self.current_min=Q.QDoubleSpinBox();self.outlier_z=Q.QDoubleSpinBox()
        for label,widget,key in [('电压阈值 mV',self.voltage_min,'voltage_min_mV'),('电流阈值 mA',self.current_min,'current_min_mA')]:
            widget.setRange(0,1e9);widget.setDecimals(6);widget.setValue(options.get(key) or 0)
            widget.setSpecialValueText('不启用');controls.addWidget(Q.QLabel(label));controls.addWidget(widget)
        self.outlier_z.setRange(.1,100);self.outlier_z.setValue(options.get('outlier_z',4.5))
        controls.addWidget(Q.QLabel('统计离群阈值'));controls.addWidget(self.outlier_z);layout.addLayout(controls)
        self.summary=Q.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        split=Q.QSplitter(QtCore.Qt.Vertical);layout.addWidget(split,1)
        self.table=Q.QTableWidget(0,11)
        self.table.setHorizontalHeaderLabels(['启用','源行','A','B','M','N','视电阻率 Ω·m','电压 mV','电流 mA','质检提示','处理原因'])
        self.table.setSelectionBehavior(Q.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(Q.QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(False);self.table.setMinimumHeight(130)
        self.table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9,Q.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(10,Q.QHeaderView.Stretch)
        split.addWidget(self.table)
        self.figure=Figure(facecolor='#0d121a',figsize=(9,2.8));self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(160);split.addWidget(self.canvas);split.setSizes([260,220])
        self.canvas.mpl_connect('pick_event',self.pick)
        row=Q.QHBoxLayout();self.reason=Q.QLineEdit();self.reason.setPlaceholderText('批量停用原因，例如：电压不稳定，等待复测')
        row.addWidget(self.reason,1)
        for text,enabled in [('停用选中',False),('恢复选中',True)]:
            button=Q.QPushButton(text);button.clicked.connect(lambda _,state=enabled:self.set_selected_enabled(state));row.addWidget(button)
        layout.addLayout(row)
        self.status=Q.QLabel('阈值为人为筛查设置；停用必须记录原因，原始测量值不会修改。')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel)
        self.ok=buttons.button(Q.QDialogButtonBox.Ok);self.ok.setText('应用质检设置')
        export=buttons.addButton('导出质检预览',Q.QDialogButtonBox.ActionRole);export.clicked.connect(self.export_preview)
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.table.itemChanged.connect(self.item_changed)
        for widget in (self.voltage_min,self.current_min,self.outlier_z):widget.valueChanged.connect(self.refresh)
        self.refresh()

    def settings(self):
        return dict(voltage_min_mV=self.voltage_min.value() or None,
            current_min_mA=self.current_min.value() or None,outlier_z=self.outlier_z.value())

    def refresh(self,*_):
        if self._updating:return
        self._updating=True
        try:
            self.report=quality_report(self.pending,**self.settings());s=self.report['summary']
            self.summary.setText(f"总观测 {s['total']} 条 · 启用 {s['active']} · 停用 {s['disabled']} · 含提示 {s['flagged']}\n"
                f"缺电压 {s['missing_voltage']} 条、缺电流 {s['missing_current']} 条：未提供不等于0；全部停用只能保存草稿。")
            self.table.setRowCount(len(self.report['records']))
            for i,record in enumerate(self.report['records']):
                row=self.pending['rows'][i]
                checkbox=Q.QTableWidgetItem();checkbox.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable|QtCore.Qt.ItemIsUserCheckable)
                checkbox.setCheckState(QtCore.Qt.Checked if record['enabled'] else QtCore.Qt.Unchecked)
                self.table.setItem(i,0,checkbox)
                values=[record['source_row'],*[v+1 for v in row['abmn']],row['rhoa'],
                    record['voltage_mV'] if record['voltage_mV'] is not None else '未提供',
                    record['current_mA'] if record['current_mA'] is not None else '未提供',
                    '；'.join(FLAG_TEXT.get(f,f) for f in record['flags']) or '未发现筛查提示',row.get('qc_note','')]
                for col,value in enumerate(values,1):
                    item=Q.QTableWidgetItem(str(value))
                    if col!=10:item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    item.setToolTip(record['note'])
                    if not record['enabled']:item.setForeground(QtGui.QColor('#8492a3'))
                    self.table.setItem(i,col,item)
            self.draw()
        finally:self._updating=False

    def draw(self):
        self.figure.clear();space,hist=self.figure.subplots(1,2)
        records=self.report['records']
        if self.array=='dipole-dipole':
            def vertical(record):
                return record.get('separation_n')
            ylabel='Dipole separation n=gap/a (not depth)'
        else:
            def vertical(record):
                return record.get('spacing_m')
            ylabel='Wenner spacing a (m), not depth'
        valid=[r for r in records if r['center_m'] is not None and vertical(r) is not None and r['rhoa'] is not None and r['rhoa']>0]
        if self.array=='dipole-dipole':
            from ert_arrays import dd_plot_levels
            levels,ticks,tick_labels,grouped=dd_plot_levels(
                [r['dipole_length_m'] for r in valid],[r['separation_n'] for r in valid])
            level_by_row={r['index']:level for r,level in zip(valid,levels)}
            def vertical(record):return level_by_row[record['index']]
            if grouped:ylabel='Groups (a, n), not depth'
        active=[r for r in valid if r['enabled']];disabled=[r for r in valid if not r['enabled']]
        self._picked_indices=[r['index'] for r in active]
        if active:
            values=np.array([r['rhoa'] for r in active]);low=float(values.min());high=float(values.max())
            if low==high:low*=.8;high*=1.2
            artist=space.scatter([r['center_m'] for r in active],[vertical(r) for r in active],
                c=values,cmap='turbo_r',norm=LogNorm(low,high),s=36,picker=5)
            self._pick_artist=artist
            colorbar=self.figure.colorbar(artist,ax=space,pad=.02);colorbar.ax.tick_params(which='both',colors='#b9c9dc',labelsize=7)
            hist.hist(np.log10(values),bins=min(16,max(3,len(values)//4)),color='#5c9fef',edgecolor='#a6c9f7')
        else:space.text(.5,.5,'No enabled data',transform=space.transAxes,ha='center',color='#aebed2')
        if disabled:space.scatter([r['center_m'] for r in disabled],[vertical(r) for r in disabled],c='#8994a3',marker='x',s=28)
        space.set_xlabel('Local distance (m)');space.set_ylabel(ylabel);space.invert_yaxis()
        if self.array=='dipole-dipole':space.set_yticks(ticks,tick_labels)
        hist.set_xlabel('log10(apparent resistivity / ohm m)');hist.set_ylabel('Enabled observations')
        for axes in (space,hist):theme_axes(axes)
        self.figure.tight_layout(pad=1.5);self.canvas.draw_idle()

    def pick(self,event):
        if event.artist is getattr(self,'_pick_artist',None) and len(event.ind):
            i=self._picked_indices[int(event.ind[0])];self.table.selectRow(i);self.table.scrollToItem(self.table.item(i,0))

    def item_changed(self,item):
        if self._updating:return
        row=self.pending['rows'][item.row()]
        if item.column()==0:
            row['enabled']=item.checkState()==QtCore.Qt.Checked
            if row['enabled'] and not self.original['rows'][item.row()].get('enabled',True):row['qc_note']='恢复参与反演'
        elif item.column()==10:row['qc_note']=item.text()
        self.refresh()

    def set_selected_enabled(self,enabled):
        indices=[i.row() for i in self.table.selectionModel().selectedRows()]
        if not indices:self.status.setText('请先选择观测行。');return
        reason=self.reason.text().strip() or ('恢复参与反演' if enabled else '')
        if not reason:self.status.setText('停用必须填写处理原因。');self.reason.setFocus();return
        for i in indices:self.pending['rows'][i].update(enabled=enabled,qc_note=reason)
        self.refresh()

    def build_packet(self):
        candidate=copy.deepcopy(self.original)
        for i,row in enumerate(self.pending['rows']):
            enabled=row.get('enabled',True);note=str(row.get('qc_note','')).strip()
            if not enabled and not note:raise ValueError(f'第{i+1}条停用观测须填写处理原因。')
            old=self.original['rows'][i]
            if enabled!=old.get('enabled',True) or note!=old.get('qc_note',''):
                candidate=set_row_state(candidate,[i],enabled,note)
        candidate['quality_settings']=self.settings()
        return candidate

    def export_preview(self):
        try:packet=self.build_packet()
        except ValueError as error:self.status.setText(str(error));return
        path,_=Q.QFileDialog.getSaveFileName(self,'导出质检预览','','CSV (*.csv)')
        if not path:return
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['原观测序号','源行','A','B','M','N','视电阻率Ωm','电压mV','电流mA','启用','提示','原因'])
                for r in quality_report(packet,**self.settings())['records']:
                    raw=packet['rows'][r['index']]
                    writer.writerow([r['index']+1,r['source_row'],*[i+1 for i in raw['abmn']],raw['rhoa'],r['voltage_mV'],r['current_mA'],r['enabled'],'；'.join(FLAG_TEXT.get(f,f) for f in r['flags']),raw.get('qc_note','')])
            self.status.setText('质检预览已导出，当前项目尚未应用；需点击应用质检设置。')
        except OSError as error:self.status.setText(str(error))

    def accept(self):
        try:self.packet=self.build_packet()
        except ValueError as error:self.status.setText(str(error));return
        super().accept()

    def reject(self):self.packet=None;super().reject()
