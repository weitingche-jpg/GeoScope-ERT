"""Qt dialog and workflow mixin for one-click calibration and registration."""
import copy
import math

import numpy as np
from PySide6 import QtWidgets as Q

from calibration import inspect_layout, make_proposal
from shortcuts import edit_snapshot
from workflow_geometry import electrode_ids, validate_binding


PREVIEW_ORIGINAL = 'calibration-preview-original'
PREVIEW_ORIGINAL_LINE = 'calibration-preview-original-line'
PREVIEW_NEW = 'calibration-preview-new'
PREVIEW_NEW_LINE = 'calibration-preview-new-line'
PREVIEW_NAMES = (PREVIEW_ORIGINAL, PREVIEW_ORIGINAL_LINE,
                 PREVIEW_NEW, PREVIEW_NEW_LINE)

REBUILD_TEXT = '按观测重建布设（推荐）'
MANUAL_TEXT = '保留坐标手动配准'
LOCAL_TEXT = '仅局部二维'
ACTIONS = ('rebuild', 'manual', 'local')


def _format_number(value):
    try:
        return f'{float(value):.10g}'
    except (TypeError, ValueError):
        return str(value)


def _set_visible(widget, visible):
    try:
        widget.setVisible(visible)
    except AttributeError:
        (widget.show if visible else widget.hide)()


