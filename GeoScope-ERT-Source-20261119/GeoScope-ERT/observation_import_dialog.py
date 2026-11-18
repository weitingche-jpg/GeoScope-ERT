"""Local, reviewable mapping of generic Wenner tables to observation packets."""
from pathlib import Path
import hashlib

from PySide6 import QtCore,QtGui,QtWidgets as Q

from observation_tables import (FIELDS, RESISTANCE_UNITS, RHO_UNITS, TITLES,
    UNIT_SCALES, analyze_rows, build_packet, detect_array, detect_header,
    normalize_value_kind, validate_mapping)
from ert_arrays import array_label, normalize_array
from point_files import read_tables


class ObservationImportDialog(Q.QDialog):
    """Map generic XLSX/CSV columns, review every row, then confirm explicitly."""

    def __init__(self,owner,path):
        super().__init__(owner)
        self.owner=owner
        self.path=str(Path(path).resolve())
        self.source_sha256=hashlib.sha256(Path(self.path).read_bytes()).hexdigest()
        self.tables=read_tables(path)
        if not self.tables:raise ValueError('文件中没有可读取的数据表。')
        self.excluded=set()
        self.records=[]
        self.packet=None
        self._updating=False
        self.setWindowTitle('导入通用观测表 · 排列/数值类型映射与检查')
        self.resize(1240,760)
        layout=Q.QVBoxLayout(self)
        title=Q.QLabel(f'{Path(path).name}\n本地表头规则识别工作表、表头与 A/B/M/N/视电阻率或原始电阻列；不调用模型、不猜测单位或间距。'
            '取消不会修改当前项目。绿色通过、红色错误必须修正或明确排除。')
        title.setWordWrap(True);layout.addWidget(title)
        source_row=Q.QHBoxLayout();layout.addLayout(source_row)
        source_row.addWidget(Q.QLabel('工作表 / CSV'))
        self.source=Q.QComboBox();source_row.addWidget(self.source,1)
        source_row.addWidget(Q.QLabel('表头行（1 基，0=无表头）'))
        self.header=Q.QSpinBox();self.header.setRange(0,10000);self.header.setSpecialValueText('无表头（首行为数据）')
        source_row.addWidget(self.header)
        mapping=Q.QGridLayout();layout.addLayout(mapping)
        self.columns={}
        for index,field in enumerate(FIELDS):
            mapping.addWidget(Q.QLabel(TITLES[field]),0,index)
            combo=Q.QComboBox();mapping.addWidget(combo,1,index);self.columns[field]=combo
        settings=Q.QHBoxLayout();layout.addLayout(settings)
        settings.addWidget(Q.QLabel('排列'))
        self.array=Q.QComboBox();self.array.addItem('自动识别','auto')
        self.array.addItem('温纳 A–M–N–B','wenner')
        self.array.addItem('偶极—偶极 A–B–M–N','dipole-dipole')
        settings.addWidget(self.array)
        self.detection=None
        settings.addWidget(Q.QLabel('数值类型'))
        self.value_kind=Q.QComboBox()
        self.value_kind.addItem('视电阻率 rhoa','rhoa')
        self.value_kind.addItem('原始电阻 R','resistance')
        settings.addWidget(self.value_kind)
        settings.addWidget(Q.QLabel('电极数量（含未使用电极）'))
        self.electrodes=Q.QSpinBox();self.electrodes.setRange(4,256);self.electrodes.setValue(24)
        settings.addWidget(self.electrodes)
        settings.addWidget(Q.QLabel('基础间距 / m（必须核实）'))
        self.spacing=Q.QDoubleSpinBox();self.spacing.setRange(0,100000);self.spacing.setDecimals(6)
        self.spacing.setSingleStep(.1);self.spacing.setSpecialValueText('未核实（必填）')
        settings.addWidget(self.spacing)
        settings.addWidget(Q.QLabel('数值单位'))
        self.unit=Q.QComboBox();self.unit.addItems(list(UNIT_SCALES))
        settings.addWidget(self.unit);settings.addStretch(1)
        self.tabs=Q.QTabWidget();layout.addWidget(self.tabs,1)
        self.raw_preview=Q.QTableWidget();self.raw_preview.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.raw_preview.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeToContents)
        self.tabs.addTab(self.raw_preview,'原始表格（源文件行号）')
        self.preview=Q.QTableWidget();self.preview.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.preview.setSelectionBehavior(Q.QAbstractItemView.SelectRows)
        self.preview.setColumnCount(9)
        self.preview.setHorizontalHeaderLabels(['导入','源行','A','B','M','N','原始视电阻率','视电阻率 / Ω·m','检查结果'])
        self._update_preview_headers()
        self.preview.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        self.tabs.addTab(self.preview,'转换结果与错误检查');self.tabs.setCurrentIndex(1)
        self.summary=Q.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.confirm=Q.QCheckBox('我已核对：A/B/M/N 为从1开始的电极编号，电极数量、基础间距和电阻率单位正确；'
            '未排除的行均合法。导入后仍将在现有确认窗口核对误差与平地假设。')
        layout.addWidget(self.confirm)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel);layout.addWidget(buttons)
        self.ok=buttons.button(Q.QDialogButtonBox.Ok);self.ok.setText('进入最终确认');self.ok.setEnabled(False)
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消');buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.source.currentIndexChanged.connect(self.select_source)
        self.header.valueChanged.connect(self.header_changed)
        for combo in self.columns.values():combo.currentIndexChanged.connect(self.refresh)
        self.electrodes.valueChanged.connect(self.refresh);self.spacing.valueChanged.connect(self.refresh)
        self.unit.currentIndexChanged.connect(self.refresh);self.confirm.toggled.connect(self.refresh)
        self.array.currentIndexChanged.connect(self.array_changed)
        self.value_kind.currentIndexChanged.connect(self.value_kind_changed)
        self.preview.itemChanged.connect(self.exclusion_changed)
        self.source.addItems([name for name,_ in self.tables])
        best=max(range(len(self.tables)),key=lambda index:len(detect_header(self.tables[index][1])[1]))
        self.source.blockSignals(True);self.source.setCurrentIndex(best);self.source.blockSignals(False)
        self.select_source()

    def table_rows(self):
        return self.tables[self.source.currentIndex()][1]

    def table_name(self):
        return self.tables[self.source.currentIndex()][0]

    def header_index(self):
        return self.header.value()-1 if self.header.value()>0 else -1

    def mapping(self):
        return {field:combo.currentData() for field,combo in self.columns.items()
            if combo.currentData() is not None and combo.currentData()>=0}

    def current_array(self):
        data=self.array.currentData() or 'auto'
        return 'auto' if data=='auto' else normalize_array(data)

    def current_value_kind(self):
        return normalize_value_kind(self.value_kind.currentData() or 'rhoa')

    def _update_preview_headers(self):
        kind=self.current_value_kind()
        raw='原始电阻 R' if kind=='resistance' else '原始视电阻率'
        converted='换算视电阻率 / Ω·m' if kind=='resistance' else '视电阻率 / Ω·m'
        self.preview.setHorizontalHeaderLabels(['导入','源行','A','B','M','N',raw,converted,'检查结果'])

    def array_changed(self,*_):
        self._update_preview_headers()
        self.refresh()

    def value_kind_changed(self,*_):
        kind=self.current_value_kind()
        units=RESISTANCE_UNITS if kind=='resistance' else RHO_UNITS
        if self.unit.currentText() not in units:
            self.unit.blockSignals(True)
            self.unit.setCurrentText('Ω' if kind=='resistance' else 'Ω·m')
            self.unit.blockSignals(False)
        self._update_preview_headers()
        self.refresh()

    def select_source(self,*_):
        self.excluded=set()
        rows=self.table_rows();index,detected=detect_header(rows)
        width=max((len(row) for row in rows),default=0)
        self.raw_preview.setColumnCount(width);self.raw_preview.setRowCount(len(rows))
        self.raw_preview.setHorizontalHeaderLabels([str(i+1) for i in range(width)])
        for i,row in enumerate(rows):
            for j,value in enumerate(row):self.raw_preview.setItem(i,j,Q.QTableWidgetItem(str(value)))
        self.header.blockSignals(True)
        self.header.setMaximum(len(rows))
        self.header.setValue(index+1 if detected else 0)
        self.header.blockSignals(False)
        self.detect_mapping()

    def header_changed(self,*_):
        self.excluded=set()
        self.detect_mapping()

    def detect_mapping(self,*_):
        rows=self.table_rows();index=self.header_index()
        headers=rows[index] if 0<=index<len(rows) else []
        _,detected=detect_header([headers]) if index>=0 else (-1,{})
        width=max((len(row) for row in rows),default=0)
        for field,combo in self.columns.items():
            combo.blockSignals(True);combo.clear();combo.addItem('不使用',-1)
            for col in range(width):
                label=headers[col] if col<len(headers) and str(headers[col]).strip() else '无表头'
                combo.addItem(f'{col+1}: {label}',col)
            combo.setCurrentIndex(detected.get(field,-1)+1)
            combo.blockSignals(False)
        lower=' '.join(str(value).lower() for value in headers)
        kind=self.current_value_kind()
        units=RESISTANCE_UNITS if kind=='resistance' else RHO_UNITS
        target=self.unit.currentText() if self.unit.currentText() in units else (
            'Ω' if kind=='resistance' else 'Ω·m')
        if 'kω' in lower or 'kohm' in lower or 'kω·m' in lower:
            target='kΩ' if kind=='resistance' else 'kΩ·m'
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(max(0,self.unit.findText(target)))
        self.unit.blockSignals(False)
        self.refresh()

    def raw_value(self,record,field):
        mapping=self.mapping();col=mapping.get(field,-1)
        return record['raw'][col] if 0<=col<len(record['raw']) else ''

    def refresh(self,*_):
        if self._updating or not hasattr(self,'preview'):return
        self._updating=True
        try:
            rows=self.table_rows();index=self.header_index();mapping=self.mapping()
            width=max((len(row) for row in rows),default=0)
            try:
                validate_mapping(mapping,width)
            except ValueError as error:
                self.records=[];self.detection=None;self.preview.setRowCount(0)
                self.summary.setText(f'字段映射未完成：{error}')
                self.ok.setEnabled(False)
                return
            unit=self.unit.currentText();array=self.current_array();kind=self.current_value_kind()
            self._update_preview_headers()
            self.detection=(detect_array(rows,index,mapping,self.excluded,self.electrodes.value())
                if array=='auto' else None)
            try:
                self.records=analyze_rows(rows,index,mapping,unit,self.excluded,self.electrodes.value(),
                    array=array,value_kind=kind,spacing=self.spacing.value())
            except ValueError as error:
                self.records=[];self.preview.setRowCount(0)
                self.summary.setText(f'无法预览：{error}');self.ok.setEnabled(False);return
            self.preview.blockSignals(True);self.preview.setRowCount(len(self.records))
            valid=errors=excluded=negative_r=negative_k=0
            for row,record in enumerate(self.records):
                excluded_state=record['excluded']
                if excluded_state:excluded+=1
                elif record['error']:errors+=1
                else:valid+=1
                if record.get('resistance') is not None and record['resistance']<0:negative_r+=1
                if record.get('k') is not None and record['k']<0:negative_k+=1
                check=Q.QTableWidgetItem()
                check.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsUserCheckable)
                check.setCheckState(QtCore.Qt.Unchecked if excluded_state else QtCore.Qt.Checked)
                check.setToolTip('取消勾选即明确排除该行；重复组合和排列识别会在排除后重新计算。')
                self.preview.setItem(row,0,check)
                abmn=record['abmn_input'] or [self.raw_value(record,field) for field in ('a','b','m','n')]
                raw_input=(record['resistance_input'] if kind=='resistance' else record['rhoa_input'])
                rho_text=f"{record['rhoa']:.10g}" if record['rhoa'] is not None else ''
                if excluded_state:
                    result='已排除'+(f'；{record["error"]}' if record['error'] else '')
                elif record['error']:
                    result=record['error']
                elif array=='auto' and record['array']:
                    result=f"通过（{array_label(record['array'])}）"
                else:result='通过'
                values=[str(record['source_row']),*[str(value) for value in abmn],
                    raw_input,rho_text,result]
                for col,value in enumerate(values,1):
                    item=Q.QTableWidgetItem(str(value));self.preview.setItem(row,col,item)
                    if record['error'] and not excluded_state:item.setForeground(QtGui.QColor('#ed917f'))
                    elif excluded_state:item.setForeground(QtGui.QColor('#7f8c8d'))
                    elif col==8:item.setForeground(QtGui.QColor('#8fd694'))
                    if kind=='resistance' and record.get('k') is not None:
                        item.setToolTip(f"K={record['k']:.8g} m；rhoa=K×R")
            self.preview.blockSignals(False)
            self.preview.resizeColumnsToContents()
            unit_ready=bool(unit)
            spacing_ready=self.spacing.value()>0
            detection_ok=array!='auto' or (self.detection is not None and self.detection['status']=='ok')
            can_import=(valid>0 and errors==0 and self.confirm.isChecked() and unit_ready
                and spacing_ready and detection_ok)
            self.ok.setEnabled(can_import)
            note=''
            if not spacing_ready:note=' 请填写并核实基础间距（观测表不能可靠推断未使用电极或真实间距）。'
            if not self.confirm.isChecked():note+=' 确认勾选后才能继续。'
            if array=='auto':
                if self.detection is not None and self.detection['status']=='ok':
                    arrangement=f"自动识别 → {array_label(self.detection['array'])}"
                else:arrangement='自动识别未通过'
                detection_note=(self.detection or {}).get('message','')
                array_text=('自动识别' if self.detection is None or self.detection['status']!='ok'
                    else array_label(self.detection['array']))
            else:
                arrangement=array_label(array);detection_note='';array_text=arrangement
            conversion=''
            if kind=='resistance':
                conversion=(f' · {array_text}：原始电阻负值 {negative_r} 条、几何因子 K 负值 {negative_k} 条；'
                    '按 rhoa=K×R 换算，未排序电极、未取绝对值。')
            self.summary.setText(f'{self.table_name()} · {arrangement} · 表头行 {self.header.value() or "无"} · 有效 {valid} 条 / '
                f'未排除错误 {errors} 条 / 已排除 {excluded} 条。{detection_note}{conversion}{note}')
        finally:
            self._updating=False

    def exclusion_changed(self,item):
        if self._updating or item.column()!=0:return
        index=self.records[item.row()]['index']
        if item.checkState()==QtCore.Qt.Unchecked:self.excluded.add(index)
        else:self.excluded.discard(index)
        self.refresh()

    def accept(self):
        self.refresh()
        if not self.ok.isEnabled():return
        try:
            packet=build_packet(self.path,self.table_name(),self.table_rows(),self.header_index(),
                self.mapping(),self.unit.currentText(),self.electrodes.value(),self.spacing.value(),self.excluded,
                array=self.current_array(),value_kind=self.current_value_kind())
            if packet['source_sha256']!=self.source_sha256:
                raise ValueError('源文件在预览期间发生变化，请取消并重新导入，核对最新数据。')
        except Exception as error:
            self.summary.setText(f'无法生成观测包：{error}')
            self.ok.setEnabled(False)
            return
        self.packet=packet
        super().accept()

    def reject(self):
        self.packet=None
        super().reject()
