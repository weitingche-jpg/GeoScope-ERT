"""Multi-section collection, derived regional layers and independent workbenches."""
import copy
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import pyvista as pv
from PySide6 import QtWidgets as Q
from core import write_json
from shortcuts import edit_snapshot
from region_model import build_region,region_signature,default_settings
from region_project import empty_region,validate_region_state


class RegionWorkflow:
    def open_region_dialog(self):
        if self.terrain is None:self.say('请先导入地形。');return
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or not self.require_committed_edit():
            self.say('请结束当前编辑或等待计算完成后管理区域。');return
        dialog=getattr(self,'_region_dialog',None)
        try:
            if dialog is not None and dialog.isVisible():
                dialog.show();dialog.raise_();dialog.activateWindow();return
            if dialog is not None:dialog.close()
        except RuntimeError:
            dialog=None
        from region_dialog import RegionDialog
        dialog=RegionDialog(self)
        dialog.setModal(False)
        self._region_dialog=dialog
        dialog.destroyed.connect(lambda *_,d=dialog:(setattr(self,'_region_dialog',None) if getattr(self,'_region_dialog',None) is d else None))
        dialog.show();dialog.raise_();dialog.activateWindow()

    def apply_region_state(self,state):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):raise ValueError('计算或测线批次中不能修改区域。')
        state=validate_region_state(state,self.terrain)
        before=edit_snapshot(self);self.region_state=state
        self.record_edit(before,'修改多测线区域');self.redraw()

    def generate_region_model(self,state):
        state=validate_region_state(state,self.terrain)
        result=build_region(state['sections'],state['settings'],self.terrain.height)
        state['generated_signature']=result['signature']
        self.region_cache=result;self.apply_region_state(state)
        self.say('区域插值体已生成；灰色网格为缺数据，剖面邻近带仅为显示分类，不是三维反演。')
        return result

    def new_region_workbench(self,workspace=None):
        """A fresh window preserves all unsaved edits in the current document."""
        child=self.manager.new_window()
        try:
            if workspace:
                import app
                document=copy.deepcopy(workspace);document.pop('region',None)
                path=app.ROOT/'.cache'/('region-line-'+uuid4().hex+'.geoscope.json')
                write_json(path,document);child.load_project_from(path)
                child.project_path=None;child.dirty=True
            else:child.load_terrain(self.terrain.source)
            child.say('独立测线工作台：完成配准和反演后保存项目，再从区域窗口导入；原区域项目保持。')
            return child
        except Exception:
            child.dirty=False;child.close();raise

    def load_region_demo(self):
        child=self.manager.new_window()
        try:
            child.load_campus();state=empty_region()
            xs=np.linspace(220,266,24);zs=np.linspace(-14,0,15)
            for index,y in enumerate((100.,110.,120.)):
                xx,zz=np.meshgrid(xs,zs);nodes=np.column_stack([xx.ravel(),np.full(xx.size,y),zz.ravel()])
                cells=[]
                for j in range(len(zs)-1):
                    for i in range(len(xs)-1):
                        a=j*len(xs)+i;cells.extend([[a,a+1,a+len(xs)+1],[a,a+len(xs)+1,a+len(xs)]])
                cells=np.asarray(cells);centers=nodes[cells].mean(axis=1)
                anomaly=np.exp(-((centers[:,0]-244)/8)**2-((centers[:,2]+6)/4)**2-((y-110)/12)**2)
                rho=150*np.exp(-1.6*anomaly)
                state['sections'].append(dict(id=f'demo-{index+1}',name=f'模拟测线 {index+1}',kind='synthetic',enabled=True,
                    crs=child.terrain.crs,terrain_source=child.terrain.source,endpoints=[[220,y],[266,y]],
                    nodes=nodes.tolist(),cells=cells.tolist(),rho=rho.tolist(),
                    provenance=dict(description='解析模拟电阻率剖面；不是仪器观测或三维反演，没有拟合指标。')))
            state['settings']=default_settings(state['sections']);state['display']['opacity']=.72
            child.generate_region_model(state);child.set_workflow_step(2);child.redraw(reset=True)
            child.opacity.setValue(22);child.focus_region_view()
            child.setWindowTitle('GeoScope · 多测线区域模拟演示 · 非实测')
            return child
        except Exception:
            child.dirty=False;child.close();raise

    def region_grid(self):
        result=self.region_cache
        grid=pv.ImageData(dimensions=tuple(result['shape']+1),spacing=tuple(result['spacing']),
            origin=tuple(result['origin']-self.terrain.origin))
        grid.cell_data['resistivity']=result['rho'];grid.cell_data['data_range']=result['classification']
        return grid

    def focus_region_view(self):
        sections=self.region_state['sections']
        if not sections:return
        nodes=np.concatenate([np.asarray(s['nodes']) for s in sections])-self.terrain.origin
        low=nodes.min(axis=0)-2;high=nodes.max(axis=0)+2
        self.plotter.view_isometric();self.plotter.reset_camera(bounds=tuple(np.column_stack([low,high]).ravel()))
        self.plotter.render()

    def draw_region_layers(self):
        self.region_actors=[];self.region_meshes=[];self.region_pick_entries=[]
        state=self.region_state;sections=state['sections'];display=state['display']
        if not sections:
            self.region_status.setText('区域建模：收录至少两条不同位置的平行剖面。');return
        signature=region_signature(sections,state['settings'])
        generated=state.get('generated_signature')==signature
        if generated and (self.region_cache is None or self.region_cache['signature']!=signature):
            try:self.region_cache=build_region(sections,state['settings'],self.terrain.height)
            except (ValueError,KeyError,TypeError,RuntimeError) as error:
                generated=False;self.say('区域体无法重建：'+str(error))
        enabled=[s for s in sections if s.get('enabled',True)]
        values=np.concatenate([np.asarray(s['rho']) for s in enabled]) if enabled else np.array([20,500])
        if self.model_actor is not None:
            values=np.r_[values,np.asarray(self.model.cell_data['resistivity'])]
        clim=[float(values.min()),float(values.max())]
        if clim[0]==clim[1]:clim=[clim[0]*.8,clim[1]*1.2]
        if self.model_actor is not None:
            self.model_actor.mapper.scalar_range=clim
            self.model_actor.mapper.lookup_table.scalar_range=clim
        legend_added=self.scalar_legend.bar is not None
        def draw(mesh,**kwargs):
            nonlocal legend_added
            self.region_meshes.append(mesh)
            scalar=kwargs.get('scalars')=='resistivity'
            show_bar=scalar and not legend_added
            actor=self.plotter.add_mesh(mesh,pickable=True,reset_camera=False,show_scalar_bar=show_bar,
                scalar_bar_args=dict(title='',vertical=False,position_x=.14,position_y=.05,height=.08,width=.72,
                    color='#dce8f7',label_font_size=9,fmt='%.3g'),**kwargs)
            self.region_actors.append(actor)
            if kwargs.get('style')!='wireframe':self.region_pick_entries.append((actor,mesh))
            if show_bar:self.scalar_legend.attach(self.plotter.scalar_bars['']);legend_added=True
        if display['sections']:
            for index,section in enumerate(enabled):
                nodes=np.asarray(section['nodes'])-self.terrain.origin
                faces=np.column_stack([np.full(len(section['cells']),3),section['cells']]).ravel()
                mesh=pv.PolyData(nodes,faces);mesh.cell_data['resistivity']=section['rho']
                draw(mesh,scalars='resistivity',cmap='turbo_r',log_scale=True,clim=clim,opacity=.95)
                a=np.asarray(section['endpoints'][0]);top=np.max(np.asarray(section['nodes'])[:,2])
                self.plotter.add_point_labels([np.r_[a,top+1]-self.terrain.origin],[f"R{index+1:02} / {'FIELD' if section['kind']=='imported_observations' else 'SIM'}"],
                    text_color='#d3f5e0',font_size=10,shape_color='#14251f',point_size=0,always_visible=True,pickable=False)
        if generated:
            result=self.region_cache;grid=self.region_grid();classes=result['classification'];mode=display['mode']
            valid=classes>0
            if mode=='slice':
                level=float(display['slice_z']);valid&=np.abs(result['centers'][:,2]-level)<=result['spacing'][2]/2+1e-8
            if display['volume'] and valid.any():
                mesh=grid.extract_cells(np.flatnonzero(valid)).extract_surface()
                if mode=='classification':draw(mesh,scalars='data_range',cmap=['#53606e','#58bb91','#569ef4'],clim=[0,2],opacity=display['opacity'])
                else:draw(mesh,scalars='resistivity',cmap='turbo_r',log_scale=True,clim=clim,opacity=display['opacity'])
            missing=classes==0
            if mode=='slice':missing&=np.abs(result['centers'][:,2]-float(display['slice_z']))<=result['spacing'][2]/2+1e-8
            if display['missing'] and missing.any():
                draw(grid.extract_cells(np.flatnonzero(missing)).extract_surface(),color='#738294',opacity=.22,style='wireframe',line_width=.5)
            self.plotter.add_mesh(grid.outline(),color='#708196',line_width=1,pickable=False)
            stats=result['stats'];total=len(classes)
            self.region_status.setText(f"区域插值（非三维反演）｜{len(enabled)}条剖面 · 网格{total}｜"
                f"剖面邻近 {stats['near_section']/total:.0%} · 线间插值 {stats['interpolated']/total:.0%} · 缺数据 {stats['missing']/total:.0%}（灰网格）")
        else:self.region_status.setText(f'区域已收录{len(sections)}条独立剖面快照；尚未生成或参数已改变，请生成区域体。')
        if not self.region_state['sections'][0]['kind']=='imported_observations':
            self.region_status.setText('模拟数据｜'+self.region_status.text())

    def export_region_model(self):
        state=self.region_state
        signature=region_signature(state['sections'],state['settings'])
        if not self.region_cache or state.get('generated_signature')!=signature or self.region_cache['signature']!=signature:
            raise ValueError('请先按当前设置生成区域模型。')
        path,_=Q.QFileDialog.getSaveFileName(self,'导出区域插值模型','','NumPy model (*.npz)')
        if not path:return
        result=self.region_cache
        metadata=dict(method='parallel-section log resistivity interpolation; not 3D inversion',
            classification={'0':'missing; rho NaN','1':'near-section display band; not measured 3D cells','2':'between-section interpolation'},
            crs=self.terrain.crs,terrain_source=self.terrain.source,settings=state['settings'],signature=result['signature'],
            sections=[{k:s.get(k) for k in ('id','name','kind','provenance')} for s in state['sections']])
        np.savez_compressed(path,origin=result['origin'],spacing=result['spacing'],shape=result['shape'],
            centers=result['centers'],resistivity=result['rho'],classification=result['classification'],
            metadata_json=json.dumps(metadata,ensure_ascii=False,allow_nan=False))
        self.say('区域模型已导出，包含绝对坐标、缺数据掩膜和插值方法说明。')