class CalibrationDialog(Q.QDialog):
    """Preview a metric rebuild or hand the chosen action back to the mixin."""

    def __init__(self, owner):
        parent = owner if isinstance(owner, Q.QWidget) else None
        super().__init__(parent)
        self.owner = owner
        self.packet = copy.deepcopy(getattr(owner, 'ert_packet', None))
        self.original_points = copy.deepcopy(getattr(owner, 'points', []))
        self.original_line = copy.deepcopy(getattr(owner, 'line', []))
        self.proposal = None
        self.terrain = getattr(owner, 'terrain', None)
        self._direction_valid = False
        self._angle_edited = False
        self._last_inspection = None
        self.setWindowTitle('一键校核与配准')
        self.resize(980, 680)

        layout = Q.QVBoxLayout(self)
        self.summary = Q.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.note = Q.QLabel('以下仅核对观测几何与当前布设的匹配关系，不是测绘精度证明；'
                             '不修改真实观测，也不缩放电极间距。')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self._build_inspection()
        layout.addWidget(self.inspection_panel)

        method_form = Q.QFormLayout()
        self.method = Q.QComboBox()
        self.method.addItems([REBUILD_TEXT, MANUAL_TEXT, LOCAL_TEXT])
        method_form.addRow('处理方式', self.method)
        layout.addLayout(method_form)

        self._build_start_fields()
        self._build_controls()

        self.preview_table = Q.QTableWidget(0, 8)
        self.preview_table.setHorizontalHeaderLabels(
            ['电极', '原 X', '原 Y', '原 Z', '新 X', '新 Y', '新 Z', '状态'])
        self.preview_table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(Q.QAbstractItemView.SelectRows)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        layout.addWidget(self.preview_table, 1)

        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        self.ok = buttons.button(Q.QDialogButtonBox.Ok)
        self.ok.setStyleSheet('QPushButton {background:#428af0; color:white; border:0; font-weight:600;}'
            'QPushButton:hover {background:#5a9cf6;}')
        self.cancel = buttons.button(Q.QDialogButtonBox.Cancel)
        self.cancel.setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._prefill_from_current_line()
        self._method_changed(self.method.currentIndex())
        self.method.currentIndexChanged.connect(self._method_changed)

        inspection = inspect_layout(self.packet, self.original_points,
                                    self.original_line, self.tolerance.value())
        self._last_inspection = inspection
        self.summary.setText(self._summary_text(inspection))
        self.requirement_label.setText(
            f"观测要求 {inspection['expected_count']} 个电极，间距 "
            f"{inspection['expected_spacing']:g} 米，总长 "
            f"{inspection['expected_length']:g} 米；当前 "
            f"{inspection['actual_count']} 个测点，线长 "
            f"{inspection['actual_length']:g} 米。")
        self.conflict_label.setText(
            ('冲突 ' + str(len(inspection['issues'])) + ' 条：'
             + '；'.join(inspection['issues'])) if inspection['issues']
            else '当前未发现几何冲突。')
        if inspection['has_file_coordinates']:
            self.method.setCurrentIndex(1)
        else:
            self.method.setCurrentIndex(0)
        self._method_changed(self.method.currentIndex())

    def _build_inspection(self):
        self.inspection_panel = Q.QFrame()
        box = Q.QVBoxLayout(self.inspection_panel)
        self.requirement_label = Q.QLabel()
        self.requirement_label.setWordWrap(True)
        self.requirement_label.hide()
        self.conflict_label = Q.QTextEdit()
        self.conflict_label.setReadOnly(True)
        self.conflict_label.setFixedHeight(76)
        box.addWidget(self.requirement_label)
        box.addWidget(self.conflict_label)

    def _build_start_fields(self):
        group = Q.QGroupBox('重建起点与方向')
        form = Q.QGridLayout(group)
        self.start_fields = [Q.QLineEdit(), Q.QLineEdit(), Q.QLineEdit()]
        for i,(label, field) in enumerate(zip(('起点 X / m', '起点 Y / m', '起点 Z / m'),
                                self.start_fields)):
            field.setPlaceholderText('未从当前测线取得，请输入')
            form.addWidget(Q.QLabel(label),0,i*2);form.addWidget(field,0,i*2+1)
        self.angle = Q.QDoubleSpinBox()
        self.angle.setDecimals(3)
        self.angle.setRange(-360., 360.)
        self.angle.setWrapping(True)
        self.angle.setSuffix(' °')
        self.angle.setToolTip('从 +X 轴起逆时针为正')
        self.angle.valueChanged.connect(self._angle_changed)
        form.addWidget(Q.QLabel('方向角（+X 起逆时针）'),1,0,1,2);form.addWidget(self.angle,1,2)
        self.tolerance = Q.QDoubleSpinBox()
        self.tolerance.setDecimals(3)
        self.tolerance.setRange(.001, 10.)
        self.tolerance.setValue(.1)
        self.tolerance.setSuffix(' m')
        form.addWidget(Q.QLabel('几何校核容差'),1,3);form.addWidget(self.tolerance,1,4,1,2)
        self._start_group = group
        self.layout().addWidget(group)

    def _build_controls(self):
        row = Q.QHBoxLayout()
        self.preview_button = Q.QPushButton('计算重建预览')
        self.preview_button.clicked.connect(self.calculate)
        row.addWidget(self.preview_button)
        row.addStretch()
        self.status = Q.QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self._controls_layout = row
        # Insert the control row before the preview table.
        layout = self.layout()
        layout.addLayout(row)
        self.confirm = Q.QCheckBox(
            '按观测重建当前布设；旧布设备份随项目保存，可撤销')
        self.confirm.setChecked(False)
        self.confirm.setStyleSheet('QCheckBox {spacing:9px;}'
            'QCheckBox::indicator {width:20px; height:20px; border:2px solid #a5b8ce; border-radius:3px; background:#0b121b;}'
            'QCheckBox::indicator:checked {background:#428af0; border-color:#c6ddff;}')
        layout.addWidget(self.confirm)

    @property
    def action(self):
        index = self.method.currentIndex()
        return ACTIONS[index] if 0 <= index < len(ACTIONS) else 'rebuild'

    @action.setter
    def action(self, value):
        if value not in ACTIONS:
            raise ValueError('校核方式须为 rebuild、manual 或 local。')
        self.method.setCurrentIndex(ACTIONS.index(value))

    def _summary_text(self, inspection):
        lines = [
            f"观测要求：{inspection['expected_count']} 个电极 · "
            f"间距 {inspection['expected_spacing']:g} 米 · "
            f"总长 {inspection['expected_length']:g} 米",
            f"当前布设：{inspection['actual_count']} 个测点 · "
            f"线长 {inspection['actual_length']:g} 米 · "
            f"冲突 {len(inspection['issues'])} 条",
        ]
        return '\n'.join(lines)

    def _angle_changed(self, value):
        self._angle_edited = True
        self.status.setText('已输入方向角；仍请核对起点和确认选项。')

    def _prefill_from_current_line(self):
        for field in self.start_fields:
            field.setText('')
        self.angle.setValue(0.)
        self._angle_edited = False
        self._direction_valid = False
        line = self.original_line
        if not isinstance(line, (list, tuple)) or len(line) < 2 or self.terrain is None:
            self.status.setText('当前没有完整测线：请输入起点和方向，并确认后重建。')
            return
        try:
            first = [float(line[0][0]), float(line[0][1])]
            last = [float(line[-1][0]), float(line[-1][1])]
            if not all(math.isfinite(value) for value in first + last):
                raise ValueError('当前测线坐标不是有限数值。')
            height = float(self.terrain.height([first])[0])
            if not math.isfinite(height):
                raise ValueError('当前测线起点的高程无效。')
        except Exception as error:
            self.status.setText(f'当前测线不可用：{error}。请输入起点和方向。')
            return
        self.start_fields[0].setText(_format_number(first[0]))
        self.start_fields[1].setText(_format_number(first[1]))
        self.start_fields[2].setText(_format_number(height))
        dx, dy = last[0] - first[0], last[1] - first[1]
        if math.hypot(dx, dy) > 0:
            self.angle.setValue(math.degrees(math.atan2(dy, dx)))
            self._direction_valid = True
        else:
            self.status.setText('当前测线方向无效（端点重合）：请手动输入方向角后再重建。')

    def _method_changed(self, index):
        if not hasattr(self, 'ok'):
            return
        action = ACTIONS[index] if 0 <= index < len(ACTIONS) else 'rebuild'
        self.ok.setText({'rebuild': '校核并应用重建',
                         'manual': '应用手动配准',
                         'local': '应用仅局部二维'}[action])
        self._start_group.setEnabled(action == 'rebuild')
        self.preview_button.setEnabled(action == 'rebuild')
        self.confirm.setEnabled(action == 'rebuild')
        if action != 'rebuild':
            self.proposal = None
            self.clear_preview()
            self.status.setText(
                '已选择“保留坐标手动配准”：将使用当前测点坐标。' if action == 'manual'
                else '已选择“仅局部二维”：不会把测点关联到地形。')

    def _parse_start(self):
        values = []
        for axis, field in zip(('X', 'Y', 'Z'), self.start_fields):
            text = field.text().strip()
            if not text:
                raise ValueError(f'请输入起点 {axis} 坐标；不会默认使用校园中心。')
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f'起点 {axis} 坐标不是有效数值。') from None
            if not math.isfinite(value):
                raise ValueError(f'起点 {axis} 坐标必须是有限数值。')
            values.append(value)
        return values

    def calculate(self):
        try:
            if self.action != 'rebuild':
                raise ValueError('当前方式不生成重建预览，请直接用应用按钮。')
            start = self._parse_start()
            if (not self._direction_valid and self.original_line
                    and len(self.original_line) >= 2 and not self._angle_edited):
                raise ValueError('当前测线没有有效方向，请手动输入方向角后再校核；'
                                 '不会静默使用默认方向。')
            proposal = make_proposal(self.packet, start, self.angle.value(),
                                     self.terrain, self.tolerance.value())
            self.clear_preview()
            self.proposal = proposal
            self._fill_preview_table(proposal)
            self._draw_preview(proposal)
            self.status.setText(
                f"几何匹配通过：保持 {proposal['binding']['length']:g} 米总长、"
                f"间距 {self.packet['spacing']:g} 米、{self.packet['electrodes']} 个电极；"
                '未修改观测。几何匹配不等于测绘精度证明。')
            return True
        except Exception as error:
            self.proposal = None
            self.clear_preview()
            self.status.setText(str(error))
            return False

    def _fill_preview_table(self, proposal):
        expected = electrode_ids(self.packet)
        old_by_id = {}
        for point in self.original_points:
            if isinstance(point, dict) and point.get('id') is not None:
                old_by_id.setdefault(point['id'], point)
        new_by_id = {}
        for point in proposal['points']:
            new_by_id.setdefault(point['id'], point)
        order = list(expected)
        order.extend(identifier for identifier in old_by_id if identifier not in set(order))
        self.preview_table.setRowCount(len(order))
        for row, identifier in enumerate(order):
            old = old_by_id.get(identifier)
            new = new_by_id.get(identifier)
            values = [identifier]
            for point, keys in ((old, ('x', 'y', 'z')), (new, ('x', 'y', 'z'))):
                values.extend(_format_number(point.get(key)) if point else ''
                              for key in keys)
            status = (('保持' if all(abs(float(old[k])-float(new[k]))<1e-8 for k in ('x','y','z')) else '移位') if old and new else
                      '备份保留' if old else '新增')
            values.append(status)
            for column, value in enumerate(values):
                self.preview_table.setItem(row, column, Q.QTableWidgetItem(value))

    def _add_preview_points(self, xyz, color, name):
        plotter = getattr(self.owner, 'plotter', None)
        if plotter is None or not hasattr(plotter, 'add_points'):
            return
        try:
            plotter.add_points(xyz, color=color, point_size=8, name=name,
                               pickable=False, reset_camera=False)
        except TypeError:
            try:
                plotter.add_points(xyz, color=color, name=name)
            except Exception:
                pass
        except Exception:
            pass

    def _add_preview_line(self, endpoints, color, name):
        plotter = getattr(self.owner, 'plotter', None)
        if plotter is None or not hasattr(plotter, 'add_mesh'):
            return
        try:
            import pyvista as pv
            mesh = pv.lines_from_points(np.asarray(endpoints, dtype=float))
            plotter.add_mesh(mesh, color=color, line_width=3, name=name,
                             pickable=False, reset_camera=False)
        except Exception:
            pass

    def _plotter_origin(self):
        origin = getattr(self.terrain, 'origin', [0., 0., 0.])
        return np.asarray(origin, dtype=float)

    def _draw_preview(self, proposal):
        plotter = getattr(self.owner, 'plotter', None)
        if plotter is None:
            return
        origin = self._plotter_origin()
        old_xyz = []
        for point in self.original_points:
            try:
                old_xyz.append([float(point['x']), float(point['y']),
                                float(point['z']) + 1.])
            except (KeyError, TypeError, ValueError):
                continue
        if old_xyz:
            self._add_preview_points(np.asarray(old_xyz) - origin,
                                     '#8b95a5', PREVIEW_ORIGINAL)
        new_xyz = np.asarray([[point['x'], point['y'], point['z'] + 1.]
                              for point in proposal['points']], dtype=float)
        self._add_preview_points(new_xyz - origin, '#4da3ff', PREVIEW_NEW)

        def endpoints(line, fallback):
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                return None
            if fallback:
                return [[float(line[0][0]), float(line[0][1]), old_xyz[0][2]],
                        [float(line[-1][0]), float(line[-1][1]), old_xyz[-1][2]]]
            points = proposal['points']
            return [[float(line[0][0]), float(line[0][1]), points[0]['z'] + 1.],
                    [float(line[-1][0]), float(line[-1][1]), points[-1]['z'] + 1.]]

        old_line = endpoints(self.original_line, bool(old_xyz))
        if old_line is not None:
            self._add_preview_line(np.asarray(old_line) - origin,
                                   '#8b95a5', PREVIEW_ORIGINAL_LINE)
        new_line = endpoints(proposal['line'], False)
        if new_line is not None:
            self._add_preview_line(np.asarray(new_line) - origin,
                                   '#4da3ff', PREVIEW_NEW_LINE)

    def clear_preview(self):
        plotter = getattr(self.owner, 'plotter', None)
        if plotter is not None and hasattr(plotter, 'remove_actor'):
            for name in PREVIEW_NAMES:
                try:
                    plotter.remove_actor(name, reset_camera=False)
                except TypeError:
                    try:
                        plotter.remove_actor(name)
                    except Exception:
                        pass
                except Exception:
                    pass
        if hasattr(self, 'preview_table'):
            self.preview_table.setRowCount(0)

    def accept(self):
        action = self.action
        if action == 'rebuild':
            if not self.confirm.isChecked():
                self.status.setText('请先明确确认：按观测重建当前布设；'
                                    '旧布设备份随项目保存，可撤销。')
                return
            if not self.calculate() or self.proposal is None:
                return
        self.clear_preview()
        super().accept()

    def reject(self):
        self.proposal = None
        self.clear_preview()
        super().reject()

    def closeEvent(self, event):
        self.clear_preview()
        super().closeEvent(event)


