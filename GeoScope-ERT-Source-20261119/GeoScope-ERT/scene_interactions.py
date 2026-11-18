"""Qt mouse routing and one shared selection entry for both scene views."""
import numpy as np
from PySide6 import QtCore
from vtkmodules.vtkRenderingCore import vtkCellPicker
from core import stations_along
from interaction_state import cell_properties
from shortcuts import edit_snapshot, restore_edit
from endpoint_edit import relayout_map_electrodes
from workflow_geometry import is_map_line_binding


class SceneInteractions:
    def _screen(self, xyz):
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(*xyz, 1.)
        renderer.WorldToDisplay()
        x, y, depth = renderer.GetDisplayPoint()
        width, height = self.plotter.render_window.GetSize()
        return np.array([x * self.plotter.interactor.width()/width,
                         (height-y) * self.plotter.interactor.height()/height]), depth

    def _pick_actor(self, position, actor):
        if actor is None:
            return None
        width, height = self.plotter.render_window.GetSize()
        x = position.x() * width/self.plotter.interactor.width()
        y = height-position.y()*height/self.plotter.interactor.height()
        picker = vtkCellPicker()
        picker.SetTolerance(.002)
        picker.PickFromListOn()
        picker.AddPickList(actor)
        if picker.Pick(x, y, 0, self.plotter.renderer) and picker.GetCellId() >= 0:
            return picker.GetCellId(), np.asarray(picker.GetPickPosition())
        return None

    def eventFilter(self, watched, event):
        if watched is self.plotter.interactor and self.terrain is not None:
            kind = event.type()
            if kind == QtCore.QEvent.Leave:
                if hasattr(self,'_coordinate_pointer_left'):
                    self._coordinate_pointer_left()
            if kind == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
                self.cancel_current_action()
                return True
            if kind == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                if self.mode == 'line' or self.line_drag_active():
                    return False
                if (self.process is None and not (hasattr(self,'survey_busy') and self.survey_busy())
                        and len(self.line) >= 2):
                    endpoint = self.line_endpoint_at(event.position())
                    if endpoint is not None:
                        if self.drag_endpoint is not None:
                            # The first click of the double-click may have armed
                            # an E-mode drag; discard that draft, not the line.
                            try:self.plotter.interactor.releaseMouse()
                            except (AttributeError, RuntimeError):pass
                            if self.drag_snapshot is not None:
                                restore_edit(self,self.drag_snapshot)
                            self.drag_snapshot = None
                            self.drag_endpoint = None
                            self.press_position = None
                        self.open_endpoint_edit_dialog(endpoint)
                        return True
                return False
            if kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                if hasattr(self,'clear_coordinate_hover'):
                    self.clear_coordinate_hover()
                self.press_position = event.position()
                if self.mode == 'line':
                    if self.process is None and not (hasattr(self,'survey_busy') and self.survey_busy()):
                        start = self.line_start_at(event.position())
                        if start is not None:
                            self.begin_line_drag(start, event.position())
                        else:
                            self.say('拖动绘线：请从地形表面或已有测点上按下左键。')
                    else:
                        self.say('计算或测线批次进行中，暂不能拖动绘线。')
                    return True
                if self.mode == 'edit' and self.process is None and len(self.line) >= 2:
                    records = self.line_endpoint_records() if hasattr(self,'line_endpoint_records') else []
                    pairs = [(0, records[0]), (len(self.line)-1, records[1])] if len(records) >= 2 else []
                    for index, record in pairs:
                        xyz = self._drag_world(record, 3.)
                        screen, depth = self._screen(xyz)
                        if 0 <= depth <= 1 and np.linalg.norm(screen-[event.position().x(),event.position().y()]) < 20:
                            self.drag_snapshot = edit_snapshot(self)
                            self.drag_endpoint = index
                            self.plotter.interactor.grabMouse()
                            return True
            if kind == QtCore.QEvent.MouseMove:
                if self.line_drag_active():
                    self.update_line_drag(event.position())
                    return True
                if self.drag_endpoint is not None:
                    hit = self._pick_actor(event.position(), self.terrain_actor)
                    if hit is not None:
                        xy = (hit[1]+self.terrain.origin)[:2]
                        self.move_endpoint(self.drag_endpoint, xy)
                    return True
                if self.mode == 'line' and event.buttons() & QtCore.Qt.LeftButton:
                    return True
                if not event.buttons() and self.mode == 'browse':
                    if hasattr(self,'request_coordinate_hover'):
                        self.request_coordinate_hover(event.position())
                elif hasattr(self,'clear_coordinate_hover'):
                    self.clear_coordinate_hover()
            if kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                if self.line_drag_active():
                    self.finish_line_drag(event.position())
                    return True
                if self.drag_endpoint is not None:
                    index = self.drag_endpoint
                    before = self.drag_snapshot
                    on_map = is_map_line_binding(getattr(self, 'ert_packet', None))
                    if on_map:
                        hit = self._pick_actor(event.position(), self.terrain_actor)
                        if hit is None:
                            problem = '端点位置没有有效地形高程。'
                        else:
                            xy = (hit[1]+self.terrain.origin)[:2]
                            problem = None if self.move_endpoint(index, xy) else self._endpoint_drag_problem
                    self.drag_endpoint = None
                    self.drag_snapshot = None
                    self.press_position = None
                    self.plotter.interactor.releaseMouse()
                    if on_map and problem:
                        restore_edit(self, before)
                        self.redraw()
                        self.say(problem)
                        return True
                    self.record_edit(before,'移动测线端点')
                    if hasattr(self,'show_coordinate_selection'):
                        self.show_coordinate_selection('endpoint',0 if index==0 else 1)
                    if on_map:
                        self.say('测线端点已更新，电极已按文件间距重新布置。')
                    else:
                        self.say('测线端点已更新；旧反演结果保持原位。' if self.result_stale else '测线已恢复原位置。')
                    return True
                if self.mode == 'line':
                    self.press_position = None
                    return True
                if self.press_position is not None:
                    distance = (event.position()-self.press_position).manhattanLength()
                    if distance < 5:
                        if self.mode == 'point':
                            hit = self._pick_actor(event.position(), self.terrain_actor)
                            if hit is not None:
                                self.picked(hit[1])
                        elif self.mode == 'browse':
                            if hasattr(self,'clear_coordinate_hints'):
                                self.clear_coordinate_hints()
                            self.query_at(event.position())
                self.press_position = None
        return super().eventFilter(watched, event)

    def query_at(self, position):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，暂不查询对象。')
            return
        if hasattr(self,'survey_pick_at'):
            target=self.survey_pick_at(position)
            if target is not None:
                try:self.handle_survey_pick(*target)
                except (ValueError,OSError,KeyError,RuntimeError) as error:self.say(str(error))
                return
        if self.points and not (hasattr(self,'survey_active') and self.survey_active()):
            best = None
            for index, point in enumerate(self.points):
                xyz = np.array([point['x'],point['y'],point['z']+2])-self.terrain.origin
                screen, depth = self._screen(xyz)
                distance = np.linalg.norm(screen-[position.x(),position.y()])
                if 0 <= depth <= 1 and distance < 14 and (best is None or distance < best[0]):
                    best = (distance,index)
            if best is not None:
                self.select_point(best[1])
                return
        if hasattr(self,'line_endpoint_at'):
            endpoint=self.line_endpoint_at(position)
            if endpoint is not None:
                self.select_line_endpoint(endpoint)
                return
        volume_actor=getattr(self,'survey_volume_actor',None)
        if volume_actor is not None:
            hit=self._pick_actor(position,volume_actor)
            if hit is not None:
                mesh=getattr(self,'survey_volume_mesh',None)
                try:
                    world=hit[1]+self.terrain.origin
                    centers=mesh.cell_centers().points+self.terrain.origin
                    index=int(np.argmin(np.linalg.norm(centers-world,axis=1)))
                    value=float(mesh.cell_data['resistivity'][index])
                    self.clear_selection()
                    self.inspector.setText(f"实际三维求解单元 #{index} | 电阻率 {value:.3g} Ω·m | X {world[0]:.2f} · Y {world[1]:.2f} · Z {world[2]:.2f} m\n"
                        + ('假设测网下的实际三维求解，非现场结论；二维单元号不适用于三维体。' if getattr(self,'survey_state',{}).get('hypothesis') else '平行测线约束的三维反演体；二维单元号不适用于三维体。'))
                except Exception as error:
                    self.say('三维体查询失败：'+str(error))
                return
        hit = self._pick_actor(position, self.model_actor)
        line_hit = self._pick_actor(position, self.line_actor)
        if line_hit is not None:
            self.select_line()
            return
        if hit is not None:
            self.select_cell(hit[0])
            return
        for actor,mesh in reversed(getattr(self,'region_pick_entries',[])):
            region_hit=self._pick_actor(position,actor)
            if region_hit is None:continue
            index,location=region_hit;world=location+self.terrain.origin
            code=int(mesh.cell_data['data_range'][index]) if 'data_range' in mesh.cell_data else None
            label={0:'缺数据',1:'剖面邻近显示带',2:'线间插值'}.get(code,'独立二维剖面快照')
            value=float(mesh.cell_data['resistivity'][index]) if 'resistivity' in mesh.cell_data else float('nan')
            self.clear_selection()
            self.inspector.setText(f"区域查询｜{label}｜X {world[0]:.2f} · Y {world[1]:.2f} · Z {world[2]:.2f} m\n"+
                (f'电阻率 {value:.3g} Ω·m；区域插值不等于三维反演。' if np.isfinite(value) else '此处没有约束值，不进行外推。'))
            return
        hit = self._pick_actor(position, self.terrain_actor)
        if hit is not None:
            world = hit[1]+self.terrain.origin
            self.clear_selection()
            self.inspector.setText(f'地形位置  |  X {world[0]:.2f} m  ·  Y {world[1]:.2f} m  ·  高程 {world[2]:.2f} m')

    def clear_selection(self):
        if hasattr(self,'clear_coordinate_hints'):
            self.clear_coordinate_hints()
        self.plotter.remove_actor('selection', reset_camera=False, render=False)
        self.section_view.clear_highlight()
        self.table.clearSelection()
        self.selected = None
        self.update_history_actions()
        if hasattr(self,'refresh_survey_ui'):self.refresh_survey_ui()

    def cancel_current_action(self):
        if hasattr(self,'clear_coordinate_hints'):
            self.clear_coordinate_hints()
        if self.line_drag_active():
            self.cancel_line_drag()
            self.set_mode('browse')
            self.say('已取消拖动绘线，恢复拖动前的测线和测点。')
        elif self.drag_endpoint is not None:
            self.plotter.interactor.releaseMouse()
            if self.drag_snapshot is not None:
                restore_edit(self,self.drag_snapshot)
            self.drag_snapshot = None
            self.drag_endpoint = None
            self.press_position = None
            self.set_mode('browse')
            self.say('已取消拖动并恢复拖动前的测线和测点。')
        elif self.mode == 'line':
            if self.line_snapshot is not None:
                restore_edit(self,self.line_snapshot)
            self.line_snapshot = None
            self.press_position = None
            self.set_mode('browse')
            self.say('已取消拖动绘线，恢复原布局。')
        elif self.mode != 'browse':
            self.set_mode('browse')
        else:
            self.clear_selection()
            self.inspector.setText('已清除选区。点击对象可重新查询。')
            self.plotter.render()

    def focus_selected(self):
        if self.selected is None or self.terrain is None:
            self.say('请先选择一个测点或地下单元，再按 F 聚焦。')
            return
        kind,index=self.selected
        if self.model_local_only and kind in ('cell','result_point'):
            self.say('局部结果尚未配准，请在二维剖面中缩放；三维地形保持原位置。')
            return
        if kind=='cell':
            target=self.model.get_cell(index).points.mean(axis=0)
            size=np.ptp(self.model.get_cell(index).points,axis=0).max()*5
        elif kind=='point':
            point=self.points[index]
            target=np.array([point['x'],point['y'],point['z']])-self.terrain.origin
            size=0
        elif kind=='line':
            xy=np.asarray(self.line).mean(axis=0)
            target=np.r_[xy,self.terrain.height([xy])[0]]-self.terrain.origin
            size=stations_along(self.line,2)[0][-1]*1.2
        else:
            target=np.r_[self.section_data['xy'][index],self.section_data['elevation'][index]]-self.terrain.origin
            size=0
        size=max(size,np.ptp(self.terrain.x)*.06,20)
        camera=self.plotter.camera
        direction=np.asarray(camera.position)-np.asarray(camera.focal_point)
        direction/=np.linalg.norm(direction)
        distance=size/(2*np.tan(np.deg2rad(camera.view_angle/2)))*1.4
        camera.focal_point=target
        camera.position=target+direction*distance
        camera.parallel_scale=size*.7
        self.plotter.renderer.ResetCameraClippingRange()
        self.plotter.render()
        self.say('已聚焦选中对象；Home 恢复全景。')

    def select_cell(self, cell_id):
        if self.model is None:
            return
        if self.model_local_only:
            self.clear_selection()
            center=self.model.get_cell(cell_id).points.mean(axis=0)
            value=float(self.model.cell_data['resistivity'][cell_id])
            self.selected=('cell',int(cell_id));self.section_view.highlight_cell(cell_id);self.update_history_actions()
            self.inspector.setText(f'局部剖面单元 #{cell_id} | 电阻率 {value:.3g} Ω·m | 里程 {center[0]:.2f} m · 相对高程 {center[2]:.2f} m\n未配准，未在三维地形上高亮；平地假设。')
            return
        self.clear_selection()
        info = cell_properties(self.model, self.terrain, cell_id)
        self.selected = ('cell',int(cell_id))
        self.update_history_actions()
        self.plotter.add_mesh(self.model.extract_cells([cell_id]), color='#ffdc80',
            style='wireframe', line_width=5, lighting=False, pickable=False,
            name='selection', reset_camera=False)
        self.section_view.highlight_cell(cell_id)
        x,y,z = info['world']
        depth = f"{info['depth']:.2f} m" if np.isfinite(info['depth']) else '地形范围外'
        state = '旧结果 / 已过期' if self.result_stale else '当前结果'
        self.inspector.setText(f"地下单元 #{cell_id}  |  {info['resistivity']:.2f} Ω·m  |  地表以下 {depth}\n"
            f"X {x:.2f} · Y {y:.2f} · 高程 {z:.2f} m  |  {state} · {'导入观测（局部坐标）' if self.case_kind=='imported_observations' else '合成 ERT'} · {self.case_path.name}")

    def select_point(self, index):
        if not 0 <= index < len(self.points):
            return
        self.clear_selection()
        point = self.points[index]
        xyz = np.array([point['x'],point['y'],point['z']+2])-self.terrain.origin
        self.plotter.add_points(np.asarray([xyz]), color='#fff0a8', point_size=18,
            render_points_as_spheres=True, name='selection', pickable=False, reset_camera=False)
        self.selected = ('point',index)
        self.update_history_actions()
        self.table.selectRow(index)
        self.table.scrollToItem(self.table.item(index,0))
        if self.section_data is not None and not self.model_local_only:
            xy = self.section_data['xy']
            distances = np.linalg.norm(xy-[point['x'],point['y']], axis=1)
            closest = int(np.argmin(distances))
            if distances[closest] < .01 and abs(self.section_data['elevation'][closest]-point['z']) < .05:
                self.section_view.highlight_point(closest)
        policy = '自动布点随测线更新' if self.points_follow_line else '独立测点不随测线自动移动'
        self.inspector.setText(f"测点 {point['id']}  |  X {point['x']:.2f} m · Y {point['y']:.2f} m · 高程 {point['z']:.2f} m\n"
            f"高程来源：{point['elevation_source']}  |  {policy}")
        if hasattr(self,'show_coordinate_selection'):
            self.show_coordinate_selection('point',index)

    def select_result_point(self, index):
        if self.section_data is None:
            return
        self.clear_selection()
        xy = self.section_data['xy'][index]
        z = self.section_data['elevation'][index]
        xyz = np.r_[xy,z+2]-self.terrain.origin
        if not self.model_local_only:
            self.plotter.add_points(np.asarray([xyz]), color='#fff0a8', point_size=18,
                render_points_as_spheres=True, name='selection', pickable=False, reset_camera=False)
        self.section_view.highlight_point(index)
        self.selected = ('result_point',index)
        self.update_history_actions()
        name=f'E{index+1:02}'
        if self.ert_packet and not self.result_stale:
            from workflow_geometry import electrode_ids
            ids=electrode_ids(self.ert_packet)
            if index<len(ids):name=ids[index]
        self.inspector.setText(f"反演案例电极 {name}  |  X {xy[0]:.2f} · Y {xy[1]:.2f} · 高程 {z:.2f} m\n"
            + ('旧测线电极；当前布局已修改。' if self.result_stale else '参与当前反演的采集位置。'))

    def move_endpoint(self, index, world_xy):
        self._endpoint_drag_problem = '端点位置无效，请在有有效高程的地形上调整测线。'
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()) or len(self.line) < 2:
            return False
        if index not in (0,len(self.line)-1):
            return False
        xy = np.asarray(world_xy, dtype=float)
        if not np.isfinite(xy).all() or not np.isfinite(self.terrain.height([xy])).all():
            return False
        line = [list(p) for p in self.line]
        line[index] = xy.tolist()
        try:
            relayout = relayout_map_electrodes(self, line)
            _, sample = stations_along(line, 150)
        except ValueError as error:
            self._endpoint_drag_problem = str(error)
            self.statusBar().showMessage(self._endpoint_drag_problem)
            return False
        if not np.isfinite(self.terrain.height(sample)).all():
            return False
        self.line = line
        overrides=dict(getattr(self,'line_endpoint_overrides',{}) or {})
        overrides.pop(str(0 if index==0 else 1),None)
        self.line_endpoint_overrides=overrides
        if relayout:
            self.ert_packet, self.points = relayout
        elif self.points_follow_line:
            _, sample = stations_along(line, 24)
            z = self.terrain.height(sample)
            self.points = [dict(id=f'E{i+1:02}',x=float(p[0]),y=float(p[1]),z=float(h),
                elevation_source='地形插值（随测线）') for i,(p,h) in enumerate(zip(sample,z))]
        self.dirty = True
        self.redraw()
        length = stations_along(line,2)[0][-1]
        if relayout:
            spacing = f'{len(self.points)} 个电极 · 文件间距 {self.ert_packet["spacing"]:g} m'
        else:
            spacing = f'24 电极间距 {length/23:.2f} m' if self.points_follow_line else '独立测点坐标保持不变'
        self.statusBar().showMessage(f'测线长度 {length:.2f} m / {spacing}；松开鼠标完成。')
        if hasattr(self,'show_coordinate_selection'):
            self.show_coordinate_selection('endpoint',0 if index==0 else 1)
        return True
