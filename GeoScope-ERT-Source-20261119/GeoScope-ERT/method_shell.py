"""Project-owned method workbenches; windows own presentation, never the data."""
import copy
import json
import sys
import weakref
from pathlib import Path
from uuid import uuid4
from PySide6 import QtCore, QtWidgets as Q
import project_tasks as _project_tasks

ROOT=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
METHODS={'ert':'电法'}
PROJECTS=weakref.WeakValueDictionary()


class MethodProject(QtCore.QObject):
    changed=QtCore.Signal()

    def __init__(self,factory=None):
        super().__init__()
        self.id=uuid4().hex
        PROJECTS[self.id]=self
        self.factory=factory or self._make_workbench
        self.modules={};self.hosts=[]
        self.terrain=None;self.scene=None;self.path=None
        self.dirty=False;self._loading=False;self._shutting_down=False
        self._change_serial=0
        self.runtime=ROOT/'runs'/('project-'+self.id)
        self.path_remap={}

    def _make_workbench(self,method,project):
        if method=='ert':
            from method_ert import ERTWorkbench
            return ERTWorkbench(project)
        raise ValueError('此版本仅包含电法模块。')

    def mark_dirty(self,*args):
        if self._loading:return
        self._change_serial+=1
        self.dirty=True;self.changed.emit()
        for host in self.hosts:host.refresh_title()

    def new_host(self,position=None):
        from method_tabs import MethodHost
        host=MethodHost(self);self.hosts.append(host)
        if position is not None:host.move(position)
        return host

    def host_for(self,method):
        return next((h for h in self.hosts if method in h.method_keys()),None)

    def open_module(self,method,host=None):
        if method not in METHODS:raise ValueError('尚未安装此方法模块。')
        owner=self.host_for(method)
        if owner is not None:
            owner.tabs.setCurrentWidget(self.modules[method]);owner.show();owner.raise_()
            return self.modules[method]
        host=host or (self.hosts[0] if self.hosts else self.new_host())
        if host.project is not self:raise ValueError('禁止跨项目添加模块。')
        widget=self.modules.get(method)
        if widget is None:
            widget=self.factory(method,self);widget.module_project=self;widget.method_key=method
            self.modules[method]=widget
            if self.terrain is not None:widget.set_terrain(self.terrain,self.scene)
        host.add_workbench(method,widget);self.mark_dirty()
        return widget

    def move_module(self,method,target,index=None):
        if target.project is not self:raise ValueError('标签只能在同一项目的窗口间移动。')
        source=self.host_for(method)
        if source is None:raise ValueError('该模块当前没有打开。')
        if source is target:return
        widget=self.modules[method]
        source.remove_workbench(method)
        target.add_workbench(method,widget,index)
        target.show();target.raise_()
        if source.tabs.count()==0:source.close_empty()
        self.mark_dirty()

    def detach_module(self,method,position=None):
        target=self.new_host(position)
        self.move_module(method,target)
        return target

    def set_terrain(self,path):
        from core import read_terrain
        from campus_scene import read_scene
        if any(self.is_busy(w) for w in self.modules.values()):
            raise ValueError('请先停止或完成各模块计算，再更换共享地表。')
        terrain=read_terrain(path);scene=read_scene(path)
        previous=(self.terrain,self.scene)
        snapshots={w:copy.deepcopy(w.export_state()) for w in self.modules.values()}
        dirties={w:getattr(w,'dirty',False) for w in self.modules.values()}
        loading=self._loading;self._loading=True
        try:
            for widget in self.modules.values():
                widget.set_terrain(terrain,scene)
        except Exception:
            for widget,state in snapshots.items():
                try:widget.set_terrain(*previous)
                except Exception:
                    widget.terrain=previous[0]
                    if hasattr(widget,'campus_scene'):widget.campus_scene=previous[1]
                widget.import_state(state);widget.dirty=dirties[widget]
            raise
        finally:self._loading=loading
        self.terrain,self.scene=terrain,scene
        self.mark_dirty()
        for host in self.hosts:host.refresh_title()

    @staticmethod
    def is_busy(widget):
        callback=getattr(widget,'busy',None)
        return bool(callback()) if callback else getattr(widget,'process',None) is not None

    def export_document(self):
        windows=[]
        for host in self.hosts:
            if getattr(host,'_will_close',False):continue
            geometry=host.normalGeometry() if host.isMaximized() else host.geometry()
            windows.append(dict(tabs=host.method_keys(),active=host.current_method(),
                geometry=[geometry.x(),geometry.y(),geometry.width(),geometry.height()],
                maximized=host.isMaximized()))
        return dict(schema=2,terrain=self.terrain.source if self.terrain else None,
                    modules={k:w.export_state() for k,w in self.modules.items()},windows=windows)

    def save(self,path=None,parent=None):
        for widget in self.modules.values():
            check=getattr(widget,'require_committed_edit',None)
            if check and not check():return False
        if path is None:path=self.path
        if path is None:
            path,_=Q.QFileDialog.getSaveFileName(parent,'保存完整项目包','','GeoScope 项目包 (*.geoscope)')
            if not path:return False
        path=Path(path)
        if path.suffix.lower()!='.geoscope':path=Path(str(path)+'.geoscope')
        requested=path
        # GUI snapshot: only plain JSON values and path strings cross the thread.
        document=self.export_document()
        snapshot_serial=self._change_serial
        listed={}
        for method,widget in self.modules.items():
            listing=getattr(widget,'asset_paths',None)
            if listing:listed[method]=dict(listing())
        def write(progress):
            from project_bundle import write_bundle
            target=requested.resolve()
            required_fields={'source_file','source_path','source','case','result_dir','result_path','file_path'}
            def validate_resources(value,field=None,required=False):
                if isinstance(value,dict):
                    # Source entries contain path plus name/hash/mapping metadata.
                    # Only path fields inherit a required resource check.
                    for key,item in value.items():
                        source_role = field=='source_files' and key in ('protocol', 'electrodes')
                        validate_resources(item,key,source_role or ((required or field=='source_files') and (key=='path' or key in required_fields)))
                elif isinstance(value,list):
                    for item in value:validate_resources(item,field,required or field=='source_files')
                elif isinstance(value,str) and value and (field in required_fields or required) and '://' not in value:
                    candidate=Path(value)
                    if (required or candidate.is_absolute()) and not candidate.exists():
                        raise ValueError('完整项目包缺少原始文件或结果资源：'+value)
            progress(0,'正在校验项目资源…')
            validate_resources(document)
            assets={};origins={}
            def collect(value):
                if isinstance(value,dict):return {k:collect(v) for k,v in value.items()}
                if isinstance(value,list):return [collect(v) for v in value]
                if isinstance(value,str) and value and '://' not in value:
                    try:
                        candidate=Path(value)
                        if candidate.is_absolute() and candidate.exists():
                            candidate=candidate.resolve()
                            # Do not capture the running application or an old project archive.
                            if candidate==target or candidate.suffix=='.geoscope':return value
                            key=next((k for k,p in assets.items() if p==candidate),None)
                            if key is None:
                                key='file'+str(len(assets));assets[key]=candidate;origins[key]=str(candidate)
                                if candidate.is_file():
                                    for ending in ('.scene.json','.ert.json'):
                                        sidecar=Path(str(candidate)+ending)
                                        if sidecar.exists():
                                            sk='sidecar'+str(len(assets));assets[sk]=sidecar;origins[sk]=str(sidecar)
                            return 'asset:'+key
                    except (OSError,ValueError):pass
                return value
            progress(5,'正在收集项目资源…')
            packed=collect(document)
            for sources in listed.values():
                for source in sources.values():
                    if not Path(source).exists():raise ValueError('完整项目包缺少资源：'+str(source))
                    collect(str(Path(source).resolve()))
            packed['asset_origins']=origins
            def bundle_progress(fraction,text=''):
                progress(10+int(max(0.0,min(1.0,float(fraction)))*88),text)
            progress(10,'正在写入项目包…')
            write_bundle(target,packed,assets,progress=bundle_progress)
            progress(99,'正在完成保存…')
            return target
        target=_project_tasks.run_project_task(write,parent,'正在保存项目包…')
        # Only after the background archive replacement succeeded.
        self.path=target
        if self._change_serial==snapshot_serial:
            self.dirty=False
            for widget in self.modules.values():
                callback=getattr(widget,'mark_saved',None)
                if callback:callback()
                else:widget.dirty=False
        else:
            # QProcess completion is delivered by the waiting event loop. Its
            # new result is newer than the archive snapshot and still needs save.
            self.dirty=True
        self.changed.emit()
        for host in self.hosts:host.refresh_title()
        return True

    def relocate_paths(self,value):
        if isinstance(value,dict):return {k:self.relocate_paths(v) for k,v in value.items()}
        if isinstance(value,list):return [self.relocate_paths(v) for v in value]
        if isinstance(value,str):
            for old,new in sorted(self.path_remap.items(),key=lambda x:len(x[0]),reverse=True):
                if value==old:return new
                if value.startswith(old+'\\') or value.startswith(old+'/'):
                    return str(Path(new)/value[len(old)+1:])
        return value

    def _restore_project_document(self,doc,assets,tick=None):
        """Restore a document on the GUI thread; ``tick`` may pump the modal UI."""
        def step(value,text=''):
            if tick is not None:tick(value,text)
        if assets is not None:
            original_origins=dict(doc.get('asset_origins',{}))
            self.path_remap={old:str(assets[key]) for key,old in original_origins.items()}
            def resolve(v):
                if isinstance(v,dict):return {k:resolve(x) for k,x in v.items()}
                if isinstance(v,list):return [resolve(x) for x in v]
                if isinstance(v,str) and v.startswith('asset:'):
                    key=v[6:]
                    if key not in assets:raise ValueError('项目缺少资源：'+key)
                    return str(assets[key])
                return v
            doc=self.relocate_paths(resolve(doc))
            # Sidecars are packaged separately; recreate next to their DEM.
            for key,old in original_origins.items():
                if old.endswith(('.scene.json','.ert.json')):
                    import shutil
                    for suffix in ('.scene.json','.ert.json'):
                        if old.endswith(suffix):
                            base=old[:-len(suffix)]
                            if base in self.path_remap:
                                shutil.copy2(assets[key],str(self.path_remap[base])+suffix)
        step(66,'正在恢复共享地表…')
        if doc.get('terrain'):self.set_terrain(doc['terrain'])
        modules=doc.get('modules',{})
        for index,(method,state) in enumerate(modules.items()):
            if method not in METHODS:raise ValueError('缺少模块：'+method)
            widget=self.factory(method,self);widget.module_project=self;widget.method_key=method
            self.modules[method]=widget
            if self.terrain is not None:widget.set_terrain(self.terrain,self.scene)
            widget.import_state(state)
            step(70+int(20*(index+1)/max(1,len(modules))),'正在恢复方法模块…')
        seen=set()
        layouts=doc.get('windows',[])
        for index,layout in enumerate(layouts):
            host=self.new_host()
            for method in layout.get('tabs',[]):
                if method in seen or method not in self.modules:raise ValueError('项目标签布局无效。')
                seen.add(method);host.add_workbench(method,self.modules[method])
            active=layout.get('active')
            if active in self.modules and active in host.method_keys():host.tabs.setCurrentWidget(self.modules[active])
            geometry=layout.get('geometry',[100,70,1420,930])
            if len(geometry)==4:
                x,y,w,h=map(int,geometry)
                if not 0<w<=32768 or not 0<h<=32768:raise ValueError('保存的窗口尺寸无效。')
                host.setGeometry(x,y,max(620,w),max(480,h));host.ensure_on_screen()
            if layout.get('maximized'):host.setWindowState(QtCore.Qt.WindowMaximized)
            step(90+int(8*(index+1)/max(1,len(layouts))),'正在恢复窗口布局…')
        if not self.hosts:self.new_host()
        return self

    @classmethod
    def load(cls,path,factory=None):
        import project_bundle
        project=cls(factory=factory);project._loading=True
        try:
            if Path(path).suffix.lower()=='.json':
                legacy=json.loads(Path(path).read_text(encoding='utf-8'))
                if legacy.get('schema')!=1:raise ValueError('不支持的旧项目版本。')
                doc=dict(schema=2,terrain=legacy.get('terrain'),modules={'ert':legacy},
                         windows=[dict(tabs=['ert'],active='ert',geometry=[100,70,1420,930])])
                project._restore_project_document(doc,None)
            else:
                destination=project.runtime/'unpacked'
                def read(progress):
                    progress(0,'正在校验项目包…')
                    def bundle_progress(fraction,text=''):
                        progress(5+int(max(0.0,min(1.0,float(fraction)))*55),text)
                    document,assets=project_bundle.read_bundle(
                        path,destination,progress=bundle_progress)
                    if document.get('schema')!=2:raise ValueError('不支持的项目包版本。')
                    return document,assets
                def build(value,tick):
                    document,assets=value
                    tick(65,'正在恢复项目内容…')
                    project._restore_project_document(document,assets,tick)
                    return project
                _project_tasks.run_project_task(
                    read,Q.QApplication.activeWindow(),'正在打开项目包…',gui_finish=build)
            project.path=Path(path).resolve() if Path(path).suffix.lower()!='.json' else None
            project._loading=False;project.dirty=False
            for widget in project.modules.values():widget.dirty=False
            return project
        except Exception:
            project.shutdown();raise

    def close_modules(self,methods,decisions=None,save=True,parent=None):
        decisions=dict(decisions or {})
        for method in methods:
            widget=self.modules[method]
            if self.is_busy(widget):
                answer=Q.QMessageBox.question(parent,'计算进行中',f'{METHODS[method]}正在计算。停止计算并继续关闭？',Q.QMessageBox.Yes|Q.QMessageBox.Cancel)
                if answer!=Q.QMessageBox.Yes:return False
                widget.stop_calculation()
                if self.is_busy(widget):return False
            if method not in decisions:
                box=Q.QMessageBox(parent);box.setWindowTitle('关闭'+METHODS[method]);box.setText('保存后保留模块数据；不保存则从项目移除该模块及其数据。')
                keep=box.addButton('保存并关闭',Q.QMessageBox.AcceptRole)
                remove=box.addButton('不保存并移除',Q.QMessageBox.DestructiveRole)
                box.addButton('取消',Q.QMessageBox.RejectRole);box.exec()
                decisions[method]='keep' if box.clickedButton() is keep else 'remove' if box.clickedButton() is remove else 'cancel'
            if decisions[method]=='cancel':return False
        target=self.path
        need_save=save and any(d=='keep' for d in decisions.values())
        if need_save and target is None:
            target,_=Q.QFileDialog.getSaveFileName(parent,'保存完整项目包','','GeoScope 项目包 (*.geoscope)')
            if not target:return False
        backups={m:(self.modules[m],self.host_for(m)) for m in methods}
        for method,(widget,host) in backups.items():
            if host:host.remove_workbench(method)
            widget.hide();widget.setParent(None)
            if decisions[method]=='remove':self.modules.pop(method)
        try:
            if need_save and not self.save(target,parent):raise ValueError('项目未保存。')
        except Exception:
            for method,(widget,host) in backups.items():
                self.modules[method]=widget
                if host:host.add_workbench(method,widget)
            raise
        for method,(widget,host) in backups.items():
            if decisions[method]=='remove':widget.shutdown();widget.deleteLater()
        if not need_save:self.mark_dirty()
        return True

    def show_all(self):
        for host in self.hosts:host.show()

    def tile(self):
        visible=[h for h in self.hosts if h.isVisible()]
        if not visible:return
        area=visible[0].screen().availableGeometry();width=area.width()//len(visible)
        for i,host in enumerate(visible):
            host.showNormal();host.setGeometry(area.x()+i*width,area.y(),width,area.height())
            host.fit_compact(width)
        self.mark_dirty()

    def shutdown(self):
        self._shutting_down=True
        for method,widget in list(self.modules.items()):
            host=self.host_for(method)
            if host is not None:host.remove_workbench(method)
            widget.shutdown();widget.deleteLater()
        self.modules.clear()
        for host in list(self.hosts):host.close_empty()
        self.hosts.clear();PROJECTS.pop(self.id,None)
