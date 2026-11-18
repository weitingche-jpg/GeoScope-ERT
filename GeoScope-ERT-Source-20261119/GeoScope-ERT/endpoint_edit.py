"""Manual XYZ editing for the active survey line endpoints.

The line itself stays a two-dimensional vertex array; an explicit override
record carries the elevation that cannot be represented in ``line``.  Overrides
are only effective while the corresponding line vertex still has the recorded
X/Y, so a later drag/calibration can never inherit an old manual Z.
"""
import copy
import hashlib
import json
import math
import numpy as np
from PySide6 import QtWidgets as Q
from core import stations_along
from shortcuts import edit_snapshot
from workflow_geometry import fit_map_line, is_map_line_binding

HEIGHT_TERRAIN = '贴地高程'
HEIGHT_MANUAL = '手动高程'
HISTORY_LABEL = '编辑终点坐标'
OVERRIDE_TOLERANCE = 1e-6


def _number(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} 必须是数值。') from None
    if not math.isfinite(value):
        raise ValueError(f'{label} 必须是有限数值，不能含 NaN 或 Inf。')
    return 0.0 if value == 0 else value


def _mode(record):
    raw = record.get('height_mode')
    if raw in ('terrain', 'manual'):
        return raw
    source = str(record.get('elevation_source') or record.get('source') or '')
    if source in ('terrain', 'point'):
        return 'terrain' if source == 'terrain' else 'manual'
    if HEIGHT_TERRAIN in source or '地形' in source or '贴地' in source:
        return 'terrain'
    return 'manual'


def normalize_endpoint(record):
    """Return a canonical override record or ``None`` when unusable.

    Invalid values are ignored for rendering/signatures instead of raising so
    that an old or hand-edited project can still be opened and inspected.
    """
    if not isinstance(record, dict):
        return None
    try:
        x = _number(record['x'], 'X')
        y = _number(record['y'], 'Y')
        z = _number(record['z'], 'Z')
    except (KeyError, ValueError, TypeError):
        return None
    mode = _mode(record)
    source = HEIGHT_TERRAIN if mode == 'terrain' else HEIGHT_MANUAL
    return dict(x=x, y=y, z=z, elevation_source=source, height_mode=mode)


def effective_endpoint_override(line, overrides, index):
    """Return the override only when it still matches that line vertex X/Y."""
    if index not in (0, 1) or not isinstance(overrides, dict):
        return None
    vertices = list(line or [])
    if len(vertices) < 2:
        return None
    record = normalize_endpoint(overrides.get(str(index)))
    if record is None:
        return None
    try:
        vertex = vertices[0] if index == 0 else vertices[-1]
        vx, vy = float(vertex[0]), float(vertex[1])
    except (IndexError, TypeError, ValueError):
        return None
    if abs(record['x'] - vx) > OVERRIDE_TOLERANCE or abs(record['y'] - vy) > OVERRIDE_TOLERANCE:
        return None
    return record


def effective_endpoint_overrides(line, overrides):
    """Normalized effective overrides; empty dict means legacy signature."""
    result = {}
    for index in (0, 1):
        record = effective_endpoint_override(line, overrides, index)
        if record is not None:
            result[str(index)] = dict(x=record['x'], y=record['y'], z=record['z'])
    return result


def prune_endpoint_overrides(line, overrides):
    """Drop stale entries so old manual Z cannot revive on new geometry."""
    kept = {}
    if not isinstance(overrides, dict):
        return kept
    for key, value in overrides.items():
        try:
            edge = int(key)
        except (TypeError, ValueError):
            continue
        if edge in (0, 1) and effective_endpoint_override(line, {str(edge): value}, edge) is not None:
            kept[str(edge)] = value
    return kept


def endpoint_geometry_signature(points, line, overrides=None):
    """2-D result signature, compatible with the old call when no override."""
    effective = effective_endpoint_overrides(line, overrides or {})
    if not effective:
        from observed_ert import geometry_signature
        return geometry_signature(points, line)
    payload = dict(
        points=[{key: p[key] for key in ('id', 'x', 'y', 'z')} for p in points],
        line=line,
        endpoint_overrides=effective)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def endpoint_overrides_conflict(line,overrides,points,tolerance=.05):
    """A display endpoint cannot replace a different physical electrode XYZ."""
    for key,record in effective_endpoint_overrides(line,overrides).items():
        if not points:return True
        try:
            point=points[0 if key=='0' else -1]
            if not np.allclose([record[k] for k in ('x','y','z')],
                               [point[k] for k in ('x','y','z')],atol=tolerance,rtol=0):return True
        except (IndexError,KeyError,TypeError,ValueError):return True
    return False


