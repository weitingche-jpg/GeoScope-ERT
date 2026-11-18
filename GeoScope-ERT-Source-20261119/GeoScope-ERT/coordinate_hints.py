"""On-demand coordinate labels for map points, endpoints and survey lines."""
import numpy as np
from PySide6 import QtCore
from line_import import is_local_import


ACTOR_NAME = 'coordinate-context'
HOVER_DELAY_MS = 55
HIT_RADIUS = 12.
ENDPOINT_PRIORITY = 2.


class CoordinateHintsMixin:
    """One transient label actor; hover overrides the persistent selection pin."""

    def init_coordinate_hints(self):
        self._coord_hint_targets = []
        self._coord_hint_version = 0
        self._coord_hint_projection = None
        self._coord_hint_pin = None
        self._coord_hint_hover = None
        self._coord_hint_shown = None
        self._coord_hint_actor = None
        self._coord_hint_pending = None
        try:
            timer = QtCore.QTimer(self)
        except TypeError:
            timer = None
        if timer is not None:
            timer.setSingleShot(True)
            timer.setInterval(HOVER_DELAY_MS)
            timer.timeout.connect(self._apply_pending_coordinate_hover)
        self._coord_hint_timer = timer

    # ------------------------------------------------------------- candidates
    def rebuild_coordinate_targets(self):
        self._coord_hint_targets = []
        self._coord_hint_version = int(getattr(self, '_coord_hint_version', 0)) + 1
        self._coord_hint_projection = None
        terrain = getattr(self, 'terrain', None)
        if terrain is None:
            return
        origin = np.asarray(getattr(terrain, 'origin', np.zeros(3)), float)
        state = getattr(self, 'survey_state', None)
        survey = (isinstance(state, dict) and bool(state.get('lines'))
                  and hasattr(self, 'survey_active') and self.survey_active())
        if survey:
            lines = [line for line in state.get('lines') or [] if line.get('visible', True)]
            # Survey electrodes are drawn at their actual local Z, including
            # when every other line is hidden. Match the rendered spheres.
            offset = 0.
            active_id = state.get('active_id')
            visible_ids = {line.get('id') for line in lines}
            for line in lines:
                workspace = line.get('workspace') or {}
                if is_local_import(workspace.get('ert_packet')):
                    continue
                for index, point in enumerate(workspace.get('points') or []):
                    self._coordinate_append_point(line.get('id'), index, point, offset, origin)
            if len(getattr(self, 'line', []) or []) >= 2 and active_id in visible_ids:
                if not is_local_import(getattr(self, 'ert_packet', None)):
                    self._coordinate_append_endpoints(active_id, origin)
        elif not is_local_import(getattr(self, 'ert_packet', None)):
            for index, point in enumerate(getattr(self, 'points', []) or []):
                self._coordinate_append_point(None, index, point, 2., origin)
            if len(getattr(self, 'line', []) or []) >= 2:
                self._coordinate_append_endpoints(None, origin)

    def _coordinate_append_point(self, sid, index, point, offset, origin):
        try:
            real = np.asarray([float(point['x']), float(point['y']), float(point['z'])], float)
        except (KeyError, TypeError, ValueError):
            return
        if not np.isfinite(real).all():
            return
        point_id = str(point.get('id') or '')
        head = f'{sid}:{point_id}' if sid and point_id else (point_id or str(sid or '测点'))
        label = (f'{head}\nX {real[0]:.2f} · Y {real[1]:.2f} · '
                 f'Z {real[2]:.2f} m')
        display = real - origin
        display[2] += offset
        self._coord_hint_targets.append(dict(
            sid=sid, kind='point', index=int(index), id=point_id,
            real_xyz=real, display_xyz=display, label=label,
            key=(sid, 'point', int(index), point_id, *real.tolist())))

    def _coordinate_endpoint_records(self):
        if hasattr(self, 'line_endpoint_records'):
            try:
                records = list(self.line_endpoint_records())
                if len(records) >= 2:
                    return records[:2]
            except (AttributeError, IndexError, TypeError, ValueError, RuntimeError):
                pass
        terrain = getattr(self, 'terrain', None)
        line = list(getattr(self, 'line', []) or [])
        if terrain is None or len(line) < 2:
            return []
        records = []
        for index in (0, len(line) - 1):
            try:
                x, y = float(line[index][0]), float(line[index][1])
                z = float(terrain.height([[x, y]])[0])
            except (IndexError, TypeError, ValueError, RuntimeError):
                return []
            if not np.isfinite([x, y, z]).all():
                return []
            records.append(dict(x=x, y=y, z=z))
        return records

    def _coordinate_append_endpoints(self, sid, origin):
        if is_local_import(getattr(self, 'ert_packet', None)):
            return
        records = self._coordinate_endpoint_records()
        if len(records) < 2:
            return
        for index, record in enumerate(records):
            try:
                real = np.asarray([float(record['x']), float(record['y']),
                                   float(record['z'])], float)
            except (KeyError, TypeError, ValueError):
                continue
            if not np.isfinite(real).all():
                continue
            name = '起点 A' if index == 0 else '终点 B'
            point_id = str(record.get('id') or '')
            head = f'{sid} {name}' if sid else name
            if point_id:
                head += f' [{point_id}]'
            label = (f'{head}\nX {real[0]:.2f} · Y {real[1]:.2f} · '
                     f'Z {real[2]:.2f} m')
            display = real - origin
            display[2] += 2.
            self._coord_hint_targets.append(dict(
                sid=sid, kind='endpoint', index=index, id=point_id,
                real_xyz=real, display_xyz=display, label=label,
                key=(sid, 'endpoint', index, point_id, *real.tolist())))

    def _coordinate_line_target(self):
        if is_local_import(getattr(self, 'ert_packet', None)):
            return None
        records = self._coordinate_endpoint_records()
        terrain = getattr(self, 'terrain', None)
        if len(records) < 2 or terrain is None:
            return None
        try:
            start = np.asarray([float(records[0][key]) for key in ('x', 'y', 'z')], float)
            end = np.asarray([float(records[1][key]) for key in ('x', 'y', 'z')], float)
        except (KeyError, TypeError, ValueError):
            return None
        if not (np.isfinite(start).all() and np.isfinite(end).all()):
            return None
        origin = np.asarray(getattr(terrain, 'origin', np.zeros(3)), float)
        first = start - origin
        last = end - origin
        first[2] += 2.
        last[2] += 2.
        state = getattr(self, 'survey_state', None)
        sid = state.get('active_id') if isinstance(state, dict) and state.get('active_id') else None
        if sid is not None:
            active = next((line for line in state.get('lines') or [] if line.get('id') == sid), None)
            if active is not None and not active.get('visible', True):
                return None
        head = f'测线 {sid}' if sid else '测线'
        length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
        label = (f'{head}\n起点 A | X {start[0]:.2f} · Y {start[1]:.2f} · Z {start[2]:.2f} m\n'
                 f'终点 B | X {end[0]:.2f} · Y {end[1]:.2f} · Z {end[2]:.2f} m\n'
                 f'长度 {length:.2f} m')
        return dict(sid=sid, kind='line', index=0, id='',
                    real_xyz=(start + end) / 2., display_xyz=(first + last) / 2.,
                    label=label, key=(sid, 'line', 0, '', *start.tolist(), *end.tolist()))

    # ------------------------------------------------------------------ hover
    def _coordinate_hover_allowed(self):
        if getattr(self, 'mode', 'browse') != 'browse':
            return False
        if hasattr(self, 'line_drag_active') and self.line_drag_active():
            return False
        return getattr(self, 'drag_endpoint', None) is None

    def request_coordinate_hover(self, position):
        if not self._coordinate_hover_allowed():
            self.clear_coordinate_hover()
            return False
        try:
            self._coord_hint_pending = QtCore.QPointF(position)
        except TypeError:
            return False
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is None:
            self._apply_pending_coordinate_hover()
        else:
            timer.start()
        return True

    def _apply_pending_coordinate_hover(self):
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is not None:
            timer.stop()
        position = getattr(self, '_coord_hint_pending', None)
        self._coord_hint_pending = None
        if position is None or not self._coordinate_hover_allowed():
            self.clear_coordinate_hover()
            return
        target = self._coordinate_target_at(position)
        self._coord_hint_hover = target
        if target is not None:
            self._coordinate_show_target(target)
        elif getattr(self, '_coord_hint_pin', None) is not None:
            self._coordinate_show_target(self._coord_hint_pin)
        else:
            self._coordinate_hide()

    def clear_coordinate_hover(self):
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is not None:
            timer.stop()
        self._coord_hint_pending = None
        self._coord_hint_hover = None
        pin = getattr(self, '_coord_hint_pin', None)
        if pin is not None:
            self._coordinate_show_target(pin)
        else:
            self._coordinate_hide()

    def _coordinate_pointer_left(self):
        self.clear_coordinate_hover()

    # -------------------------------------------------------------- projection
    def _coordinate_projection_key(self):
        plotter = getattr(self, 'plotter', None)
        camera = getattr(plotter, 'camera', None)
        try:
            mtime = float(camera.GetMTime())
        except (AttributeError, TypeError, ValueError):
            mtime = 0.
        interactor = getattr(plotter, 'interactor', None)
        try:
            size = (int(interactor.width()), int(interactor.height()))
        except (AttributeError, TypeError, ValueError):
            size = (0, 0)
        return mtime, size, int(getattr(self, '_coord_hint_version', 0))

    def _coordinate_projection_entries(self):
        key = self._coordinate_projection_key()
        cached = getattr(self, '_coord_hint_projection', None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == key:
            return cached[1]
        entries = []
        for target in getattr(self, '_coord_hint_targets', []):
            try:
                screen, depth = self._screen(target['display_xyz'])
                screen = np.asarray(screen, float)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                continue
            if screen.shape != (2,) or not np.isfinite(screen).all():
                continue
            try:
                depth = float(depth)
            except (TypeError, ValueError):
                continue
            if not 0. <= depth <= 1.:
                continue
            entries.append((screen, target))
        self._coord_hint_projection = (key, entries)
        return entries

    def _coordinate_target_at(self, position):
        try:
            px, py = float(position.x()), float(position.y())
        except AttributeError:
            px, py = float(position[0]), float(position[1])
        endpoint = point = None
        endpoint_distance = point_distance = HIT_RADIUS
        for screen, target in self._coordinate_projection_entries():
            distance = float(np.hypot(screen[0] - px, screen[1] - py))
            if distance > HIT_RADIUS:
                continue
            if target['kind'] == 'endpoint' and distance <= endpoint_distance:
                endpoint, endpoint_distance = target, distance
            elif target['kind'] == 'point' and distance <= point_distance:
                point, point_distance = target, distance
        if endpoint is not None and (point is None or endpoint_distance <= point_distance + ENDPOINT_PRIORITY):
            return endpoint
        return point

    # ------------------------------------------------------------- label actor
    def _coordinate_remove_actor(self, render=False):
        plotter = getattr(self, 'plotter', None)
        if plotter is not None:
            try:
                plotter.remove_actor(ACTOR_NAME, reset_camera=False, render=False)
            except TypeError:
                try:
                    plotter.remove_actor(ACTOR_NAME)
                except (AttributeError, RuntimeError):
                    pass
            except (AttributeError, RuntimeError):
                pass
        self._coord_hint_actor = None
        self._coord_hint_shown = None
        if render:
            self._coordinate_render()

    def _coordinate_render(self):
        plotter = getattr(self, 'plotter', None)
        if plotter is not None and hasattr(plotter, 'render'):
            plotter.render()

    def _coordinate_hide(self):
        if getattr(self, '_coord_hint_actor', None) is None and getattr(self, '_coord_hint_shown', None) is None:
            return
        self._coordinate_remove_actor(render=True)

    def _coordinate_show_target(self, target):
        plotter = getattr(self, 'plotter', None)
        if plotter is None or target is None:
            return
        key = target.get('key')
        if (key == getattr(self, '_coord_hint_shown', None)
                and getattr(self, '_coord_hint_actor', None) is not None):
            return
        self._coordinate_remove_actor(render=False)
        try:
            actor = plotter.add_point_labels(
                np.asarray([target['display_xyz']], float), [target['label']],
                font_size=11, text_color='#eff6ff', shape_color='#202c3c',
                shape_opacity=.94, point_size=0, always_visible=True,
                pickable=False, reset_camera=False, render=False,
                name=ACTOR_NAME)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return
        self._coord_hint_actor = actor
        self._coord_hint_shown = key
        self._coordinate_render()

    # --------------------------------------------------------------- selection
    def _coordinate_selection_target(self, kind, index):
        if kind == 'line':
            return self._coordinate_line_target()
        if not getattr(self, '_coord_hint_targets', None) and getattr(self, 'terrain', None) is not None:
            self.rebuild_coordinate_targets()
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        state = getattr(self, 'survey_state', None)
        active = state.get('active_id') if isinstance(state, dict) else None
        for target in getattr(self, '_coord_hint_targets', []):
            if target['kind'] != kind or target['index'] != index:
                continue
            if active is not None and target['sid'] != active:
                continue
            if active is None and target['sid'] is not None:
                continue
            return target
        return None

    def show_coordinate_selection(self, kind, index):
        target = self._coordinate_selection_target(kind, index)
        if target is None:
            return False
        self._coord_hint_pin = target
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is not None:
            timer.stop()
        self._coord_hint_pending = None
        self._coord_hint_hover = None
        self._coordinate_show_target(target)
        return True

    def clear_coordinate_hints(self):
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is not None:
            timer.stop()
        self._coord_hint_pending = None
        self._coord_hint_pin = None
        self._coord_hint_hover = None
        self._coordinate_hide()

    def _reset_coordinate_hints_for_redraw(self):
        timer = getattr(self, '_coord_hint_timer', None)
        if timer is not None:
            timer.stop()
        self._coord_hint_pending = None
        self._coord_hint_pin = None
        self._coord_hint_hover = None
        self._coord_hint_shown = None
        self._coord_hint_actor = None
        self._coord_hint_targets = []
        self._coord_hint_projection = None
