"""Drag-to-draw survey line: live preview, endpoints and cancel/commit state.

The mixin deliberately keeps a draft separate from ``Window.line`` until the
mouse is released on valid ground.  Preview actors are updated by name so the
terrain actor and its picker stay alive for the whole gesture.
"""
import numpy as np
import pyvista as pv

from shortcuts import edit_snapshot, restore_edit
from endpoint_edit import effective_endpoint_override, normalize_endpoint, relayout_map_electrodes


PREVIEW_LINE = 'drag-line-preview'
PREVIEW_END = 'drag-line-end'
PREVIEW_LABELS = 'drag-line-labels'
COMMITTED_LABELS = 'line-endpoint-labels'

MIN_DRAG_PIXELS = 3.
MIN_LINE_LENGTH = 1e-6
SNAP_RADIUS = 14.
HISTORY_LABEL = '拖动绘制测线'


def _xy(position):
    """Accept QPoint/QPointF or a 2-item sequence."""
    try:
        return float(position.x()), float(position.y())
    except AttributeError:
        return float(position[0]), float(position[1])


def _record(point, point_id=None, source='terrain'):
    return dict(x=float(point['x']), y=float(point['y']), z=float(point['z']),
                id=point_id, source=source)


def _endpoint_label(name, record, length=None):
    suffix = f"  [{record['id']}]" if record.get('id') else ''
    text = (f"{name}{suffix}\n"
            f"X {record['x']:.2f} · Y {record['y']:.2f} · Z {record['z']:.2f} m")
    if length is not None:
        text += f"\n长度 {length:.2f} m"
    return text


