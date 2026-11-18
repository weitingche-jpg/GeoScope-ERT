"""Reviewable, atomic station import with explicit coordinate confirmation."""
import copy
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Q
from point_files import read_tables,infer_header,convert_rows


class PointImportDialog(Q.QDialog):
    def __init__(self,owner,path,target_points=None,target_label='',new_line=False):
        super().__init__(owner)
        self.owner=owner;self.path=str(Path(path).resolve());self.tables=read_tables(path)
        if not self.tables:raise ValueError('文件中没有数据表。')
        self.records=[];self.excluded=set();self.imported_points=[]
        self.target_points=list(owner.points if target_points is None else target_points)
        self.target_label=str(target_label or '');self.new_line=bool(new_line)
        self.setWindowTitle('新增测线并导入测点 · 预览与检查' if self.new_line else '智能导入测点 · 预览与检查')
        self.resize(1080,740)
        layout=Q.QVBoxLayout(self)
        target=(f'目标：{self.target_label}\n' if self.target_label else '')
        label=Q.QLabel(f'{target}{Path(path).name}\n本地规则识别表头与字段；请核实 X/Y 方向、单位及坐标系。非大模型服务。')
        label.setWordWrap(True);layout.addWidget(label)
        form=Q.QHBoxLayout();layout.addLayout(form)
        self.source=Q.QComboBox();self.source.addItems([name for name,_ in self.tables]);form.addWidget(self.source)
        form.addWidget(Q.QLabel('表头行'))
        self.header=Q.QSpinBox();self.header.setRange(0,10000);self.header.setSpecialValueText('无表头（首行为数据）');form.addWidget(self.header)
        self.units=Q.QComboBox();self.units.addItems(['米 → 米','厘米 → 米','毫米 → 米']);form.addWidget(self.units)
        self.mode=Q.QComboBox();self.mode.addItems(['追加测点','按编号更新 / 新增','替换当前测线测点']);form.addWidget(self.mode)
        mapping=Q.QHBoxLayout();layout.addLayout(mapping);self.columns={}
        for field,title in [('id','编号'),('x','X / 东向'),('y','Y / 北向'),('z','Z / 高程')]:
            mapping.addWidget(Q.QLabel(title));combo=Q.QComboBox();mapping.addWidget(combo);self.columns[field]=combo
            combo.currentIndexChanged.connect(self.refresh)
        split=Q.QSplitter();layout.addWidget(split,1)
        left=Q.QWidget();ll=Q.QVBoxLayout(left);ll.addWidget(Q.QLabel('原始表格（前500行）'))
        self.raw=Q.QTableWidget();self.raw.setEditTriggers(Q.QAbstractItemView.NoEditTriggers);ll.addWidget(self.raw);split.addWidget(left)
        right=Q.QWidget();rl=Q.QVBoxLayout(right);rl.addWidget(Q.QLabel('转换预览：取消勾选可排除错误行；修改原文件后重新导入'))
        self.preview=Q.QTableWidget();self.preview.setColumnCount(7)
        self.preview.setHorizontalHeaderLabels(['导入','源行','编号','X / m','Y / m','Z / m','检查结果'])
        self.preview.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.preview.itemChanged.connect(self.exclusion_changed);rl.addWidget(self.preview);split.addWidget(right)
        rl.addWidget(Q.QLabel('平面位置预览：模型范围与待导入点（最多显示1000点）'))
        self.map_scene=Q.QGraphicsScene(self)
        self.map_view=Q.QGraphicsView(self.map_scene);self.map_view.setMinimumHeight(150);self.map_view.setMaximumHeight(210)
        rl.addWidget(self.map_view)
        self.summary=Q.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.confirm_coordinates=Q.QCheckBox('我已确认：文件 X/Y 含义与当前模型一致，单位换算正确，使用相同坐标系（此处不执行坐标配准或投影转换）')
        layout.addWidget(self.confirm_coordinates)
        self.connect_line=Q.QCheckBox('按导入顺序另建测线（默认保留当前测线；导入后整组测点转为独立布点）');layout.addWidget(self.connect_line)
        if self.new_line:
            self.mode.setEnabled(False)
            self.connect_line.setText('按导入顺序建立新测线（新线只使用导入点，不混入默认24点）')
            self.connect_line.setChecked(True);self.connect_line.setEnabled(False)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel)
        self.ok=buttons.button(Q.QDialogButtonBox.Ok);self.ok.setText('确认导入')
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消');layout.addWidget(buttons)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        self.source.currentIndexChanged.connect(self.select_source)
        self.header.valueChanged.connect(self.detect_mapping)
        self.units.currentIndexChanged.connect(self.refresh);self.mode.currentIndexChanged.connect(self.refresh)
        self.confirm_coordinates.toggled.connect(self.refresh)
        self.source.setCurrentIndex(max(range(len(self.tables)),key=lambda i:len(infer_header(self.tables[i][1])[1])))
        self.select_source()

    def select_source(self,*_):
        rows=self.tables[self.source.currentIndex()][1];self.excluded=set()
        detected,mapping=infer_header(rows)
        self.header.blockSignals(True);self.header.setValue(detected+1 if {'x','y'}<=mapping.keys() else 0);self.header.blockSignals(False)
        self.raw.clear();self.raw.setRowCount(min(len(rows),500))
        self.raw.setColumnCount(max((len(r) for r in rows),default=0))
        for r,row in enumerate(rows[:500]):
            for c,value in enumerate(row):self.raw.setItem(r,c,Q.QTableWidgetItem(str(value or '')))
        self.detect_mapping()

    def detect_mapping(self,*_):
        self.excluded=set();rows=self.tables[self.source.currentIndex()][1];index=self.header.value()-1
        headers=rows[index] if self.header.value()>0 and index<len(rows) else []
        _,mapping=infer_header([headers])
        width=max((len(r) for r in rows),default=0)
        for field,combo in self.columns.items():
            combo.blockSignals(True);combo.clear();combo.addItem('不使用',-1)
            for col in range(width):combo.addItem(f'{col+1}: {headers[col] if col<len(headers) else "无表头"}',col)
            combo.setCurrentIndex(mapping.get(field,-1)+1);combo.blockSignals(False)
        self.refresh()

    def refresh(self,*_):
        if not hasattr(self,'preview'):return
        rows=self.tables[self.source.currentIndex()][1][self.header.value():]
        mapping={field:c.currentData() for field,c in self.columns.items() if c.currentData() is not None and c.currentData()>=0}
        duplicate_columns=len(set(mapping.values()))!=len(mapping)
        scale=[1,.01,.001][self.units.currentIndex()]
        height=lambda xy:float(self.owner.terrain.height(xy)[0])
        inside=lambda x,y:bool(np.isfinite(height([[x,y]])))
        self.records=convert_rows(rows,mapping,scale,height,inside,self.excluded)
        existing={p['id'] for p in self.target_points}
        for record in self.records:
            if not record['error'] and self.mode.currentIndex()==0 and record['point']['id'] in existing:
                record['error']='编号与当前项目重复；选择更新模式或修改编号'
            if duplicate_columns:record['error']='多个字段使用了同一列，请检查映射'
        self.preview.blockSignals(True);self.preview.setRowCount(len(self.records))
        valid=errors=0
        for row,record in enumerate(self.records):
            check=Q.QTableWidgetItem();check.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Unchecked if record['excluded'] else QtCore.Qt.Checked)
            self.preview.setItem(row,0,check)
            point=record['point'] or {}
            values=[str(record['index']+self.header.value()+1),point.get('id',''),
                point.get('x',''),point.get('y',''),point.get('z',''),record['error'] or '通过']
            for col,value in enumerate(values,1):
                item=Q.QTableWidgetItem(str(value));self.preview.setItem(row,col,item)
                if record['error']:item.setForeground(QtGui.QColor('#ed917f'))
            if not record['excluded']:
                if record['error']:errors+=1
                else:valid+=1
        self.preview.blockSignals(False)
        self.preview.resizeColumnsToContents()
        self.map_scene.clear()
        terrain=self.owner.terrain
        xmin,xmax=float(terrain.x.min()),float(terrain.x.max())
        ymin,ymax=float(terrain.y.min()),float(terrain.y.max())
        bounds=QtCore.QRectF(xmin,-ymax,xmax-xmin,ymax-ymin)
        self.map_scene.addRect(bounds,QtGui.QPen(QtGui.QColor('#6c8c9e')),QtGui.QBrush(QtGui.QColor('#243a30')))
        campus=self.owner.campus_scene
        if campus:
            for building in campus['buildings']:
                a,b,c,d,_,_=building['bounds']
                self.map_scene.addRect(a,-d,b-a,d-c,QtGui.QPen(QtGui.QColor('#829aaf')),QtGui.QBrush(QtGui.QColor('#536a7e')))
            for line in campus['roads']:
                a,b=line;self.map_scene.addLine(a[0],-a[1],b[0],-b[1],QtGui.QPen(QtGui.QColor('#6b7785'),10))
            track=campus['track'];cx,cy=track['center'];rx,ry=track['radii']
            self.map_scene.addEllipse(cx-rx,-cy-ry,rx*2,ry*2,QtGui.QPen(QtGui.QColor('#bd6b55'),8))
        radius=max(xmax-xmin,ymax-ymin)*.006
        for record in [r for r in self.records if not r['excluded'] and not r['error']][:1000]:
            p=record['point'];self.map_scene.addEllipse(p['x']-radius,-p['y']-radius,2*radius,2*radius,
                QtGui.QPen(QtGui.QColor('#ffe5a0')),QtGui.QBrush(QtGui.QColor('#ffbf50')))
        self.map_scene.setSceneRect(bounds);self.map_view.fitInView(bounds,QtCore.Qt.KeepAspectRatio)
        virtual='虚拟平面坐标' if self.owner.campus_scene else self.owner.terrain.crs
        self.summary.setText(f'当前模型：{virtual} · 选中有效 {valid} 条 / 错误 {errors} 条 / 排除 {len(self.excluded)} 条。空白 Z 自动贴地；公式无计算缓存请先在 Excel/WPS 中计算并保存。')
        self.ok.setEnabled(valid>0 and errors==0 and self.confirm_coordinates.isChecked())

    def exclusion_changed(self,item):
        if item.column()!=0:return
        index=self.records[item.row()]['index']
        if item.checkState()==QtCore.Qt.Unchecked:self.excluded.add(index)
        else:self.excluded.discard(index)
        self.refresh()

    def accept(self):
        self.refresh()
        if not self.ok.isEnabled():return
        points=[]
        for record in self.records:
            if record['excluded']:continue
            point=copy.deepcopy(record['point'])
            point['source_file']=self.path;point['source_table']=self.tables[self.source.currentIndex()][0]
            point['source_row']=record['index']+self.header.value()+1
            point['source_values']=record['raw']
            points.append(point)
        self.imported_points=points
        super().accept()
