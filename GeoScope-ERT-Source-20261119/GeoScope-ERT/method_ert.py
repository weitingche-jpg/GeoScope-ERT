"""Embed the existing ERT workbench without changing its standalone checks."""
import copy
import json
import shutil
import sys
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from app import Window,ROOT,DATA
from core import write_json
from region_project import empty_region
from survey_project import empty_survey,validate_survey


class EmbeddedManager:
    def __init__(self,project):self.project=project;self.windows=[]
    def register(self,widget):
        widget.window_number=1;self.windows.append(widget)
    def new_window(self):
        # Legacy region actions expect an ERT workbench, but a second electrical
        # module in this project would violate unique method tabs. Open a new
        # project and return its ERT page instead.
        from method_shell import MethodProject
        project=MethodProject();host=project.new_host();widget=project.open_module('ert',host)
        host.show();return widget
    def tile(self):self.project.tile()
    def remove(self,widget):
        if widget in self.windows:self.windows.remove(widget)


class ERTWorkbench(Window):
    allow_unlocated_processing=True
    def setParent(self,parent,*flags):
        from qt_lifecycle import native_widget_transition
        with native_widget_transition():
            return super().setParent(parent,*flags)

    def __init__(self,project):
        self.module_project=project;self._applying_shared_terrain=False
        super().__init__(manager=EmbeddedManager(project),demo=False)
        self.setWindowFlags(QtCore.Qt.Widget)
        self.menuBar().hide()
        self.dirty=False
        # The shared base window owns these fields. Keep a safe fallback for
        # standalone use while the survey integration is being rolled out.
        if not hasattr(self,'survey_state'):self.survey_state=empty_survey()
        if not hasattr(self,'survey_queue'):self.survey_queue=[]
        if not hasattr(self,'volume_arrays'):self.volume_arrays=None
        if not hasattr(self,'volume_metadata'):self.volume_metadata=None
        if not hasattr(self,'_survey_batch_running'):self._survey_batch_running=False
        self.sidebar_scroll.setFixedWidth(290)

    def close(self):
        return self.module_project.close_modules(['ert'],parent=self.window())

    def busy(self):
        checker=getattr(self,'survey_busy',None)
        if callable(checker):return bool(checker())
        return (self.process is not None or bool(getattr(self,'survey_queue',[]))
                or bool(getattr(self,'_survey_batch_running',False)))

    def stop_calculation(self):
        had_work=(self.process is not None or bool(getattr(self,'survey_queue',[]))
                  or bool(getattr(self,'_survey_batch_running',False)))
        # Drop pending work before killing the process so a completion signal
        # cannot enqueue the next section.
        self.survey_queue=[];self._survey_batch_running=False
        process=self.process
        if process is not None:
            process.blockSignals(True);process.kill();process.waitForFinished(5000)
            self.process=None;process.deleteLater()
        if not had_work:return
        self.refresh_workflow();self.update_history_actions()
        refresh=getattr(self,'refresh_survey_ui',None)
        if callable(refresh):refresh()
        self.say('电法计算已停止；保留此前已完成结果。' if process is not None else
                 '已取消排队中的测线计算；保留此前已完成结果。')

    def shutdown(self):
        self.stop_calculation()
        if self.line_drag_active() or self.drag_endpoint is not None or self.mode=='line':
            self.cancel_current_action()
        Q.QApplication.instance().removeEventFilter(self.shortcut_controller)
        self.plotter.close()

    def record_edit(self,before,label):
        super().record_edit(before,label);self.module_project.mark_dirty()

    def load_hypothetical_survey(self,paths=None):
        before=getattr(self,'survey_state',None)
        result=super().load_hypothetical_survey(paths)
        if getattr(self,'survey_state',None) is not before:self.module_project.mark_dirty()
        return result

    def survey_volume_done(self,code,status):
        old=getattr(self,'survey_state',None)
        old_case=old.get('volume_case') if isinstance(old,dict) else None
        result=super().survey_volume_done(code,status)
        new=getattr(self,'survey_state',None)
        new_case=new.get('volume_case') if isinstance(new,dict) else None
        if code==0 and new_case and new_case!=old_case:self.module_project.mark_dirty()
        return result

    def change_survey_view(self,index):
        state=getattr(self,'survey_state',None)
        before=state.get('view') if isinstance(state,dict) else None
        result=super().change_survey_view(index)
        state=getattr(self,'survey_state',None)
        after=state.get('view') if isinstance(state,dict) else None
        if after!=before:self.module_project.mark_dirty()
        return result

    def set_terrain(self,terrain,scene=None):
        first=self.terrain is None
        old=self.terrain
        previous_source=old.source if old is not None else None
        changed=(terrain is not None and old is not None and
                 (previous_source!=terrain.source or not self._terrain_equal(old,terrain)))
        if changed:
            sync=getattr(self,'sync_survey_line',None)
            if callable(sync):sync()
        self.terrain=terrain;self.campus_scene=scene
        if terrain is not None:
            if changed:
                # Preserve coordinates/results in their original reference. Do not
                # silently drape or translate an existing acquisition.
                if self.ert_packet:
                    binding=self.ert_packet.get('binding')
                    if not isinstance(binding,dict) or binding.get('mode')!='local':
                        self.ert_packet['binding']={'mode':'local'}
                if self.result_binding:self.result_binding={'mode':'local'}
                self.region_state=empty_region();self.region_cache=None
                self._placement_key=None
                self._rebind_survey_terrain(terrain,previous_source)
                self.say('共享地表已更换；原测点坐标保持，旧结果仅局部展示，请重新核对定位。')
            self.title.setText(scene['label'] if scene else Path(terrain.source).name)
            self.info.setText(f'{terrain.crs} · XY 单位 m\n共享项目地表；电法视角与其他方法独立。')
        else:
            self.info.setText('尚未加载项目地表；可先导入观测、质检与局部二维反演。')
        self.redraw(reset=first);self.refresh_workflow()
        if changed:
            refresh=getattr(self,'refresh_survey_ui',None)
            if callable(refresh):refresh()

    @staticmethod
    def _terrain_equal(first,second):
        """Compare a shared surface by CRS and raster contents, including NaNs."""
        return (first.crs==second.crs
                and np.array_equal(first.x,second.x,equal_nan=True)
                and np.array_equal(first.y,second.y,equal_nan=True)
                and np.array_equal(first.z,second.z,equal_nan=True))

    def _rebind_survey_terrain(self,terrain,previous_source):
        """Keep survey edits/cases, but make every line local to the new terrain."""
        state=getattr(self,'survey_state',None)
        if not isinstance(state,dict):return
        for line in state.get('lines') or []:
            ws=line.get('workspace')
            if not isinstance(ws,dict):continue
            ws['terrain']=terrain.source
            packet=ws.get('ert_packet')
            if isinstance(packet,dict):
                binding=packet.get('binding')
                if not isinstance(binding,dict) or binding.get('mode')!='local':
                    packet['binding']={'mode':'local'}
            if ws.get('result_binding') is not None:ws['result_binding']={'mode':'local'}
        # A prior volume cannot be trusted after a terrain change even when the
        # survey coordinates happen to hash the same. Keep the files, not the view.
        state['volume_case']=None;state['view']='sections'
        if previous_source:state['terrain_provenance']={'previous_source':previous_source}
        self.volume_arrays=None;self.volume_metadata=None

    def load_terrain(self,path):self.module_project.set_terrain(path)

    def import_terrain(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'更换整个项目的共享地表','','地形 (*.tif *.tiff *.dem)')
        if path:
            try:self.module_project.set_terrain(path)
            except Exception as error:self.error(error)

    def confirm_replace(self):return self.require_committed_edit()

    def save_project(self):return self.module_project.save(parent=self.window())
    def save_project_as(self):
        path,_=Q.QFileDialog.getSaveFileName(self,'另存完整项目包','','GeoScope (*.geoscope)')
        return self.module_project.save(path,parent=self.window()) if path else False
    def save_project_to(self,path):return self.module_project.save(path,parent=self.window())
    def open_project(self):self.window().open_project()

    def set_workflow_step(self,index):
        if self.terrain is not None:return super().set_workflow_step(index)
        if not hasattr(self,'data_tabs') or not self.require_committed_edit():return
        self.workflow_step=index;self.step_stack.setCurrentIndex(index)
        self.data_tabs.setVisible(index>0)
        if index==1:self.data_tabs.setCurrentIndex(1)
        if index==2:self.data_tabs.setCurrentIndex(3 if self.case_arrays else 2)
        self.refresh_workflow()

    def workflow_checks(self):
        if self.terrain is not None:return super().workflow_checks()
        from observed_ert import validate_packet
        issues=[dict(level='提示',text='未加载地表：当前只进行局部二维处理，不在三维地表定位。',tab=2,row=None)]
        try:
            if self.ert_packet:
                validate_packet(self.ert_packet)
                if self.ert_packet.get('binding',{}).get('mode')!='local':raise ValueError('未加载地表，请切换为仅局部反演。')
            else:raise ValueError('请先导入观测数据。')
        except (ValueError,KeyError,TypeError) as error:issues.append(dict(level='错误',text=str(error),tab=1,row=None))
        return issues

    def redraw(self,reset=False):
        if self.terrain is not None:return super().redraw(reset)
        if not hasattr(self,'plotter'):return
        self.plotter.clear()
        self._redraw_unlocated()

    def run_observed_inversion(self):
        if self.terrain is not None:return super().run_observed_inversion()
        if self.process is not None:return
        from observed_ert import validate_packet
        try:
            self.commit_workflow_parameters();packet=copy.deepcopy(validate_packet(self.ert_packet))
            if packet.get('binding',{}).get('mode')!='local':raise ValueError('没有地表时仅支持局部二维反演。')
            station=np.arange(packet['electrodes'])*packet['spacing']
            output=self.module_project.runtime/('ert-'+__import__('uuid').uuid4().hex[:10])
            request=dict(mode='observed',station=station.tolist(),xy=np.column_stack([station,np.zeros(len(station))]).tolist(),
                elevation=np.zeros(len(station)).tolist(),crs='LOCAL_METRE',terrain_source='',
                line_vertices=[[0.,0.],[float(station[-1]),0.]],output=str(output),observations=packet,geometry_signature=None)
            write_json(output/'request.json',request);self.current_request=output
            self.process=QtCore.QProcess(self);self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.worker_log);self.process.finished.connect(self.worker_done)
            self.process.errorOccurred.connect(self.worker_error)
            args=['--worker',str(output/'request.json')] if getattr(sys,'frozen',False) else [str(ROOT/'app.py'),'--worker',str(output/'request.json')]
            self.process.start(sys.executable,args);self.set_workflow_step(2);self.refresh_workflow()
            self.say('局部二维反演已启动；没有创建或替代项目地表。')
        except Exception as error:self.error(error)

    def worker_done(self,*args):
        super().worker_done(*args)
        if self.process is None:self.module_project.mark_dirty()

    def load_case(self,path):
        path=Path(path)
        if self.terrain is None:
            meta=json.loads((path/'metadata.json').read_text(encoding='utf-8'))
            if meta.get('data_kind')!='imported_observations':raise ValueError('没有共享地表时只允许加载局部二维观测结果。')
            self.load_workflow_case(path,meta)
            return
        return super().load_case(path)

    def export_state(self):
        sync=getattr(self,'sync_survey_line',None)
        if callable(sync):sync()
        names=('points','line','line_endpoint_overrides','ert_packet','simulation_enabled','points_follow_line','workflow_step','result_binding','region_state')
        data={}
        for name in names:
            if name=='line_endpoint_overrides':
                data[name]=copy.deepcopy(getattr(self,name,{}) or {})
            else:
                data[name]=copy.deepcopy(getattr(self,name))
        data.update(case=str(self.case_path) if self.case_path else None,opacity=self.opacity.value(),
                    camera=[list(v) for v in self.plotter.camera_position],data_tab=self.data_tabs.currentIndex())
        survey=getattr(self,'survey_state',None)
        if not isinstance(survey,dict):survey=empty_survey()
        if self.terrain is None:survey=validate_survey(survey,None)
        data['survey_state']=copy.deepcopy(survey)
        # MethodProject validates keys it knows about; volume_case is survey-only,
        # so check both here before the portable collector sees them.
        self._validate_survey_assets(data['survey_state'])
        return data

    def _validate_survey_assets(self,state):
        for line in state.get('lines') or []:
            case=(line.get('workspace') or {}).get('case')
            if case:self._validate_case(Path(case),volume=False)
        volume=state.get('volume_case')
        if volume:self._validate_case(Path(volume),volume=True)

    def _validate_case(self,path,volume=False):
        if not path.is_dir():raise ValueError('案例目录不存在：'+str(path))
        metadata_file=path/'metadata.json'
        if not metadata_file.is_file():raise ValueError('案例缺少 metadata.json：'+str(path))
        if volume and not (path/'volume.npz').is_file():raise ValueError('三维结果缺少 volume.npz：'+str(path))
        if not volume and not (path/'model.npz').is_file():raise ValueError('二维结果缺少 model.npz：'+str(path))
        try:metadata=json.loads(metadata_file.read_text(encoding='utf-8'))
        except (OSError,ValueError) as error:raise ValueError('案例元数据无法读取：'+str(metadata_file)) from error
        if not isinstance(metadata,dict):raise ValueError('案例元数据格式无效：'+str(metadata_file))
        if volume:return
        for key in ('model','model_file','model_path'):
            value=metadata.get(key)
            if not isinstance(value,str) or not value or '://' in value:continue
            candidate=Path(value)
            if not candidate.is_absolute():candidate=path/candidate
            if candidate.suffix and not candidate.exists():raise ValueError('案例缺少模型文件：'+str(candidate))

    @staticmethod
    def _rebase_case_refs(value,source,target):
        """Point self-references inside a copied case at the runtime copy."""
        # Resolve once per case, rather than issuing a filesystem query for
        # every string (including IDs and notes) in nested observation data.
        roots={str(source),str(source.resolve())}
        prefixes=tuple(root+separator for root in roots for separator in ('\\','/'))
        def rebase(item):
            if isinstance(item,dict):return {k:rebase(v) for k,v in item.items()}
            if isinstance(item,list):return [rebase(v) for v in item]
            if isinstance(item,str) and item:
                if item in roots:return str(target)
                for prefix in prefixes:
                    if item.startswith(prefix):return str(target/item[len(prefix):])
            return item
        return rebase(value)

    def _prepare_case(self,case,target,volume=False,names=('metadata.json','request.json','observations.json')):
        from project_tasks import run_project_step
        # Snapshot the plain path mapping before crossing the thread boundary.
        remap=dict(getattr(self.module_project,'path_remap',{}))
        parent=self.window() if isinstance(self,Q.QWidget) else None
        return run_project_step(
            lambda progress:ERTWorkbench._prepare_case_files(case,target,remap,volume,names),
            parent=parent,title='正在恢复案例文件…')

    @staticmethod
    def _prepare_case_files(case,target,remap,volume=False,names=('metadata.json','request.json','observations.json')):
        """Copy and rebind files only; never access a workbench in this worker."""
        source=Path(case);target=Path(target)
        if not source.is_dir():raise ValueError('案例目录不存在：'+str(source))
        ERTWorkbench._validate_case(None,source,volume=volume)
        target.parent.mkdir(parents=True,exist_ok=True)
        if source.resolve()!=target.resolve():
            if target.exists():target=target.with_name(target.name+'-'+__import__('uuid').uuid4().hex[:10])
            shutil.copytree(source,target)
        mappings=sorted(remap.items(),key=lambda pair:len(pair[0]),reverse=True)
        def relocate(value):
            if isinstance(value,dict):return {k:relocate(v) for k,v in value.items()}
            if isinstance(value,list):return [relocate(v) for v in value]
            if isinstance(value,str):
                for old,new in mappings:
                    if value==old:return new
                    if value.startswith(old+'\\') or value.startswith(old+'/'):
                        return str(Path(new)/value[len(old)+1:])
            return value
        # Rebase only the runtime copy; archived originals stay byte-for-byte.
        for name in names:
            file=target/name
            if not file.is_file():continue
            try:value=json.loads(file.read_text(encoding='utf-8'))
            except (OSError,ValueError) as error:raise ValueError('案例元数据无法读取：'+str(file)) from error
            value=relocate(value)
            write_json(file,ERTWorkbench._rebase_case_refs(value,source,target))
        ERTWorkbench._validate_case(None,target,volume=volume)
        return target

    def import_state(self,state):
        from observed_ert import validate_packet
        raw=state.get('survey_state')
        if raw is None:raw=state.get('survey')
        if raw is None:
            survey=empty_survey()
        elif not isinstance(raw,dict):
            raise ValueError('测网状态格式无效。')
        elif not raw:
            survey=empty_survey()
        elif self.terrain is None:
            # validate_survey allows only explicitly marked local imported
            # lines without a terrain; arbitrary unlocated global surveys stay
            # rejected with the same message.
            survey=validate_survey(raw,None)
        else:
            survey=validate_survey(raw,self.terrain)
        active_id=survey.get('active_id')
        active_line=next((line for line in survey.get('lines') or [] if line.get('id')==active_id),None)

        packet_source=state.get('ert_packet')
        if packet_source is None and active_line is not None:
            packet_source=active_line.get('workspace',{}).get('ert_packet')
        packet=copy.deepcopy(packet_source)
        if packet is not None:validate_packet(packet,allow_empty=True)
        points_source=state.get('points')
        if points_source is None and active_line is not None:
            points_source=active_line.get('workspace',{}).get('points',[])
        line_source=state.get('line')
        if line_source is None and active_line is not None:
            line_source=active_line.get('workspace',{}).get('line',[])
        points=copy.deepcopy(points_source or []);line=copy.deepcopy(line_source or [])
        saved_overrides=state.get('line_endpoint_overrides')
        if saved_overrides is None and active_line is not None:
            saved_overrides=(active_line.get('workspace') or {}).get('line_endpoint_overrides')
        if not isinstance(saved_overrides,dict):saved_overrides={}
        saved_overrides=copy.deepcopy(saved_overrides)
        if 'points_follow_line' in state:follow=state.get('points_follow_line')
        elif active_line is not None:follow=active_line.get('workspace',{}).get('points_follow_line',False)
        else:follow=False
        if 'simulation_enabled' in state:simulation=state.get('simulation_enabled')
        elif active_line is not None:simulation=active_line.get('workspace',{}).get('simulation_enabled',False)
        else:simulation=False
        saved_region=copy.deepcopy(state.get('region_state',state.get('region',empty_region())))

        # Copy/rebind the active case first. All later line-case copies are
        # archived assets and must never mutate their original metadata.
        active_case=state.get('case')
        if not active_case and active_line is not None:
            active_case=active_line.get('workspace',{}).get('case')
        active_target=None
        if active_case:
            active_target=self._prepare_case(active_case,self.module_project.runtime/'ert-case')
            if active_line is not None:active_line['workspace']['case']=str(active_target)
        for index,line_state in enumerate(survey.get('lines') or [],1):
            if active_line is not None and line_state.get('id')==active_line.get('id'):continue
            ws=line_state.get('workspace') or {}
            case=ws.get('case')
            if not case:continue
            target=self._prepare_case(case,self.module_project.runtime/f'survey-{index}')
            ws['case']=str(target)

        volume_source=survey.get('volume_case');volume_target=None
        if volume_source:
            volume_target=self._prepare_case(volume_source,self.module_project.runtime/'survey-volume',
                volume=True,names=('metadata.json','request.json'))
            survey['volume_case']=str(volume_target)
        else:
            survey['volume_case']=None
        if volume_target is None and survey.get('view')=='volume':survey['view']='sections'

        # Basic active edit/model restoration.
        self.ert_packet=copy.deepcopy(packet)
        self.points=copy.deepcopy(points);self.line=copy.deepcopy(line)
        self.points_follow_line=follow;self.simulation_enabled=simulation
        self.region_state=saved_region;self.region_cache=None
        if active_target is not None:
            self.load_case(active_target)
            # Keep the owning workspace on the runtime copy even if the loader
            # normalized the path differently.
            self.case_path=Path(active_target)
        else:
            self.case_path=None
        # A historic result may restore its own acquisition geometry. Put the
        # user's current edits back afterwards; they are independent of it.
        self.ert_packet=copy.deepcopy(packet)
        self.points=copy.deepcopy(points);self.line=copy.deepcopy(line)
        self.line_endpoint_overrides=copy.deepcopy(saved_overrides)
        self.points_follow_line=follow;self.simulation_enabled=simulation
        self.region_state=copy.deepcopy(saved_region);self.region_cache=None
        self._placement_key=None
        self.result_binding=copy.deepcopy(state.get('result_binding',self.result_binding))
        self.survey_state=survey
        self.survey_queue=[];self._survey_batch_running=False
        if volume_target is not None:
            # Rebased metadata/request JSON is loaded only after the active
            # model, points and packet are restored. Signature is unchanged.
            self.load_survey_volume(volume_target)
        else:
            self.volume_arrays=None;self.volume_metadata=None
        self.opacity.setValue(state.get('opacity',58));self.redraw(reset=True)
        if state.get('camera'):self.plotter.camera_position=state['camera']
        self.set_workflow_step(state.get('workflow_step',0))
        self.data_tabs.setCurrentIndex(state.get('data_tab',0))
        refresh=getattr(self,'refresh_survey_ui',None)
        if callable(refresh):refresh()
        dock=getattr(self,'survey_dock',None)
        if dock is not None:dock.setVisible(bool(survey.get('lines')))
        self.mark_saved()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'sidebar_scroll'):
            self.sidebar_scroll.setFixedWidth(245 if self.width()<1100 else 290)