class DragLineMixin:
    """Finite drag state shared by the real window and headless tests."""

    def init_drag_line(self):
        self._line_drag = False
        self._line_drag_start = None
        self._line_drag_end = None
        self._line_drag_snapshot = None
        self._line_drag_press = None
        self._line_drag_grabbed = False
        self._line_drag_pointer = None
        if not isinstance(getattr(self, 'line_endpoint_overrides', None), dict):
            self.line_endpoint_overrides = {}

    def line_drag_active(self):
        return bool(getattr(self, '_line_drag', False))

    # ------------------------------------------------------------------ start
    def snap_line_start(self, position, radius=SNAP_RADIUS):
        """Return an existing active-workspace point within ``radius`` pixels."""
        if getattr(self, 'terrain', None) is None:
            return None
        px, py = _xy(position)
        origin = np.asarray(self.terrain.origin, float)
        best = None
        for index, point in enumerate(getattr(self, 'points', []) or []):
            try:
                x, y, z = float(point['x']), float(point['y']), float(point['z'])
                if not np.isfinite([x, y, z]).all():
                    continue
                offset=0. if hasattr(self,'survey_active') and self.survey_active() else 2.
                screen, depth = self._screen(np.array([x, y, z + offset]) - origin)
            except (KeyError, TypeError, ValueError, RuntimeError):
                continue
            if not np.isfinite(screen).all() or not 0. <= float(depth) <= 1.:
                continue
            distance = float(np.hypot(screen[0] - px, screen[1] - py))
            if distance <= radius and (best is None or distance < best[0]):
                best = (distance, index, x, y, z, point.get('id'))
        if best is None:
            return None
        return _record(dict(x=best[2], y=best[3], z=best[4]), best[5], 'point')

    def terrain_line_hit(self, position):
        """Pick the terrain actor and return a snapped ground coordinate."""
        if getattr(self, 'terrain', None) is None or getattr(self, 'terrain_actor', None) is None:
            return None
        try:
            canvas=self.plotter.interactor
            if hasattr(canvas,'width') and hasattr(canvas,'height'):
                px,py=_xy(position)
                if not (0<=px<canvas.width() and 0<=py<canvas.height()):
                    return None
            hit = self._pick_actor(position, self.terrain_actor)
            if hit is None:
                return None
            world = np.asarray(hit[1], float) + np.asarray(self.terrain.origin, float)
            z = float(self.terrain.height([[world[0], world[1]]])[0])
        except (IndexError, TypeError, ValueError, RuntimeError):
            return None
        if not np.isfinite([world[0], world[1], z]).all():
            return None
        return _record(dict(x=world[0], y=world[1], z=z), None, 'terrain')

    def line_start_at(self, position, radius=SNAP_RADIUS):
        """Existing point first, otherwise the terrain surface."""
        return self.snap_line_start(position, radius) or self.terrain_line_hit(position)

    def _drag_world(self, record, dz=0.):
        origin = np.asarray(self.terrain.origin, float)
        return np.array([record['x'] - origin[0], record['y'] - origin[1],
                         record['z'] - origin[2] + dz], float)

    def begin_line_drag(self, start, press_position=None):
        if self.line_drag_active() or self.terrain is None or start is None:
            return False
        if getattr(self, 'process', None) is not None or (
                hasattr(self, 'survey_busy') and self.survey_busy()):
            if hasattr(self, 'say'):
                self.say('计算或测线批次进行中，暂不能拖动绘线。')
            return False
        values = [start['x'], start['y'], start['z']]
        if not np.isfinite(values).all():
            return False
        self._line_drag_snapshot = edit_snapshot(self)
        self._line_drag = True
        self._line_drag_start = _record(start, start.get('id'), start.get('source', 'terrain'))
        self._line_drag_end = None
        self._line_drag_press = press_position
        self._line_drag_pointer = press_position
        self._line_drag_grabbed = False
        try:
            self.plotter.interactor.grabMouse()
            self._line_drag_grabbed = True
        except (AttributeError, RuntimeError):
            self._line_drag_grabbed = False
        if hasattr(self, 'press_position'):
            self.press_position = press_position
        for name in (COMMITTED_LABELS,'line-endpoint-markers'):
            self.plotter.remove_actor(name,reset_camera=False,render=False)
        if hasattr(self, 'say'):
            self.say('拖动绘线：按住左键从起点拖至终点；松开完成，Esc 取消。')
        self._update_line_drag_preview()
        self._update_line_drag_text(valid=True)
        return True

    # ----------------------------------------------------------------- update
    def line_drag_length(self):
        start, end = getattr(self, '_line_drag_start', None), getattr(self, '_line_drag_end', None)
        if start is None or end is None:
            return 0.
        return float(np.hypot(end['x'] - start['x'], end['y'] - start['y']))

    def update_line_drag(self, position):
        if not self.line_drag_active():
            return False
        self._line_drag_pointer = position
        return self.set_line_drag_end(self.terrain_line_hit(position))

    def set_line_drag_end(self, end):
        if not self.line_drag_active():
            return False
        if end is None or not np.isfinite([end['x'], end['y'], end['z']]).all():
            self._line_drag_end = None
            self._update_line_drag_preview()
            self._update_line_drag_text(valid=False)
            return False
        self._line_drag_end = _record(end, end.get('id'), end.get('source', 'terrain'))
        self._update_line_drag_preview()
        self._update_line_drag_text(valid=True)
        return True

    def _update_line_drag_preview(self):
        if not self.line_drag_active():
            return
        start = self._line_drag_start
        end = self._line_drag_end
        if start is None:
            self._remove_line_drag_actors()
            return
        length = self.line_drag_length()
        start_world = self._drag_world(start, 2.)
        points = [start_world]
        labels = [_endpoint_label('起点 A', start)]
        if end is not None and length > MIN_LINE_LENGTH:
            end_world = self._drag_world(end, 2.)
            try:
                mesh = pv.lines_from_points(np.vstack([start_world, end_world]))
                self.plotter.add_mesh(mesh, color='#ffd166', line_width=5, pickable=False,
                                      reset_camera=False, render=False, name=PREVIEW_LINE)
                self.plotter.add_points(np.asarray([end_world]), color='#ffd166', point_size=18,
                                        render_points_as_spheres=True, pickable=False,
                                        reset_camera=False, render=False, name=PREVIEW_END)
            except (TypeError, ValueError, RuntimeError):
                pass
            points.append(end_world)
            labels.append(_endpoint_label('终点 B', end, length))
        else:
            self._remove_line_drag_line_actors()
        try:
            self.plotter.add_point_labels(np.asarray(points), labels, font_size=11,
                text_color='#fff0b8', shape_color='#172435', shape_opacity=.92,
                point_size=0, always_visible=True, pickable=False,
                reset_camera=False, render=False, name=PREVIEW_LABELS)
            self.plotter.render()
        except (TypeError, ValueError, RuntimeError):
            pass

    def _update_line_drag_text(self, valid=True):
        start = self._line_drag_start
        if start is None:
            return
        lines = [f"起点 A | X {start['x']:.2f} · Y {start['y']:.2f} · Z {start['z']:.2f} m"
                 + (f" | 测点 {start['id']}" if start.get('id') else '')]
        if self._line_drag_end is not None:
            end = self._line_drag_end
            lines.append(f"终点 B | X {end['x']:.2f} · Y {end['y']:.2f} · Z {end['z']:.2f} m")
            lines.append(f"长度 {self.line_drag_length():.2f} m")
        if not valid:
            lines.append('终点无效：当前位置没有有效地形，松开将取消。')
        if hasattr(self, 'inspector'):
            self.inspector.setText('拖动绘线  |  ' + '  |  '.join(lines))
        if hasattr(self, 'statusBar'):
            if valid:
                self.statusBar().showMessage(
                    f"拖动绘线：长度 {self.line_drag_length():.2f} m；松开完成，Esc 取消。")
            else:
                self.statusBar().showMessage('拖动绘线：当前位置无效，松开将取消。')

    # ----------------------------------------------------------------- finish
    def _line_drag_position_inside(self, position):
        try:
            x, y = _xy(position)
            width = float(self.plotter.interactor.width())
            height = float(self.plotter.interactor.height())
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return True
        return 0. <= x < width and 0. <= y < height

    def finish_line_drag(self, position=None):
        if not self.line_drag_active():
            return False
        if position is not None and not self._line_drag_position_inside(position):
            self.cancel_line_drag()
            return False
        if position is not None:
            self._line_drag_pointer = position
            self.update_line_drag(position)
        if (position is not None and self._line_drag_press is not None):
            px, py = _xy(self._line_drag_press)
            x, y = _xy(position)
            if float(np.hypot(x - px, y - py)) < MIN_DRAG_PIXELS:
                self.cancel_line_drag()
                return False
        if self._line_drag_start is None or self._line_drag_end is None:
            self.cancel_line_drag()
            return False
        if self.line_drag_length() <= MIN_LINE_LENGTH:
            self.cancel_line_drag()
            return False
        return self.commit_line_drag()

    def commit_line_drag(self):
        if not self.line_drag_active() or self._line_drag_start is None or self._line_drag_end is None:
            return False
        before = self._line_drag_snapshot
        start, end = self._line_drag_start, self._line_drag_end
        length = self.line_drag_length()
        line = [[start['x'], start['y']], [end['x'], end['y']]]
        try:
            relayout = relayout_map_electrodes(self, line)
        except ValueError as error:
            self.cancel_line_drag()
            self.say(str(error))
            return False
        self._line_drag = False
        self._release_line_drag_mouse()
        self._remove_line_drag_actors()
        self._line_drag_start = None
        self._line_drag_end = None
        self._line_drag_press = None
        self._line_drag_pointer = None
        self._line_drag_snapshot = None
        if hasattr(self, 'press_position'):
            self.press_position = None
        self.line = line
        self.points_follow_line = False
        if relayout:
            self.ert_packet, self.points = relayout
        overrides = {}
        for key, record in (('0', start), ('1', end)):
            normalized = normalize_endpoint(dict(record, height_mode='manual' if record.get('source') == 'point' else 'terrain'))
            if normalized is not None:
                overrides[key] = normalized
        self.line_endpoint_overrides = overrides
        self.line_snapshot = None
        if hasattr(self, 'set_mode'):
            self.set_mode('browse')
        else:
            self.mode = 'browse'
        if before is not None:
            self.record_edit(before, HISTORY_LABEL)
        summary = self.line_summary_text() if hasattr(self, 'line_summary_text') else ''
        if summary and hasattr(self, 'inspector'):
            self.inspector.setText(summary)
        if hasattr(self, 'say'):
            update = ('电极已按文件间距重新布置。' if relayout
                      else '原测点与观测未改动，旧反演结果保留并标记为过期。')
            self.say(f"已完成拖动绘线：长度 {length:.2f} m；{update}")
        return True

    def cancel_line_drag(self):
        if not self.line_drag_active():
            return False
        snapshot = self._line_drag_snapshot
        self._line_drag = False
        self._release_line_drag_mouse()
        self._remove_line_drag_actors()
        self._line_drag_start = None
        self._line_drag_end = None
        self._line_drag_press = None
        self._line_drag_pointer = None
        self._line_drag_snapshot = None
        self.line_snapshot = None
        if hasattr(self, 'press_position'):
            self.press_position = None
        if snapshot is not None:
            try:
                restore_edit(self, snapshot)
            except Exception as error:
                if hasattr(self, 'say'):
                    self.say('取消绘线时恢复失败：' + str(error))
        if hasattr(self, 'redraw'):
            self.redraw()
        if hasattr(self, 'inspector'):
            self.inspector.setText('拖动绘线已取消；恢复拖动前的测线和测点。')
        if hasattr(self, 'say'):
            self.say('已取消拖动绘线，恢复拖动前的测线和测点。')
        return True

    def _release_line_drag_mouse(self):
        if not getattr(self, '_line_drag_grabbed', False):
            return
        self._line_drag_grabbed = False
        try:
            self.plotter.interactor.releaseMouse()
        except (AttributeError, RuntimeError):
            pass

    def _remove_line_drag_line_actors(self):
        for name in (PREVIEW_LINE, PREVIEW_END):
            try:
                self.plotter.remove_actor(name, reset_camera=False, render=False)
            except TypeError:
                self.plotter.remove_actor(name)
            except (AttributeError, RuntimeError):
                pass

    def _remove_line_drag_actors(self):
        self._remove_line_drag_line_actors()
        try:
            self.plotter.remove_actor(PREVIEW_LABELS, reset_camera=False, render=False)
        except TypeError:
            self.plotter.remove_actor(PREVIEW_LABELS)
        except (AttributeError, RuntimeError):
            pass

    # ------------------------------------------------------- committed labels
    def line_endpoint_records(self):
        """Start/end with effective override Z taking priority over electrode Z."""
        terrain = getattr(self, 'terrain', None)
        line = getattr(self, 'line', []) or []
        if terrain is None or len(line) < 2:
            return []
        overrides = getattr(self, 'line_endpoint_overrides', {}) or {}
        records = []
        for index, vertex in enumerate((line[0], line[-1])):
            try:
                x, y = float(vertex[0]), float(vertex[1])
                ground = float(terrain.height([[x, y]])[0])
            except (IndexError, TypeError, ValueError, RuntimeError):
                continue
            match = None
            for point in getattr(self, 'points', []) or []:
                try:
                    if abs(float(point['x']) - x) <= 1e-6 and abs(float(point['y']) - y) <= 1e-6:
                        match = point
                        break
                except (KeyError, TypeError, ValueError):
                    continue
            override = effective_endpoint_override(line, overrides, index)
            if override is not None:
                records.append(_record(override, match.get('id') if match is not None else None,
                                       override.get('elevation_source') or '手动高程'))
            elif match is not None:
                records.append(_record(match, match.get('id'), 'point'))
            elif np.isfinite(ground):
                records.append(_record(dict(x=x, y=y, z=ground), None, 'terrain'))
        return records

    def line_summary_text(self):
        records = self.line_endpoint_records()
        if len(records) < 2:
            return ''
        start, end = records[0], records[1]
        length = float(np.hypot(end['x'] - start['x'], end['y'] - start['y']))
        parts = [f'测线长度 {length:.2f} m']
        for name, record in (('起点 A', start), ('终点 B', end)):
            suffix = f" | 测点 {record['id']}" if record.get('id') else ''
            parts.append(f"{name} | X {record['x']:.2f} · Y {record['y']:.2f} · "
                         f"Z {record['z']:.2f} m{suffix}")
        return '\n'.join(parts)

    def draw_line_endpoint_labels(self):
        state=getattr(self,'survey_state',{})
        if any(line.get('id')==state.get('active_id') and not line.get('visible',True)
               for line in state.get('lines',[])):
            return
        if self.line_drag_active():
            return
        records = self.line_endpoint_records()
        if len(records) < 2:
            return
        points = np.asarray([self._drag_world(record, 2.) for record in records[:2]])
        try:
            self.plotter.add_points(points,color='#ffe38a',point_size=12,
                render_points_as_spheres=True,pickable=False,reset_camera=False,
                render=False,name='line-endpoint-markers')
            # Coordinates are shown on hover/selection only; keep the markers.
        except (TypeError, ValueError, RuntimeError):
            pass

    def line_endpoint_at(self, position, radius=SNAP_RADIUS):
        state=getattr(self,'survey_state',{})
        if any(line.get('id')==state.get('active_id') and not line.get('visible',True)
               for line in state.get('lines',[])):
            return None
        records = self.line_endpoint_records()
        if len(records) < 2:
            return None
        px, py = _xy(position)
        best = None
        for index, record in enumerate(records):
            try:
                screen, depth = self._screen(self._drag_world(record, 2.))
            except (TypeError, ValueError, RuntimeError):
                continue
            if not np.isfinite(screen).all() or not 0. <= float(depth) <= 1.:
                continue
            distance = float(np.hypot(screen[0] - px, screen[1] - py))
            if distance <= radius and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def select_line_endpoint(self, index):
        records = self.line_endpoint_records()
        if len(records) < 2 or index not in (0, 1):
            return False
        record = records[index]
        name = '起点 A' if index == 0 else '终点 B'
        if hasattr(self, 'clear_selection'):
            self.clear_selection()
        try:
            self.plotter.add_points(np.asarray([self._drag_world(record, 2.)]),
                color='#fff0a8', point_size=18, render_points_as_spheres=True,
                name='selection', pickable=False, reset_camera=False)
        except (TypeError, ValueError, RuntimeError):
            pass
        self.selected = None
        if hasattr(self, 'update_history_actions'):
            self.update_history_actions()
        suffix = f" | 测点 {record['id']}" if record.get('id') else ''
        total = float(np.hypot(records[1]['x'] - records[0]['x'],
                               records[1]['y'] - records[0]['y']))
        if hasattr(self, 'inspector'):
            self.inspector.setText(f"{name} | X {record['x']:.2f} · Y {record['y']:.2f} · "
                f"Z {record['z']:.2f} m{suffix}\n测线长度 {total:.2f} m；"
                '点击测线中段可查询整条测线。')
        if hasattr(self, 'show_coordinate_selection'):
            self.show_coordinate_selection('endpoint', index)
        return True
