"""Quick parallel survey-line planning plus one atomic commit path.

``build_parallel_plan`` is a pure planner: it never mutates the baseline
workspace, the terrain or any existing survey line.  The window mixin rebuilds
and validates a fresh plan immediately before committing it, so a stale preview
can never overwrite changed geometry.
"""
import copy
import hashlib
import json
import math
import numpy as np
import pyvista as pv
from PySide6 import QtWidgets as Q
from core import stations_along
from region_project import workspace_document
from shortcuts import edit_snapshot
from endpoint_edit import line_render_xyz
from survey_project import empty_survey, validate_survey

PLANNED_ELEVATION = '规划贴地'
PREVIEW_VALID = 'quick-parallel-preview-valid'
PREVIEW_INVALID = 'quick-parallel-preview-invalid'
DEFAULT_PLANNED_POINTS = 24
MAX_PROJECT_LINES = 30
_POINT_TOL = .05
_CONFLICT_TOL = .05
_PARALLEL_TOL = math.sin(math.radians(.5))


def workspace_signature(workspace):
    """Stable signature used to detect a changed baseline before commit."""
    raw = json.dumps(workspace, sort_keys=True, ensure_ascii=False,
                     allow_nan=True, default=str, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _finite_number(value, label, positive=False):
    if isinstance(value, bool):
        raise ValueError(label + '必须是数值。')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是数值。') from None
    if not math.isfinite(number):
        raise ValueError(label + '必须是有限数值。')
    if positive and number <= 0:
        raise ValueError(label + '必须大于0。')
    return number


def _total_count(value):
    if isinstance(value, bool):
        raise ValueError('总条数（含基准线）必须是整数。')
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('总条数（含基准线）必须是整数。') from None
    if number != value:
        raise ValueError('总条数（含基准线）必须是整数。')
    if not 2 <= number <= MAX_PROJECT_LINES:
        raise ValueError('总条数（含基准线）须为2–30。')
    return number


def _line_geometry(line):
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        return None
    vertices = []
    for vertex in line:
        if not isinstance(vertex, (list, tuple)) or len(vertex) < 2:
            return None
        try:
            x, y = float(vertex[0]), float(vertex[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        vertices.append((x, y))
    return np.asarray(vertices, float)


def _terrain_bounds(terrain):
    try:
        xs = np.asarray(getattr(terrain, 'x', []), float).ravel()
        ys = np.asarray(getattr(terrain, 'y', []), float).ravel()
    except (TypeError, ValueError):
        return None
    if not xs.size or not ys.size or not np.isfinite(xs).all() or not np.isfinite(ys).all():
        return None
    return float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())


def _terrain_fallback_z(terrain):
    try:
        values = np.asarray(getattr(terrain, 'z', []), float).ravel()
        values = values[np.isfinite(values)]
        if values.size:
            return float(values.mean())
    except (TypeError, ValueError):
        pass
    return 0.0


def _sample_height(terrain, xy):
    try:
        values = np.asarray(terrain.height(np.asarray(xy, float).reshape(1, 2)), float).ravel()
    except (TypeError, ValueError, RuntimeError):
        return None
    if values.size != 1 or not math.isfinite(float(values[0])):
        return None
    return float(values[0])


def _line_in_terrain(line, terrain):
    bounds = _terrain_bounds(terrain)
    if bounds is None:
        return False, '地形范围为空或无效。'
    xmin, xmax, ymin, ymax = bounds
    try:
        _, xy = stations_along(line, 129)
        xy = np.asarray(xy, float)
    except (TypeError, ValueError, IndexError):
        return False, '平移后测线几何无效。'
    if not np.isfinite(xy).all():
        return False, '平移后测线坐标无效。'
    if np.any(xy[:, 0] < xmin) or np.any(xy[:, 0] > xmax) or np.any(xy[:, 1] < ymin) or np.any(xy[:, 1] > ymax):
        return False, '平移后测线超出地形范围，不能自动裁短。'
    try:
        z = np.asarray(terrain.height(xy), float).ravel()
    except (TypeError, ValueError, RuntimeError):
        return False, '地形高程读取失败。'
    if z.size != len(xy) or not np.isfinite(z).all():
        return False, '平移后测线经过地形空值区域。'
    return True, ''


def _existing_workspace(item):
    if not isinstance(item, dict):
        return None
    workspace = item.get('workspace')
    if isinstance(workspace, dict):
        return workspace
    return item if 'line' in item else None


def _lines_conflict(candidate, existing):
    """Parallel, collinear and with a positive-length interval overlap."""
    candidate_geometry = _line_geometry(candidate)
    existing_geometry = _line_geometry(existing)
    if candidate_geometry is None or existing_geometry is None:
        return False
    if len(existing_geometry)>2:
        return any(_lines_conflict(candidate,existing_geometry[i:i+2].tolist())
                   for i in range(len(existing_geometry)-1))
    candidate_vector = candidate_geometry[-1] - candidate_geometry[0]
    existing_vector = existing_geometry[-1] - existing_geometry[0]
    candidate_length = float(np.linalg.norm(candidate_vector))
    existing_length = float(np.linalg.norm(existing_vector))
    if candidate_length <= 1e-9 or existing_length <= 1e-9:
        return False
    candidate_direction = candidate_vector / candidate_length
    existing_direction = existing_vector / existing_length
    if abs(candidate_direction[0] * existing_direction[1] -
           candidate_direction[1] * existing_direction[0]) > _PARALLEL_TOL:
        return False
    relative = existing_geometry - candidate_geometry[0]
    perpendicular = relative[:, 0] * candidate_direction[1] - relative[:, 1] * candidate_direction[0]
    if np.max(np.abs(perpendicular)) > _CONFLICT_TOL:
        return False
    stations = relative @ candidate_direction
    low = max(0.0, float(stations.min()))
    high = min(candidate_length, float(stations.max()))
    return high - low > _CONFLICT_TOL


def _next_free_line_id(used):
    number = 1
    while f'L{number:02}' in used:
        number += 1
    return f'L{number:02}'


def build_parallel_plan(base_workspace, terrain, total_count=3, gap=2., side='left',
                        existing_lines=(), base_id=None):
    """Return translated parallel-line candidates without mutating any input.

    Invalid geometric arguments raise ``ValueError``.  Structural problems are
    reported in ``errors``.  Out-of-bounds or collinear-overlap candidates are
    returned as ``valid=False`` with human-readable ``issues`` so the dialog can
    still draw a red preview instead of silently dropping them.
    """
    total_count = _total_count(total_count)
    gap = _finite_number(gap, '线距', positive=True)
    if side not in ('left', 'right'):
        raise ValueError('基准侧只能是 left 或 right。')
    existing = list(existing_lines or ())
    plan = dict(candidates=[], errors=[], point_count=0, spacing=0., length=0.,
                total_count=total_count, gap=gap, side=side, base_id=base_id,
                base_signature=None, existing_line_ids=[], point_source=None)
    if not isinstance(base_workspace, dict):
        plan['errors'].append('基准工作区格式无效。')
        return plan
    plan['base_signature'] = workspace_signature(base_workspace)
    if terrain is None:
        plan['errors'].append('尚未加载地形，不能规划平行测线。')
        return plan

    raw_line = base_workspace.get('line')
    if not isinstance(raw_line, list) or len(raw_line) < 2:
        plan['errors'].append('基准测线至少需要两个端点。')
        return plan
    vertices = []
    for index, vertex in enumerate(raw_line):
        if not isinstance(vertex, (list, tuple)) or len(vertex) < 2:
            plan['errors'].append(f'基准测线第 {index + 1} 个顶点格式无效。')
            continue
        try:
            x, y = float(vertex[0]), float(vertex[1])
        except (TypeError, ValueError):
            plan['errors'].append(f'基准测线第 {index + 1} 个顶点不是数值。')
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            plan['errors'].append(f'基准测线第 {index + 1} 个顶点含空值。')
            continue
        vertices.append((x, y))
    if plan['errors']:
        return plan
    vertices = np.asarray(vertices, float)
    direction_vector = vertices[-1] - vertices[0]
    length = float(np.linalg.norm(direction_vector))
    if length <= 1e-9:
        plan['errors'].append('基准测线长度为零。')
        return plan
    direction = direction_vector / length
    tolerance = max(1e-6, length * 1e-6)
    relative = vertices - vertices[0]
    perpendicular = relative[:, 0] * direction[1] - relative[:, 1] * direction[0]
    if np.max(np.abs(perpendicular)) > tolerance:
        plan['errors'].append('基准测线必须为二维直线（允许多顶点共线），偏离直线请先修正。')
    projections = relative @ direction
    if np.any(np.diff(projections) < -tolerance):
        plan['errors'].append('基准测线出现回折，请先修正顶点顺序。')
    segment_lengths = np.linalg.norm(np.diff(vertices, axis=0), axis=1)
    if np.any(segment_lengths <= 1e-9):
        plan['errors'].append('基准测线含重合的相邻顶点。')
    if plan['errors']:
        return plan

    raw_points = base_workspace.get('points')
    if raw_points is None:
        raw_points = []
    if not isinstance(raw_points, list):
        plan['errors'].append('基准测点格式无效。')
        return plan
    records = []
    used_ids = set()
    off_line = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict):
            plan['errors'].append(f'基准测点第 {index + 1} 项格式无效。')
            continue
        try:
            x, y = float(point['x']), float(point['y'])
        except (KeyError, TypeError, ValueError):
            plan['errors'].append(f'基准测点第 {index + 1} 项缺少有效的 X/Y。')
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            plan['errors'].append(f'基准测点第 {index + 1} 项 X/Y 含空值。')
            continue
        if point.get('z') is not None:
            try:
                z = float(point['z'])
            except (TypeError, ValueError):
                plan['errors'].append(f'基准测点第 {index + 1} 项 Z 不是数值。')
                continue
            if not math.isfinite(z):
                plan['errors'].append(f'基准测点第 {index + 1} 项 Z 含空值。')
                continue
        point_id = point.get('id')
        point_id = str(point_id).strip() if point_id is not None else ''
        if not point_id:
            point_id = f'E{index + 1:02}'
        if point_id in used_ids:
            plan['errors'].append(f'基准测点编号重复：{point_id}。')
            continue
        used_ids.add(point_id)
        offset = np.array([x, y], float) - vertices[0]
        station = float(offset @ direction)
        perpendicular = abs(offset[0] * direction[1] - offset[1] * direction[0])
        if perpendicular > _POINT_TOL or station < -_POINT_TOL or station > length + _POINT_TOL:
            off_line.append(point_id)
            continue
        records.append((point_id, float(x), float(y), min(max(station, 0.), length)))
    if off_line:
        names = '、'.join(off_line[:4]) + ('等' if len(off_line) > 4 else '')
        plan['errors'].append(f'基准测点 {names} 不在基准测线上，请先核对测点与测线；不会把独立点铺成散点。')
    if plan['errors']:
        return plan

    packet = base_workspace.get('ert_packet')
    packet_spacing = None
    if packet is not None:
        if not isinstance(packet, dict):
            plan['errors'].append('基准观测布设格式无效。')
        else:
            try:
                electrodes = int(packet['electrodes'])
                spacing = float(packet['spacing'])
            except (KeyError, TypeError, ValueError, OverflowError):
                plan['errors'].append('基准观测布设缺少电极数或电极间距。')
                electrodes, spacing = 0, 0.
            if electrodes < 2 or not math.isfinite(spacing) or spacing <= 0:
                plan['errors'].append('基准观测布设的电极数或电极间距无效。')
            else:
                expected_length = (electrodes - 1) * spacing
                if not math.isclose(length, expected_length, rel_tol=1e-6, abs_tol=1e-3):
                    plan['errors'].append(
                        f'基准测线长度 {length:.6f} m 与观测布设 (电极数-1)×间距 = '
                        f'{expected_length:.6f} m 不一致，请先校准观测布设；不会缩放物理布设。')
                elif len(records) != electrodes:
                    plan['errors'].append(
                        f'基准测点 {len(records)} 个，与观测电极数 {electrodes} 不一致，请先校准；'
                        '不会把观测复制到新线。')
                else:
                    station_tolerance = max(.05, spacing * 1e-3)
                    for index, (_, _, _, station) in enumerate(records):
                        if not math.isclose(station, index * spacing, rel_tol=0,
                                            abs_tol=station_tolerance):
                            plan['errors'].append(
                                f'基准测点 {records[index][0]} 的沿线位置与观测电极间距不一致，'
                                '请先校准观测布设。')
                            break
                packet_spacing = spacing
    if plan['errors']:
        return plan

    if len(records) >= 2:
        point_count = len(records)
        stations = np.asarray([record[3] for record in records], float)
        point_ids = [record[0] for record in records]
        intervals=np.linalg.norm(np.diff(np.asarray([[r[1],r[2]] for r in records]),axis=0),axis=1)
        spacing = float(packet_spacing) if packet_spacing else float(intervals.mean())
        point_source = 'base'
    else:
        point_count = DEFAULT_PLANNED_POINTS
        stations = np.linspace(0., length, point_count)
        point_ids = [f'E{index + 1:02}' for index in range(point_count)]
        spacing = length / (point_count - 1)
        point_source = 'planned'

    plan.update(point_count=point_count, spacing=float(spacing), length=length,
                point_source=point_source)

    for item in existing:
        if isinstance(item, dict) and item.get('id') is not None:
            plan['existing_line_ids'].append(str(item['id']))
    base_in_existing = base_id is not None and any(
        isinstance(item, dict) and item.get('id') == base_id for item in existing)
    projected_total = len(existing) + (total_count - 1 if base_in_existing else total_count)
    if projected_total > MAX_PROJECT_LINES:
        plan['errors'].append(
            f'生成后项目共 {projected_total} 条测线，超过 {MAX_PROJECT_LINES} 条上限。')
        return plan

    left_normal = np.array([-direction[1], direction[0]], float)
    side_normal = left_normal if side == 'left' else -left_normal
    fallback_z = _terrain_fallback_z(terrain)
    for offset_index in range(1, total_count):
        offset = float(offset_index) * gap
        shift = side_normal * offset
        start = vertices[0] + shift
        end = vertices[-1] + shift
        translated_line = [[float(start[0]), float(start[1])],
                           [float(end[0]), float(end[1])]]
        issues = []
        inside, reason = _line_in_terrain(translated_line, terrain)
        if not inside:
            issues.append(reason)
        new_points = []
        missing_heights = 0
        for point_index,(station, point_id) in enumerate(zip(stations, point_ids)):
            xy = (np.asarray(records[point_index][1:3]) if point_source=='base'
                  else vertices[0] + direction * station) + shift
            z = _sample_height(terrain, xy)
            if z is None:
                missing_heights += 1
                z = fallback_z
            new_points.append(dict(id=point_id, x=float(xy[0]), y=float(xy[1]), z=float(z),
                                   elevation_source=PLANNED_ELEVATION))
        if missing_heights:
            issues.append(f'平移后 {missing_heights} 个测点处地形高程无效。')
        for item in existing:
            if isinstance(item, dict) and base_id is not None and item.get('id') == base_id:
                continue
            existing_workspace = _existing_workspace(item)
            if existing_workspace is None:
                continue
            if _lines_conflict(translated_line, existing_workspace.get('line')):
                label = item.get('id') if isinstance(item, dict) else None
                label = label or '未命名'
                issues.append(f'与已有测线 {label} 平行共线且线段区间重叠，不能覆盖或重合布置。')
        workspace = dict(
            schema=1, terrain=terrain.source, points=new_points, line=translated_line,
            line_endpoint_overrides={}, ert_packet=None, case=None,
            points_follow_line=True, simulation_enabled=False, result_binding=None,
            workflow_step=1, opacity=base_workspace.get('opacity', 40))
        plan['candidates'].append(dict(index=offset_index, offset=offset, workspace=workspace,
                                       issues=issues, valid=not issues))
    return plan


class QuickParallelMixin:
    """Window-side base selection, preview-independent commit and dialog entry."""

    def quick_parallel_base_workspace(self):
        state = getattr(self, 'survey_state', None)
        if isinstance(state, dict) and state.get('lines'):
            active_id = state.get('active_id')
            line = next((item for item in state['lines'] if item.get('id') == active_id), None)
            if line is None:
                raise ValueError('没有活动测线，无法确定快速布置的基准。')
            return (copy.deepcopy(line.get('workspace') or {}), line.get('id'),
                    line.get('name') or line.get('id'))
        workspace = workspace_document(self)
        if not isinstance(workspace.get('line'), list) or len(workspace['line']) < 2:
            raise ValueError('当前没有完整的基准测线。')
        return workspace, None, '当前单线'

    def _quick_parallel_existing_lines(self):
        state = getattr(self, 'survey_state', None)
        return list(state.get('lines') or []) if isinstance(state, dict) else []

    def open_quick_parallel_dialog(self):
        if getattr(self, 'terrain', None) is None:
            self.say('请先导入地形，再快速布置平行线。')
            return None
        if getattr(self, 'process', None) is not None or (
                hasattr(self, 'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，暂不能快速布置平行线。')
            return None
        if hasattr(self, 'require_committed_edit') and not self.require_committed_edit():
            return None
        if hasattr(self, 'sync_survey_line'):
            self.sync_survey_line()
        try:
            base_workspace, base_id, base_name = self.quick_parallel_base_workspace()
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.say(str(error))
            return None
        camera = None
        try:
            camera = copy.deepcopy(self.plotter.camera_position)
        except Exception:
            camera = None
        dialog = QuickParallelDialog(self, base_workspace, base_id=base_id, base_name=base_name,
                                     existing_lines=self._quick_parallel_existing_lines(),
                                     camera=camera)
        result = dialog.exec()
        if result == Q.QDialog.Accepted:
            plan = dialog.plan
            dialog.deleteLater()
            return plan
        dialog.cleanup()
        dialog.deleteLater()
        return None

    def apply_quick_parallel_plan(self, plan):
        """Validate a fresh plan and commit all new lines as one history step."""
        if not isinstance(plan, dict):
            raise ValueError('快速布置方案格式无效。')
        if getattr(self, 'terrain', None) is None:
            raise ValueError('尚未加载地形。')
        if getattr(self, 'process', None) is not None or (
                hasattr(self, 'survey_busy') and self.survey_busy()):
            raise ValueError('计算或测线批次进行中，暂不能生成平行测线。')
        if hasattr(self, 'require_committed_edit') and not self.require_committed_edit():
            raise ValueError('请先完成当前编辑，再生成平行测线。')
        if plan.get('errors'):
            raise ValueError('；'.join(str(item) for item in plan['errors']))
        candidates = plan.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise ValueError('快速布置方案没有可新增的平行线。')
        if any(not isinstance(item, dict) or not item.get('valid') for item in candidates):
            raise ValueError('快速布置方案含越界或重合的红色候选线，请先处理。')
        try:
            total_count = plan['total_count']
            gap = plan['gap']
            side = plan['side']
        except KeyError:
            raise ValueError('快速布置方案缺少总条数、线距或基准侧。') from None

        if hasattr(self, 'sync_survey_line'):
            self.sync_survey_line()
        base_workspace, base_id, base_name = self.quick_parallel_base_workspace()
        expected_signature = plan.get('base_signature')
        actual_signature = workspace_signature(base_workspace)
        plan_base_id = plan.get('base_id')
        if plan_base_id is not None and plan_base_id != base_id:
            raise ValueError('基准测线已切换，当前方案已过期；请重新打开快速布置核对。')
        if expected_signature and expected_signature != actual_signature:
            raise ValueError('基准测线已变化，当前预览已过期；请重新打开快速布置核对。')
        if plan_base_id is None and base_id is not None and not expected_signature:
            raise ValueError('当前方案没有基准签名，不能确认；请重新打开快速布置核对。')

        state = copy.deepcopy(getattr(self, 'survey_state', None) or empty_survey())
        if not isinstance(state, dict) or state.get('schema') != 1:
            raise ValueError('测网状态无效，不能生成平行测线。')
        existing = list(state.get('lines') or [])
        if not existing:
            if not (base_workspace.get('points') or base_workspace.get('line')
                    or base_workspace.get('ert_packet') is not None or base_workspace.get('case')):
                raise ValueError('当前工作区没有可收纳的基准测线。')
            base_sid = 'L01'
            state['lines'] = [dict(id=base_sid, name=base_sid, selected=True, visible=True,
                                   workspace=copy.deepcopy(base_workspace))]
            state['active_id'] = base_sid
        else:
            active = next((item for item in existing if item.get('id') == state.get('active_id')), None)
            if active is None:
                raise ValueError('没有活动基准测线，不能快速布置平行线。')
            if plan.get('base_id') is not None and active.get('id') != plan['base_id']:
                raise ValueError('基准测线已切换，当前方案已过期。')
            base_sid = active['id']

        fresh = build_parallel_plan(base_workspace, self.terrain, total_count=total_count,
                                    gap=gap, side=side, existing_lines=state['lines'],
                                    base_id=base_sid)
        if fresh.get('errors'):
            raise ValueError('；'.join(str(item) for item in fresh['errors']))
        if expected_signature and fresh.get('base_signature') != expected_signature:
            raise ValueError('基准测线已变化，当前预览已过期。')
        if len(fresh.get('candidates') or []) != len(candidates):
            raise ValueError('当前测线与预览不一致，请重新打开快速布置核对。')
        if any(not item.get('valid') for item in fresh['candidates']):
            problems = [issue for item in fresh['candidates'] for issue in item.get('issues') or []]
            raise ValueError('当前基准或已有测线已变化，不能生成：' + '；'.join(problems[:3]))
        if len(state['lines']) + len(fresh['candidates']) > MAX_PROJECT_LINES:
            raise ValueError(f'生成后项目超过 {MAX_PROJECT_LINES} 条测线上限。')

        used = {item.get('id') for item in state['lines']}
        new_ids = []
        for candidate in fresh['candidates']:
            sid = _next_free_line_id(used)
            used.add(sid)
            state['lines'].append(dict(
                id=sid, name=sid, selected=True, visible=True,
                workspace=copy.deepcopy(candidate['workspace'])))
            new_ids.append(sid)
        state['active_id'] = base_sid
        state = validate_survey(state, self.terrain)
        if state.get('active_id') != base_sid:
            raise ValueError('生成后活动测线不是基准测线，已取消提交。')

        before = edit_snapshot(self)
        self.survey_state = state
        self.record_edit(before, '快速布置平行线')
        if hasattr(self, 'survey_dock'):
            self.survey_dock.show()
        if hasattr(self, 'redraw'):
            self.redraw()
        if hasattr(self, 'refresh_survey_ui'):
            self.refresh_survey_ui()
        self._focus_quick_parallel_group([base_sid] + new_ids)
        if hasattr(self, 'say'):
            self.say(f'已以 {base_name} 为基准快速布置 {len(new_ids)} 条平行线：{", ".join(new_ids)}；'
                     f'总计 {total_count} 条，线距 {float(gap):.3f} m。Ctrl+Z 可一次撤销整组新增。')
        return new_ids

    def _focus_quick_parallel_group(self, line_ids):
        wanted = set(line_ids)
        state = getattr(self, 'survey_state', None) or {}
        lines = [item for item in state.get('lines') or [] if item.get('id') in wanted]
        points = []
        for line in lines:
            workspace = line.get('workspace') or {}
            points.extend(point for point in workspace.get('points') or [] if isinstance(point, dict))
            try:
                xyz = line_render_xyz(self.terrain, workspace.get('line') or [],
                                      workspace.get('line_endpoint_overrides') or {}, 2)
            except (TypeError, ValueError, IndexError):
                xyz = None
            if xyz is not None:
                points.extend(dict(x=float(point[0]), y=float(point[1]), z=float(point[2]))
                              for point in np.asarray(xyz, float))
        if points and hasattr(self, '_focus_survey_points'):
            try:
                self._focus_survey_points(points)
                return
            except (TypeError, ValueError, KeyError):
                pass
        if hasattr(self, 'focus_all_survey_lines'):
            try:
                self.focus_all_survey_lines()
            except (TypeError, ValueError, KeyError):
                pass


class QuickParallelDialog(Q.QDialog):
    """Small parameter dialog; the dashed preview lives only in the plotter."""

    def __init__(self, window, base_workspace, base_id=None, base_name=None,
                 existing_lines=(), camera=None):
        super().__init__(window)
        self.workbench = window
        self.base_workspace = copy.deepcopy(base_workspace)
        self.base_id = base_id
        self.base_name = base_name or (base_id or '当前单线')
        self.existing_lines = copy.deepcopy(list(existing_lines or ()))
        self._camera = copy.deepcopy(camera)
        try:self._full_camera=window.plotter.camera.copy()
        except (AttributeError,TypeError,RuntimeError):self._full_camera=None
        self._preview_names = set()
        self.plan = None
        self.setObjectName('quick-parallel-dialog')
        self.setWindowTitle('快速布置平行线')
        self.setModal(True)
        self.resize(460, 440)

        layout = Q.QVBoxLayout(self)
        title = Q.QLabel(f'基准测线：{self.base_name}')
        title.setStyleSheet('font-weight:700; color:#ffe38a;')
        layout.addWidget(title)
        form = Q.QFormLayout()
        self.total = Q.QSpinBox(objectName='parallel-total-count')
        self.total.setRange(2, MAX_PROJECT_LINES)
        self.total.setValue(3)
        self.total.setSuffix(' 条')
        self.total.setToolTip('包含当前基准测线的总条数。')
        self.gap = Q.QDoubleSpinBox(objectName='parallel-line-gap')
        self.gap.setRange(.001, 10000.)
        self.gap.setDecimals(3)
        self.gap.setSingleStep(.5)
        self.gap.setValue(2.)
        self.gap.setSuffix(' m')
        self.side = Q.QComboBox(objectName='parallel-side')
        self.side.addItem('左侧（起点→终点左手法向）', 'left')
        self.side.addItem('右侧（起点→终点右手法向）', 'right')
        self.side.setToolTip('方向由基准线起点指向终点；左侧取法向 (-dy, dx)，右侧取反。')
        form.addRow('总条数（含基准线）', self.total)
        form.addRow('平行线距', self.gap)
        form.addRow('基准侧', self.side)
        layout.addLayout(form)
        direction = Q.QLabel('方向箭头：基准起点 → 终点。左侧/右侧按该方向作等距法向平移；'
                             '蓝色虚线有效，红色虚线需先处理。')
        direction.setWordWrap(True)
        layout.addWidget(direction)
        self.summary = Q.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.error_label = Q.QPlainTextEdit()
        self.error_label.setReadOnly(True)
        self.error_label.setFixedHeight(88)
        self.error_label.setVisible(False)
        self.error_label.setStyleSheet('color:#ff7b72;')
        layout.addWidget(self.error_label)
        locate_row = Q.QHBoxLayout()
        self.locate_button = Q.QPushButton('定位整组')
        self.locate_button.setObjectName('quick-parallel-focus')
        self.locate_button.setToolTip('只移动相机查看基准和预览线；取消会恢复打开前的相机。')
        self.locate_button.clicked.connect(self._locate_group)
        locate_row.addWidget(self.locate_button)
        locate_row.addStretch()
        layout.addLayout(locate_row)
        layout.addStretch()
        self.buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        self.confirm_button = self.buttons.button(Q.QDialogButtonBox.Ok)
        self.confirm_button.setText('确认生成')
        self.confirm_button.setObjectName('quick-parallel-confirm')
        cancel_button = self.buttons.button(Q.QDialogButtonBox.Cancel)
        cancel_button.setText('取消')
        cancel_button.setObjectName('quick-parallel-cancel')
        self.buttons.accepted.connect(self._confirm)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.total.valueChanged.connect(self._refresh_plan)
        self.gap.valueChanged.connect(self._refresh_plan)
        self.side.currentIndexChanged.connect(self._refresh_plan)
        self.finished.connect(self._finished)
        self._refresh_plan()
        try:
            area=window.screen().availableGeometry()
            anchor=window.mapToGlobal(window.rect().topLeft())
            self.setMaximumHeight(max(360,area.height()-60))
            self.adjustSize()
            self.move(max(area.left()+12,min(anchor.x()+12,area.right()-self.width()-12)),area.top()+30)
        except (AttributeError,RuntimeError,TypeError):pass

    def _refresh_plan(self, *_args):
        try:
            plan = build_parallel_plan(
                self.base_workspace, self.workbench.terrain,
                total_count=self.total.value(), gap=self.gap.value(),
                side=self.side.currentData() or 'left',
                existing_lines=self.existing_lines, base_id=self.base_id)
        except (ValueError, TypeError, ArithmeticError) as error:
            self.plan = None
            self.summary.setText('参数无效，无法生成预览。')
            self.error_label.setText(str(error))
            self.confirm_button.setEnabled(False)
            self.locate_button.setEnabled(False)
            self._clear_preview()
            return
        self.plan = plan
        problems = list(plan.get('errors') or [])
        for candidate in plan.get('candidates') or []:
            for issue in candidate.get('issues') or []:
                problems.append(f"第 {candidate.get('index')} 条：{issue}")
        if plan.get('errors'):
            self.summary.setText('基准结构或观测布设未通过检查，不能生成。')
        else:
            side_text = '左侧' if (self.side.currentData() or 'left') == 'left' else '右侧'
            if plan.get('point_source') == 'base':
                source_text = f"沿用基准测点的相对位置与编号（平均间距 {plan['spacing']:.3f} m）"
            else:
                source_text = f"基准少于2点，按24个等距点规划（规划间距 {plan['spacing']:.3f} m）"
            count = len(plan.get('candidates') or [])
            self.summary.setText(
                f"将新增 {count} 条平行线，总计 {plan['total_count']} 条（含基准）。"
                f"长度 {plan['length']:.3f} m，线距 {plan['gap']:.3f} m，{side_text}；"
                f"{source_text}。"
                + ('已核验基准观测的电极数与间距。' if self.base_workspace.get('ert_packet') else ''))
        self.error_label.setPlainText('\n'.join(problems))
        self.error_label.setVisible(bool(problems))
        enabled = (not plan.get('errors') and bool(plan.get('candidates'))
                   and all(candidate.get('valid') for candidate in plan['candidates']))
        self.confirm_button.setEnabled(enabled)
        self.locate_button.setEnabled(not plan.get('errors') and bool(plan.get('candidates')))
        self._refresh_preview(plan)

    def _locate_group(self):
        if self.plan is None:
            return
        workspaces = [self.base_workspace]
        workspaces.extend(candidate['workspace'] for candidate in self.plan.get('candidates') or [])
        points = []
        for workspace in workspaces:
            points.extend(point for point in workspace.get('points') or [] if isinstance(point, dict))
            try:
                xyz = line_render_xyz(self.workbench.terrain, workspace.get('line') or [],
                                      workspace.get('line_endpoint_overrides') or {}, 2)
            except (TypeError, ValueError, IndexError):
                xyz = None
            if xyz is not None:
                points.extend(dict(x=float(point[0]), y=float(point[1]), z=float(point[2]))
                              for point in np.asarray(xyz, float))
        if points and hasattr(self.workbench, '_focus_survey_points'):
            try:
                self.workbench._focus_survey_points(points)
                return
            except (TypeError, ValueError, KeyError):
                pass
        if hasattr(self.workbench, 'focus_all_survey_lines'):
            try:
                self.workbench.focus_all_survey_lines()
            except (TypeError, ValueError, KeyError):
                pass

    def _refresh_preview(self, plan):
        self._clear_preview()
        valid = [candidate for candidate in plan.get('candidates') or [] if candidate.get('valid')]
        invalid = [candidate for candidate in plan.get('candidates') or [] if not candidate.get('valid')]
        self._add_dashed(PREVIEW_VALID, [candidate['workspace']['line'] for candidate in valid], '#4da3ff')
        self._add_dashed(PREVIEW_INVALID, [candidate['workspace']['line'] for candidate in invalid], '#ff5a5a')
        try:
            self.workbench.plotter.render()
        except Exception:
            pass

    def _add_dashed(self, name, lines, color):
        if not lines:
            return
        terrain = self.workbench.terrain
        try:
            origin = np.asarray(terrain.origin, float).ravel()[:3]
        except (TypeError, ValueError, AttributeError):
            origin = np.zeros(3)
        points = []
        connectivity = []
        for line in lines:
            try:
                _, xy = stations_along(line, 97)
                xy = np.asarray(xy, float)
            except (TypeError, ValueError, IndexError):
                continue
            if len(xy) < 2:
                continue
            try:
                z = np.asarray(terrain.height(xy), float).ravel()
            except (TypeError, ValueError, RuntimeError):
                z = np.full(len(xy), np.nan)
            if z.size != len(xy):
                z = np.full(len(xy), np.nan)
            if not np.isfinite(z).all():
                fallback = _terrain_fallback_z(terrain)
                z = np.where(np.isfinite(z), z, fallback)
            xyz = np.column_stack([xy, z + .8]) - origin
            for start in range(0, len(xyz) - 1, 8):
                segment = xyz[start:start + 5]
                if len(segment) < 2:
                    break
                base = len(points)
                points.extend(segment.tolist())
                for index in range(len(segment) - 1):
                    connectivity.extend([2, base + index, base + index + 1])
        if not points:
            return
        try:
            mesh = pv.PolyData(np.asarray(points, float),
                               lines=np.asarray(connectivity, dtype=np.int64))
            self.workbench.plotter.add_mesh(mesh, color=color, line_width=3, pickable=False,
                                         reset_camera=False, render=False, name=name)
            self._preview_names.add(name)
        except Exception:
            return

    def _clear_preview(self):
        for name in list(self._preview_names):
            try:
                self.workbench.plotter.remove_actor(name, render=False)
            except TypeError:
                try:
                    self.workbench.plotter.remove_actor(name)
                except Exception:
                    pass
            except Exception:
                pass
        self._preview_names.clear()
        try:
            self.workbench.plotter.render()
        except Exception:
            pass

    def _restore_camera(self):
        if self._camera is None and self._full_camera is None:
            return
        try:
            if self._full_camera is not None:self.workbench.plotter.camera.DeepCopy(self._full_camera)
            else:self.workbench.plotter.camera_position = self._camera
            self.workbench.plotter.render()
        except Exception:
            pass

    def _confirm(self):
        if self.plan is None:
            return
        try:
            self.workbench.apply_quick_parallel_plan(self.plan)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.error_label.setPlainText(str(error))
            self.error_label.setVisible(True)
            self.summary.setText('未生成：请核对当前基准与已有测线，取消后重新打开预览。')
            self.confirm_button.setEnabled(False)
            return
        self._clear_preview()
        self.accept()

    def _finished(self, result):
        self._clear_preview()
        if result != Q.QDialog.Accepted:
            self._restore_camera()

    def cleanup(self):
        self._clear_preview()
        if self.result() != Q.QDialog.Accepted:
            self._restore_camera()