class CalibrationWorkflow:
    """Shared workflow entry points; the main window supplies the UI."""

    def calibrate_observations(self):
        if getattr(self, 'terrain', None) is None or not getattr(self, 'ert_packet', None):
            self.say('请先加载地形并导入/新增观测。')
            return
        if getattr(self, 'process', None) is not None:
            self.say('计算进行中，请等待完成后再校核布设。')
            return
        if not self.require_committed_edit():
            return
        dialog = CalibrationDialog(self)
        if dialog.exec() != Q.QDialog.Accepted:
            return
        action = dialog.action
        if action == 'rebuild':
            if dialog.proposal is None:
                self.error(ValueError('未生成有效的重建方案，请重新校核。'))
                return
            try:
                self.apply_calibration_proposal(dialog.proposal)
            except Exception as error:
                self.error(error)
        elif action == 'manual':
            self.register_observations()
        elif action == 'local':
            self.use_local_observations()

    def apply_calibration_proposal(self, proposal):
        if not isinstance(proposal, dict):
            raise ValueError('校核方案无效。')
        if not isinstance(getattr(self, 'ert_packet', None), dict):
            raise ValueError('尚未导入观测，无法应用校核方案。')
        if getattr(self, 'terrain', None) is None:
            raise ValueError('请先加载地形。')
        binding = proposal.get('binding')
        points = proposal.get('points')
        line = proposal.get('line')
        if not isinstance(binding,dict) or binding.get('mode')!='registered':
            raise ValueError('仅可应用通过校核的已配准重建方案。')
        problems = validate_binding(self.ert_packet, binding, points, line, self.terrain)
        if problems:
            raise ValueError('\n'.join(problems))
        before = edit_snapshot(self)
        packet = copy.deepcopy(self.ert_packet)
        backup = dict(points=copy.deepcopy(self.points),
                      line=copy.deepcopy(self.line),
                      line_endpoint_overrides=copy.deepcopy(getattr(self,'line_endpoint_overrides',{})),
                      points_follow_line=getattr(self, 'points_follow_line', False))
        existing = packet.get('calibration_backup')
        if existing is None:
            backups = []
        elif isinstance(existing, list):
            backups = copy.deepcopy(existing)
        else:
            backups = [copy.deepcopy(existing)]
        backups.append(backup)
        if len(backups) > 5:
            # Keep the original first snapshot, then the four most recent.
            backups = [backups[0]] + backups[-4:]
        packet['calibration_backup'] = (backups if existing is not None else backup)
        self.points = copy.deepcopy(points)
        self.line = copy.deepcopy(line)
        self.line_endpoint_overrides = {}
        self.points_follow_line = False
        packet['binding'] = copy.deepcopy(binding)
        self.ert_packet = packet
        self.record_edit(before, '一键校核与配准')
        self.redraw()
        if hasattr(self, 'refresh_workflow'):
            self.refresh_workflow()
        if hasattr(self, 'refresh_calibration_notice'):
            self.refresh_calibration_notice()

    def refresh_calibration_notice(self):
        notice = getattr(self, 'calibration_notice', None)
        if notice is None:
            return
        if not getattr(self, 'ert_packet', None) or getattr(self, 'terrain', None) is None:
            _set_visible(notice, False)
            return
        try:
            inspection = inspect_layout(self.ert_packet, self.points, self.line)
        except Exception as error:
            notice.setText(f'校核信息不可用：{error}')
            _set_visible(notice, True)
            return
        packet = self.ert_packet
        binding = packet.get('binding') if isinstance(packet, dict) else None
        lines = [
            f"观测要求：{inspection['expected_count']} 个电极 · "
            f"间距 {inspection['expected_spacing']:g} 米 · "
            f"总长 {inspection['expected_length']:g} 米",
            f"当前：{inspection['actual_count']} 个测点 · "
            f"线长 {inspection['actual_length']:g} 米 · "
            f"冲突 {len(inspection['issues'])} 条",
        ]
        if binding and binding.get('mode') == 'registered':
            problems = validate_binding(packet, binding, self.points, self.line,
                                        self.terrain)
            if problems:
                lines.insert(0, '当前布设未配准（校验未通过），请重新校核；旧剖面保留原定位')
            else:
                lines.insert(0, '已配准')
        else:
            lines.insert(0, '未配准：地下结果仅二维显示，点击一键校核与配准')
        notice.setText('\n'.join(lines))
        _set_visible(notice, True)
