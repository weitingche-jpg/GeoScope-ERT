"""Scale-one planar registration with explicit flat-ground compatibility."""
import copy
import math
import numpy as np
from PySide6 import QtWidgets as Q
from workflow_geometry import electrode_ids,fit_endpoints,generate_bound_points,validate_binding,physical_signature
from line_import import is_local_import


class RegistrationDialog(Q.QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.packet=copy.deepcopy(owner.ert_packet)
        self.local_sources=is_local_import(self.packet) or any(p.get('local_frame') for p in owner.points)
        self.binding=None;self.points=[];self.line=[]
        self.setWindowTitle('关联当前地形 · 米制刚性配准');self.resize(720,450)
        layout=Q.QVBoxLayout(self)
        info=Q.QLabel('比例保持1∶1，只平移、水平旋转和整体高程偏移。当前观测采用平地假设；各电极必须与目标地面在设定容差内一致。起伏地形不满足时请保留局部反演，补充高程后再扩展带地形计算。')
        info.setWordWrap(True);layout.addWidget(info)
        if self.local_sources:
            info.setText(info.text()+'\n当前测点来自文件局部坐标，不能当作地形位置；请填写已知的地形坐标端点。')
        self.method=Q.QComboBox();self.method.addItems(['按两个端点定位并生成电极','关联当前表中同编号电极'])
        layout.addWidget(self.method)
        length=(self.packet['electrodes']-1)*self.packet['spacing']
        terrain=owner.terrain;start=[float(terrain.x.mean()),float(terrain.y.mean()),0.]
        start[2]=float(terrain.height([start[:2]])[0]);end=[start[0]+length,start[1],start[2]]
        if len(owner.line)==2:
            start[:2]=owner.line[0];end[:2]=owner.line[-1]
            start[2]=float(terrain.height([start[:2]])[0]);end[2]=float(terrain.height([end[:2]])[0])
        grid=Q.QGridLayout();layout.addLayout(grid);self.fields=[]
        for col,label in enumerate(['对应位置','X / m','Y / m','Z / m']):grid.addWidget(Q.QLabel(label),0,col)
        ids=electrode_ids(self.packet)
        for row,(label,values) in enumerate([(ids[0],start),(ids[-1],end)],1):
            grid.addWidget(Q.QLabel(label),row,0);edits=[]
            for col,value in enumerate(values,1):
                edit=Q.QLineEdit('' if self.local_sources else str(value))
                if self.local_sources:edit.setPlaceholderText('填写已知地形坐标')
                grid.addWidget(edit,row,col);edits.append(edit)
            self.fields.append(edits)
        self.tolerance=Q.QDoubleSpinBox();self.tolerance.setDecimals(3);self.tolerance.setRange(.001,10);self.tolerance.setValue(.1);self.tolerance.setSuffix(' m')
        form=Q.QFormLayout();form.addRow('校核容差（根据数据精度设置）',self.tolerance);layout.addLayout(form)
        preview=Q.QPushButton('预览定位和误差');preview.clicked.connect(self.calculate);layout.addWidget(preview)
        self.status=Q.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.confirm=Q.QCheckBox('确认坐标单位为米并核对端点顺序；应用将关联/更新当前测点表（可撤销）')
        layout.addWidget(self.confirm)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel);layout.addWidget(buttons)
        buttons.button(Q.QDialogButtonBox.Ok).setText('应用配准')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)

    def calculate(self):
        try:
            tolerance=self.tolerance.value()
            if self.method.currentIndex()==0:
                start,end=[[float(e.text()) for e in edits] for edits in self.fields]
                binding=fit_endpoints(self.packet,start,end,tolerance)
                points,line=generate_bound_points(self.packet,binding)
            else:
                if self.local_sources:
                    raise ValueError('当前同编号测点是局部源电极，不是地形测点；请填写已知地形坐标的两个端点。')
                ids=electrode_ids(self.packet);by_id={p['id']:p for p in self.owner.points}
                if len(by_id)!=len(self.owner.points) or set(by_id)!=set(ids):raise ValueError('当前测点表须恰好包含全部观测电极编号；先在测点表修正编号，或选择两端定位生成。')
                points=[copy.deepcopy(by_id[name]) for name in ids]
                start,end=[[p[k] for k in ('x','y','z')] for p in (points[0],points[-1])]
                binding=fit_endpoints(self.packet,start,end,tolerance)
                line=[[points[0]['x'],points[0]['y']],[points[-1]['x'],points[-1]['y']]]
            binding['geometry_signature']=physical_signature(points,line)
            binding['terrain_source']=self.owner.terrain.source
            problems=validate_binding(self.packet,binding,points,line,self.owner.terrain)
            if problems:raise ValueError('\n'.join(problems))
            predicted,_=generate_bound_points(self.packet,binding)
            residual=max(np.linalg.norm(np.array([p['x'],p['y']])-np.array([q['x'],q['y']])) for p,q in zip(points,predicted))
            self.binding,self.points,self.line=binding,points,line
            self.status.setText(f"长度保持 {binding['length']:.6g} m · 端点长度差 {binding['endpoint_residual']:.4g} m · 最大平面校核差 {residual:.4g} m。比例1∶1；通过当前地形兼容检查。没有独立实测校核点时，不视为测绘精度证明。")
            xyz=np.array([[p['x'],p['y'],p['z']+1] for p in points])-self.owner.terrain.origin
            self.owner.plotter.add_points(xyz,color='#73d5ff',point_size=9,name='registration-preview',pickable=False,reset_camera=False)
            return True
        except Exception as error:
            self.binding=None;self.status.setText(str(error));self.clear_preview();return False

    def clear_preview(self):
        self.owner.plotter.remove_actor('registration-preview',reset_camera=False)

    def accept(self):
        if not self.confirm.isChecked():self.status.setText('请先核对单位、端点顺序并确认应用。');return
        if self.calculate():self.clear_preview();super().accept()

    def reject(self):
        self.clear_preview();super().reject()