def endpoint_result_is_stale(line,overrides,section_data,tolerance=.05):
    """Compare edited end XYZ to the frozen profile surface, not display flags."""
    records=effective_endpoint_overrides(line,overrides)
    if not records or section_data is None:return False
    try:
        for key,record in records.items():
            index=0 if key=='0' else -1
            actual=np.r_[section_data['xy'][index],section_data['elevation'][index]]
            if not np.allclose([record[k] for k in ('x','y','z')],actual,atol=tolerance,rtol=0):return True
    except (IndexError,KeyError,TypeError,ValueError):return True
    return False


def line_render_xyz(terrain, line, overrides=None, count=150):
    """Line samples in world coordinates with true endpoint Z when overridden.

    Interior samples (including interior vertices of a multi-vertex line) keep
    following the terrain; only the two line ends may carry manual elevation.
    """
    if terrain is None or len(line or []) < 2:
        return None
    try:
        _, xy = stations_along([list(v) for v in line], count)
        xy = np.asarray(xy, float)
        z = np.asarray(terrain.height(xy), float)
        if len(z) != len(xy) or not np.isfinite(xy).all() or not np.isfinite(z).all():
            return None
        xyz = np.column_stack([xy, z])
        start = effective_endpoint_override(line, overrides or {}, 0)
        end = effective_endpoint_override(line, overrides or {}, 1)
        manual_height=any(record is not None and record['height_mode']=='manual'
                          and not np.isclose(record['z'],z[index],atol=1e-6,rtol=0)
                          for record,index in ((start,0),(end,-1)))
        if len(line) == 2 and manual_height:
            # A straight line with an explicit endpoint elevation is a 3-D
            # segment between the two true endpoints; do not put a one-sample
            # ground spike next to the manual endpoint.
            start_z = start['z'] if start is not None else float(z[0])
            end_z = end['z'] if end is not None else float(z[-1])
            xyz[:, 2] = np.linspace(start_z, end_z, len(xyz))
        else:
            if start is not None:
                xyz[0, 2] = start['z']
            if end is not None:
                xyz[-1, 2] = end['z']
        return xyz
    except (IndexError, TypeError, ValueError, RuntimeError):
        return None


def validate_endpoint(window, index, record):
    """Validate and normalize one endpoint edit without mutating the window."""
    if index not in (0, 1):
        raise ValueError('只支持编辑测线起点或终点。')
    if getattr(window, 'terrain', None) is None:
        raise ValueError('尚未加载地形，不能编辑测线端点。')
    line = list(getattr(window, 'line', []) or [])
    if len(line) < 2:
        raise ValueError('当前没有完整测线，不能编辑测线端点。')
    if getattr(window, 'process', None) is not None or (
            hasattr(window, 'survey_busy') and window.survey_busy()):
        raise ValueError('计算或测线批次进行中，暂不能编辑端点。')
    if hasattr(window, 'require_committed_edit') and not window.require_committed_edit():
        raise ValueError('请先完成当前拖动或绘线，再编辑端点。')
    if not isinstance(record, dict):
        raise ValueError('端点记录格式无效。')
    x = _number(record.get('x'), 'X')
    y = _number(record.get('y'), 'Y')
    mode = _mode(record)
    raw_z = record.get('z')
    z = None if raw_z is None and mode == 'terrain' else _number(raw_z, 'Z')
    terrain = window.terrain
    xs = np.asarray(getattr(terrain, 'x', []), float)
    ys = np.asarray(getattr(terrain, 'y', []), float)
    if xs.size == 0 or ys.size == 0:
        raise ValueError('地形范围无效。')
    if not (float(np.nanmin(xs)) <= x <= float(np.nanmax(xs)) and
            float(np.nanmin(ys)) <= y <= float(np.nanmax(ys))):
        raise ValueError('端点超出地形范围。')
    ground = np.asarray(terrain.height([[x, y]]), float)
    if len(ground) != 1 or not np.isfinite(ground[0]):
        raise ValueError('端点位置没有有效地形高程。')
    if mode == 'terrain':
        z = float(ground[0])
    elif not math.isfinite(z):
        raise ValueError('手动高程必须是有限数值。')
    candidate = [list(v) for v in line]
    candidate[0 if index == 0 else -1] = [x, y]
    try:
        other = candidate[-1] if index == 0 else candidate[0]
        if math.hypot(x - float(other[0]), y - float(other[1])) <= OVERRIDE_TOLERANCE:
            relayout_map_electrodes(window, candidate)
            raise ValueError('终点不能与另一端点重合。')
        _, sample = stations_along(candidate, 150)
        heights = np.asarray(terrain.height(sample), float)
        if len(heights) != len(sample) or not np.isfinite(heights).all():
            raise ValueError('测线经过地形空值区域，不能提交该端点。')
    except ValueError:
        raise
    source = HEIGHT_TERRAIN if mode == 'terrain' else HEIGHT_MANUAL
    return dict(x=x, y=y, z=float(z), elevation_source=source, height_mode=mode)


