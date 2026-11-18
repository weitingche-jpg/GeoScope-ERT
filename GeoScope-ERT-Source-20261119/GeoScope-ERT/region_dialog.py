"""A roomy, cancelable multi-profile project editor."""
import copy
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from region_project import capture_current,section_from_project
from region_model import default_settings


class RegionDialog(Q.QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.pending=copy.deepcopy(owner.region_state)
        self.setWindowTitle('多测线区域建模 · 剖面集合与插值')
        screen=Q.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1180,screen.width()-40),min(820,screen.height()-70))
        layout=Q.QVBoxLayout(self);layout.setSpacing(12)
        label=Q.QLabel('每条测线先独立完成导入、配准和反演，再收录当前剖面或导入已保存项目。\n'
            '首版仅支持近似平行直线；快照不会被工作台后续编辑覆盖，更新时请重新收录。线间插值不是三维反演。')
        label.setWordWrap(True);layout.addWidget(label)
        self.table=Q.QTableWidget(0,7);self.table.setMinimumHeight(140)
        self.table.setHorizontalHeaderLabels(['显示/参与','测线名称','来源类别','长度 m','单元数','χ² / 拟合提示','来源项目/案例'])
        self.table.setSelectionBehavior(Q.QAbstractItemView.SelectRows);self.table.setSelectionMode(Q.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeToContents)
        for c in (1,6):self.table.horizontalHeader().setSectionResizeMode(c,Q.QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36);layout.addWidget(self.table,1)
        self.table.itemChanged.connect(self.item_changed);self._updating=False
        row=Q.QHBoxLayout();row.setSpacing(10)
        for text,callback in [('收录当前剖面',self.capture),('导入测线项目',self.import_project),('移除选中',self.remove_selected),('打开测线副本',self.open_selected)]:
            button=Q.QPushButton(text);button.setMinimumHeight(40);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row)
        row=Q.QHBoxLayout();row.setSpacing(10)
        for text,callback in [('新建测线工作台',self.new_line),('区域模拟演示（新窗口）',self.demo),('按剖面重设范围',self.reset_bounds),('聚焦区域',lambda:self.owner.focus_region_view())]:
            button=Q.QPushButton(text);button.setMinimumHeight(40);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row)
        group=Q.QGroupBox('区域范围（同地形投影坐标，单位米；Z为绝对高程）');grid=Q.QGridLayout(group);grid.setHorizontalSpacing(14);grid.setVerticalSpacing(10)
        self.bounds=[]
        for i,title in enumerate(('X 最小','X 最大','Y 最小','Y 最大','Z 底部','Z 顶部')):
            widget=Q.QDoubleSpinBox();widget.setRange(-1e10,1e10);widget.setDecimals(3)
            self.bounds.append(widget);grid.addWidget(Q.QLabel(title),i//2,(i%2)*2);grid.addWidget(widget,i//2,(i%2)*2+1)
        layout.addWidget(group)
        options=Q.QHBoxLayout();options.setSpacing(12)
        self.hstep=Q.QDoubleSpinBox();self.vstep=Q.QDoubleSpinBox();self.gap=Q.QDoubleSpinBox()
        for name,widget in [('水平网格 m',self.hstep),('垂向网格 m',self.vstep),('允许线距 m',self.gap)]:
            widget.setRange(.01,100000);widget.setDecimals(2);options.addWidget(Q.QLabel(name));options.addWidget(widget)
        layout.addLayout(options)
        row=Q.QHBoxLayout();row.setSpacing(12)
        self.show_sections=Q.QCheckBox('独立剖面');self.show_volume=Q.QCheckBox('区域模型');self.show_missing=Q.QCheckBox('缺数据灰网格')
        for widget in (self.show_sections,self.show_volume,self.show_missing):row.addWidget(widget)
        self.mode=Q.QComboBox();self.mode.addItems(['电阻率外表面','数据范围分类','水平切片'])
        row.addWidget(self.mode);self.slice_z=Q.QDoubleSpinBox();self.slice_z.setRange(-1e7,1e7);self.slice_z.setPrefix('Z = ');row.addWidget(self.slice_z)
        self.opacity=Q.QDoubleSpinBox();self.opacity.setRange(.05,1);self.opacity.setSingleStep(.1)
        row.addWidget(Q.QLabel('区域透明度'));row.addWidget(self.opacity);layout.addLayout(row)
        self.status=Q.QLabel('分类：绿色为剖面邻近的网格显示带，蓝色为线间插值，灰色网格为缺数据。邻近带不代表实测三维单元。')
        self.status.setWordWrap(True);self.status.setMinimumHeight(42);layout.addWidget(self.status)
        row=Q.QHBoxLayout();row.setSpacing(10)
        for text,callback in [('生成区域模型',self.generate),('导出区域模型',self.export),('应用并查看',self.accept),('取消未应用修改',self.reject)]:
            button=Q.QPushButton(text);button.setMinimumHeight(42);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row);self.fill_controls();self.refresh_table()

    def fill_controls(self):
        settings=self.pending['settings'];display=self.pending['display']
        for widget,value in zip(self.bounds,settings['bounds']):widget.setValue(value)
        for widget,key in [(self.hstep,'horizontal_step'),(self.vstep,'vertical_step'),(self.gap,'max_gap')]:widget.setValue(settings[key])
        for widget,key in [(self.show_sections,'sections'),(self.show_volume,'volume'),(self.show_missing,'missing')]:widget.setChecked(display[key])
        self.mode.setCurrentIndex(['resistivity','classification','slice'].index(display['mode']))
        self.slice_z.setValue(display['slice_z']);self.opacity.setValue(display['opacity'])

    def collect(self):
        result=copy.deepcopy(self.pending)
        result['settings']=dict(bounds=[w.value() for w in self.bounds],horizontal_step=self.hstep.value(),vertical_step=self.vstep.value(),max_gap=self.gap.value())
        result['display']=dict(sections=self.show_sections.isChecked(),volume=self.show_volume.isChecked(),missing=self.show_missing.isChecked(),
            mode=['resistivity','classification','slice'][self.mode.currentIndex()],slice_z=self.slice_z.value(),opacity=self.opacity.value())
        return result

    def refresh_table(self):
        self._updating=True;self.table.setRowCount(len(self.pending['sections']))
        for i,s in enumerate(self.pending['sections']):
            metadata=s.get('provenance',{}).get('metadata',{});chi=metadata.get('chi2')
            fit=(f'{chi:.3g}'+(' · 需复核拟合' if chi>2 else '') if isinstance(chi,(int,float)) else '解析模拟/未记录')
            values=['',s['name'],'实测反演' if s['kind']=='imported_observations' else '模拟数据',f"{np.linalg.norm(np.subtract(*np.asarray(s['endpoints'])[::-1])):.2f}",str(len(s['cells'])),fit,
                s.get('provenance',{}).get('project') or s.get('provenance',{}).get('case') or '解析模拟，非实测']
            for j,value in enumerate(values):
                item=Q.QTableWidgetItem(value)
                if j==0:
                    item.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable|QtCore.Qt.ItemIsUserCheckable)
                    item.setCheckState(QtCore.Qt.Checked if s.get('enabled',True) else QtCore.Qt.Unchecked)
                elif j!=1:item.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable)
                item.setToolTip(value);self.table.setItem(i,j,item)
        self._updating=False

    def item_changed(self,item):
        if self._updating:return
        if item.column()==0:self.pending['sections'][item.row()]['enabled']=item.checkState()==QtCore.Qt.Checked
        if item.column()==1:self.pending['sections'][item.row()]['name']=item.text().strip() or f'测线{item.row()+1}'

    def add_snapshot(self,snapshot):
        if len(self.pending['sections'])>=30:raise ValueError('区域最多30条剖面。')
        self.pending['sections'].append(snapshot)
        self.reset_bounds();self.refresh_table()
        self.status.setText('剖面已加入待应用列表；生成或应用后进入项目。取消可放弃未应用修改。')

    def capture(self):
        try:self.add_snapshot(capture_current(self.owner,f'测线 {len(self.pending["sections"])+1:02}'))
        except (OSError,ValueError,KeyError,TypeError) as error:self.status.setText(str(error))

    def import_project(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'导入已完成配准和反演的测线项目','','GeoScope (*.geoscope.json *.json)')
        if not path:return
        try:self.add_snapshot(section_from_project(path,self.owner.terrain))
        except (OSError,ValueError,KeyError,TypeError) as error:self.status.setText(str(error))

    def remove_selected(self):
        index=self.table.currentRow()
        if index<0:self.status.setText('请先选择剖面。');return
        self.pending['sections'].pop(index);self.refresh_table()

    def reset_bounds(self):
        self.pending['settings']=default_settings(self.pending['sections'])
        if hasattr(self,'bounds'):self.fill_controls()

    def open_selected(self):
        index=self.table.currentRow()
        if index<0:return
        workspace=self.pending['sections'][index].get('workspace')
        if not workspace:self.status.setText('此解析模拟快照没有仪器观测工作台。');return
        try:self.owner.new_region_workbench(workspace)
        except Exception as error:self.status.setText(str(error))

    def new_line(self):
        try:self.owner.new_region_workbench()
        except Exception as error:self.status.setText(str(error))

    def demo(self):
        try:self.owner.load_region_demo()
        except Exception as error:self.status.setText(str(error))

    def generate(self):
        try:
            result=self.owner.generate_region_model(self.collect());self.pending=copy.deepcopy(self.owner.region_state)
            self.status.setText(f"区域体已应用：{len(result['rho'])}单元；关闭窗口查看。后续取消仅放弃尚未应用的修改。")
        except (ValueError,KeyError,TypeError,RuntimeError) as error:self.status.setText(str(error))

    def export(self):
        if self.collect()!=self.owner.region_state:self.status.setText('存在未应用修改，请先生成或应用后再导出。');return
        try:self.owner.export_region_model()
        except (OSError,ValueError) as error:self.status.setText(str(error))

    def accept(self):
        try:self.owner.apply_region_state(self.collect())
        except (ValueError,KeyError,TypeError,RuntimeError) as error:self.status.setText(str(error));return
        super().accept()