def relayout_map_electrodes(window, line):
    """Prepare the new geometry before any workspace or history change."""
    packet = getattr(window, 'ert_packet', None)
    if not is_map_line_binding(packet):
        return None
    binding, points = fit_map_line(packet, line, window.terrain)
    return {**packet, 'binding': binding}, points


class EndpointEditMixin:
    """Window methods shared by the real application and headless tests."""

    def init_endpoint_edit(self):
        if not isinstance(getattr(self, 'line_endpoint_overrides', None), dict):
            self.line_endpoint_overrides = {}

    def line_name_for_endpoint(self):
        try:
            line = self.active_survey_line()
        except (AttributeError, ValueError, KeyError):
            line = None
        return line.get('name') if isinstance(line, dict) else '当前测线'

    def generate_line_from_coordinates(self):
        if self.terrain is None or self._local_acquisition():
            self.say('请先加载地形，并选择地图测线工作区。')
            return False
        if self.process is not None or self.survey_busy() or not self.require_committed_edit():
            return False
        dialog = Q.QDialog(self)
        dialog.setWindowTitle('生成测线')
        form = Q.QFormLayout(dialog)
        form.addRow(Q.QLabel('输入地形坐标系中的起点和终点 X/Y（米）。'))
        if len(self.line) == 2:
            defaults = self.line
        else:
            xmin, xmax = float(min(self.terrain.x)), float(max(self.terrain.x))
            y = float(np.mean(self.terrain.y))
            defaults = [[xmin + .4*(xmax-xmin), y], [xmin + .6*(xmax-xmin), y]]
        fields = []
        for index, (label, default) in enumerate(zip(('起点', '终点'), defaults)):
            row = Q.QHBoxLayout()
            pair = []
            for axis, value in zip(('X', 'Y'), default):
                field = Q.QDoubleSpinBox(dialog)
                field.setObjectName(f'generate-line-{index}-{axis.lower()}')
                field.setRange(-1e10, 1e10)
                field.setDecimals(4)
                field.setValue(value)
                row.addWidget(Q.QLabel(axis)); row.addWidget(field)
                pair.append(field)
            form.addRow(label, row)
            fields.append(pair)
        problem = Q.QLabel()
        problem.setWordWrap(True)
        form.addRow(problem)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        buttons.button(Q.QDialogButtonBox.Ok).setText('生成测线')
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消')
        form.addRow(buttons)

        def commit():
            line = [[field.value() for field in pair] for pair in fields]
            try:
                relayout = relayout_map_electrodes(self, line)
                if math.dist(*line) <= OVERRIDE_TOLERANCE:
                    raise ValueError('起点与终点不能重合。')
                _, sample = stations_along(line, 150)
                if not np.isfinite(self.terrain.height(sample)).all():
                    raise ValueError('测线超出地形范围或经过无有效高程的区域。')
            except ValueError as error:
                problem.setText(str(error))
                return
            before = edit_snapshot(self)
            self.line = line
            self.line_endpoint_overrides = {}
            self.points_follow_line = False
            if relayout:
                self.ert_packet, self.points = relayout
            self.record_edit(before, '按坐标生成测线')
            self.set_mode('browse')
            dialog.accept()
            self.say('测线与电极已更新。' if relayout else '测线已生成，可导入观测并沿线布点。')

        buttons.accepted.connect(commit)
        buttons.rejected.connect(dialog.reject)
        try:
            return dialog.exec() == Q.QDialog.Accepted
        finally:
            dialog.deleteLater()

    def edit_endpoint_coordinates(self, index=1):
        return self.open_endpoint_edit_dialog(index)

    def open_endpoint_edit_dialog(self, index=1):
        if index not in (0, 1):
            return False
        if getattr(self, 'process', None) is not None or (
                hasattr(self, 'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，暂不能编辑端点坐标。')
            return False
        if hasattr(self, 'require_committed_edit') and not self.require_committed_edit():
            return False
        if getattr(self, 'terrain', None) is None or len(getattr(self, 'line', []) or []) < 2:
            self.say('当前没有完整测线，无法编辑终点坐标。')
            return False
        records = self.line_endpoint_records() if hasattr(self, 'line_endpoint_records') else []
        if len(records) < 2:
            self.say('当前测线端点没有有效地形位置，无法编辑终点坐标。')
            return False
        def validate_candidate(record):
            normalized = validate_endpoint(self, index, record)
            line = [list(point) for point in self.line]
            line[0 if index == 0 else -1] = [normalized['x'], normalized['y']]
            relayout_map_electrodes(self, line)
            return normalized

        dialog = EndpointEditDialog(
            self, index, records[index], self.terrain, self.line,
            line_name=self.line_name_for_endpoint(),
            validator=validate_candidate)
        try:
            if dialog.exec() != Q.QDialog.Accepted:
                return False
            record = dialog.record()
        finally:
            dialog.deleteLater()
        try:
            return self.commit_line_endpoint(index, record)
        except (ValueError, KeyError, TypeError) as error:
            self.say('终点坐标未提交：' + str(error))
            return False

    def commit_line_endpoint(self, index, record):
        """Atomically edit the active line end and record one history step."""
        if index not in (0, 1):
            raise ValueError('只支持编辑测线起点或终点。')
        normalized = validate_endpoint(self, index, record)
        before = edit_snapshot(self)
        line = [list(v) for v in self.line]
        line[0 if index == 0 else -1] = [normalized['x'], normalized['y']]
        overrides = copy.deepcopy(getattr(self, 'line_endpoint_overrides', {}) or {})
        overrides[str(index)] = normalized
        # Drop entries that no longer match a vertex (old manual Z must not be
        # silently re-used by a programmatic geometry change).
        overrides = prune_endpoint_overrides(line, overrides)
        packet = getattr(self, 'ert_packet', None)
        relayout = relayout_map_electrodes(self, line)
        changed_packet = None
        if relayout:
            changed_packet, new_points = relayout
        elif isinstance(packet, dict):
            binding = packet.get('binding') or {}
            if binding.get('mode') not in (None, 'local'):
                try:
                    from workflow_geometry import validate_binding
                    errors = validate_binding(packet, binding, self.points, line, self.terrain)
                except Exception:
                    errors = ['配准验证失败']
                if errors:
                    changed_packet = copy.deepcopy(packet)
                    changed_packet['binding'] = {'mode': 'local','endpoint_edit_preserve_pose':True}
        old = (self.line, getattr(self, 'line_endpoint_overrides', {}),
               getattr(self, 'ert_packet', None), getattr(self, 'points_follow_line', False), self.points)
        self.line = line
        self.line_endpoint_overrides = overrides
        self.points_follow_line = False
        if changed_packet is not None:
            self.ert_packet = changed_packet
        if relayout:
            self.points = new_points
        try:
            self.record_edit(before, HISTORY_LABEL)
        except Exception:
            (self.line, self.line_endpoint_overrides, self.ert_packet,
             self.points_follow_line, self.points) = old
            raise
        if hasattr(self, 'redraw'):
            self.redraw()
        if hasattr(self, 'select_line_endpoint'):
            self.select_line_endpoint(index)
        if hasattr(self, 'say'):
            mode = HEIGHT_TERRAIN if normalized['height_mode'] == 'terrain' else HEIGHT_MANUAL
            update = '电极已按文件间距重新布置。' if relayout else '电极与原始观测未改动。'
            self.say(f"测线端点已更新为 X {normalized['x']:.2f} · Y {normalized['y']:.2f} · "
                     f"Z {normalized['z']:.2f} m（{mode}）；{update}")
        return True


class EndpointEditDialog(Q.QDialog):
    """Small modal XYZ editor; object names are kept stable for acceptance."""

    def __init__(self, parent, index, record, terrain, line, line_name='当前测线', validator=None):
        super().__init__(parent)
        self.index = int(index)
        self.terrain = terrain
        self.line = [list(v) for v in line]
        self.line_name = line_name
        self.validator = validator
        self._record = None
        self.setObjectName('endpoint-edit-dialog')
        self.setWindowTitle('编辑终点坐标' if self.index else '编辑起点坐标')
        self.setModal(True)
        layout = Q.QVBoxLayout(self)
        title = Q.QLabel(('终点 B' if self.index else '起点 A') + f' · {self.line_name}')
        title.setObjectName('endpoint-target')
        layout.addWidget(title)
        original = Q.QLabel(
            f"原值：X {float(record['x']):.3f} · Y {float(record['y']):.3f} · Z {float(record['z']):.3f} m")
        original.setObjectName('endpoint-original')
        original.setStyleSheet('color:#9aaabe;')
        layout.addWidget(original)
        form = Q.QFormLayout()
        self.x = self._spin('endpoint-x')
        self.y = self._spin('endpoint-y')
        self.z = self._spin('endpoint-z')
        self.height_mode = Q.QComboBox()
        self.height_mode.setObjectName('endpoint-height-mode')
        self.height_mode.addItem(HEIGHT_TERRAIN, 'terrain')
        self.height_mode.addItem(HEIGHT_MANUAL, 'manual')
        form.addRow('X (m)', self.x)
        form.addRow('Y (m)', self.y)
        form.addRow('Z / 高程 (m)', self.z)
        form.addRow('高程模式', self.height_mode)
        layout.addLayout(form)
        self.error = Q.QLabel('')
        self.error.setObjectName('endpoint-error')
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#f39c91;')
        self.error.hide()
        layout.addWidget(self.error)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(Q.QDialogButtonBox.Ok).setText('确认')
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消')
        layout.addWidget(buttons)
        self.x.setValue(float(record['x']))
        self.y.setValue(float(record['y']))
        self.z.setValue(float(record['z']))
        mode = _mode(record)
        self.height_mode.setCurrentIndex(0 if mode == 'terrain' else 1)
        self._apply_mode(initial=True)
        self.height_mode.currentIndexChanged.connect(self._mode_changed)
        self.x.valueChanged.connect(self._xy_changed)
        self.y.valueChanged.connect(self._xy_changed)

    @staticmethod
    def _spin(name):
        field = Q.QDoubleSpinBox()
        field.setObjectName(name)
        field.setRange(-1e12, 1e12)
        field.setDecimals(6)
        field.setSingleStep(0.1)
        field.setKeyboardTracking(False)
        return field

    def _ground_z(self):
        try:
            values = np.asarray(self.terrain.height([[self.x.value(), self.y.value()]]), float)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return None
        return float(values[0]) if len(values) == 1 and np.isfinite(values[0]) else None

    def _apply_mode(self, initial=False):
        terrain_mode = self.height_mode.currentData() == 'terrain'
        self.z.setEnabled(not terrain_mode)
        if terrain_mode:
            ground = self._ground_z()
            if ground is not None:
                self.z.setValue(ground)

    def _mode_changed(self, _index=0):
        self._apply_mode()
        self.error.hide()

    def _xy_changed(self, _value=0.0):
        if self.height_mode.currentData() == 'terrain':
            self._apply_mode()
        self.error.hide()

    def _build_record(self):
        return dict(x=self.x.value(), y=self.y.value(), z=self.z.value(),
                    height_mode=self.height_mode.currentData(),
                    elevation_source=HEIGHT_TERRAIN if self.height_mode.currentData() == 'terrain'
                    else HEIGHT_MANUAL)

    def accept(self):
        record = self._build_record()
        try:
            if self.validator is not None:
                normalized = self.validator(record)
            else:
                normalized = normalize_endpoint(record)
                if normalized is None:
                    raise ValueError('端点坐标无效。')
        except (ValueError, KeyError, TypeError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self._record = normalized
        self.error.hide()
        super().accept()

    def record(self):
        return copy.deepcopy(self._record)
