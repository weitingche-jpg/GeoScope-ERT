"""Atomic, explicit entry and editing of observed ERT rows."""
import copy
import hashlib
import math
from uuid import uuid4

from PySide6 import QtWidgets as Q

from observed_ert import validate_packet
from workflow_geometry import electrode_ids
from ert_arrays import array_label, normalize_array


def make_manual_packet(points, line, array='wenner'):
    """Create an empty local record; the editor validates its first observation."""
    array = normalize_array(array)
    count = len(points) if len(points) >= 4 else 24
    spacing = 2.
    try:
        if len(line) >= 2:
            start, end = line[0], line[-1]
            coordinates = [float(v) for v in (start[0], start[1], end[0], end[1])]
            if all(math.isfinite(v) for v in coordinates):
                distance = math.hypot(coordinates[2] - coordinates[0], coordinates[3] - coordinates[1])
                if distance > 0:
                    spacing = distance / (count - 1)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return dict(schema=1, array=array, rows=[], electrodes=count, spacing=spacing,
                start_distance=0., source_name='手动录入观测', source_file='',
                source_sha256=hashlib.sha256(uuid4().bytes).hexdigest(),
                flat_assumed=True,
                coordinate_frame='测线局部坐标；未地理定位；无实测高程，平地假设',
                relative_error=.03, lam=20., max_iter=12, binding={'mode': 'local'})


class ObservationRowDialog(Q.QDialog):
    """Return a copied packet on acceptance without touching the owner/project."""

    def __init__(self, owner, packet, index=None):
        super().__init__(owner)
        self.packet = copy.deepcopy(packet)
        self._original = copy.deepcopy(packet)
        self.index = index
        if index is not None and not 0 <= index < len(packet['rows']):
            raise ValueError('待编辑观测行不存在。')
        self.array_name = normalize_array(packet.get('array', 'wenner'))
        self.setWindowTitle(f'编辑{array_label(self.array_name)}观测' if index is not None
                            else f'新增{array_label(self.array_name)}观测')
        self.resize(620, 580)
        layout = Q.QVBoxLayout(self)
        self.description = Q.QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.array = Q.QComboBox()
        self.array.addItem('温纳 A–M–N–B', 'wenner')
        self.array.addItem('偶极—偶极 A–B–M–N', 'dipole-dipole')
        has_rows = bool(packet['rows'])
        self.array.setCurrentIndex(max(0, self.array.findData(self.array_name)))
        if has_rows:
            self.array.setEnabled(False)
        form.addRow('排列', self.array)
        self.electrodes = Q.QSpinBox()
        self.electrodes.setRange(4, 256)
        self.electrodes.setValue(packet['electrodes'])
        self.spacing = Q.QDoubleSpinBox()
        self.spacing.setDecimals(8)
        self.spacing.setRange(.01, 1000.)
        self.spacing.setValue(packet['spacing'])
        self.spacing.setSuffix(' m')
        # Existing acquisition geometry is displayed exactly, including spacing
        # outside the range permitted for a new manual record.
        if has_rows:
            count_label = Q.QLabel(str(packet['electrodes']))
            spacing_label = Q.QLabel(f"{packet['spacing']:.12g} m（已有观测，间距只读）")
            form.addRow('电极数量（只读）', count_label)
            form.addRow('电极间距', spacing_label)
            self.electrodes.setEnabled(False)
            self.spacing.setEnabled(False)
        else:
            form.addRow('电极数量', self.electrodes)
            form.addRow('电极间距', self.spacing)
        row = packet['rows'][index] if index is not None else dict(
            abmn=[2, 3, 0, 1] if self.array_name == 'dipole-dipole' else [0, 3, 1, 2],
            rhoa=100.)
        self.abmn = []
        self.source_labels = []
        for name, value in zip(('A 供电', 'B 供电', 'M 测量', 'N 测量'), row['abmn']):
            field = Q.QSpinBox()
            field.setRange(1, packet['electrodes'])
            field.setValue(value + 1)
            label = Q.QLabel()
            box = Q.QWidget()
            box_layout = Q.QHBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.addWidget(field)
            box_layout.addWidget(label, 1)
            form.addRow(name, box)
            self.abmn.append(field)
            self.source_labels.append(label)
            field.valueChanged.connect(self._refresh_labels)
        self.a, self.b, self.m, self.n = self.abmn
        # Text input permits scientific notation and lets validation explain NaN,
        # infinity and non-positive input without silently clamping a measurement.
        self.rhoa = Q.QLineEdit(str(row['rhoa']))
        form.addRow('视电阻率 / Ω·m', self.rhoa)
        self.note = Q.QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.confirm = Q.QCheckBox('我已核对排列与间距，接受当前平地及误差假设')
        self.confirm.setChecked(bool(packet.get('confirmed', False)))
        layout.addWidget(self.confirm)
        self.error_label = Q.QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color: #b42318;')
        layout.addWidget(self.error_label)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        buttons.button(Q.QDialogButtonBox.Ok).setText('保存观测')
        buttons.button(Q.QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.electrodes.valueChanged.connect(self._count_changed)
        self.array.currentIndexChanged.connect(self._array_changed)
        self._update_texts()
        self._refresh_labels()

    def _update_texts(self):
        self.array_name = normalize_array(self.array.currentData() or self.array_name)
        if self.array_name == 'dipole-dipole':
            self.description.setText('编号从 1 开始。偶极—偶极须为不相交、等长的 AB 与 MN 偶极；'
                '隔离间距 gap 必须是偶极长度 a 的整数倍且至少为 1a。原始电阻 R 与带符号 K 同号时，'
                '视电阻率为正；程序不会排序电极或取绝对值。视电阻率单位为 Ω·m。')
            self.note.setText(f"当前假设：平地、无实测高程；相对误差 "
                f"{self._original.get('relative_error', .03) * 100:g}%。"
                '局部坐标须在后续步骤完成地理配准。')
        else:
            self.description.setText('编号从 1 开始。温纳排列须按 A–M–N–B 等间隔布置；'
                '视电阻率单位为 Ω·m。')
            self.note.setText(f"当前假设：平地、无实测高程；相对误差 "
                f"{self._original.get('relative_error', .03) * 100:g}%。"
                '局部坐标须在后续步骤完成地理配准。')

    def _array_changed(self, *_):
        if self._original['rows']:
            self.array.setCurrentIndex(max(0, self.array.findData(
                normalize_array(self._original.get('array', 'wenner')))))
            return
        self._update_texts()
        values = [2, 3, 0, 1] if self.array_name == 'dipole-dipole' else [0, 3, 1, 2]
        for field, value in zip(self.abmn, values):
            field.setValue(value + 1)
        self._refresh_labels()

    def _count_changed(self, count):
        for field in self.abmn:
            field.setMaximum(count)
        self._refresh_labels()

    def _refresh_labels(self, *_):
        packet = copy.deepcopy(self._original)
        if not packet['rows']:
            packet['array'] = self.array_name
            packet['electrodes'] = self.electrodes.value()
            packet['spacing'] = self.spacing.value()
            if packet['electrodes'] != self._original['electrodes']:
                packet.pop('electrode_ids', None)
        try:
            ids = electrode_ids(packet)
            for field, label in zip(self.abmn, self.source_labels):
                label.setText(f'源电极：{ids[field.value() - 1]}')
        except (ValueError, KeyError, IndexError, TypeError) as error:
            for label in self.source_labels:
                label.setText(f'源电极无效：{error}')

    def accept(self):
        try:
            if not self.confirm.isChecked():
                raise ValueError('请先勾选接受当前平地及误差假设。')
            candidate = copy.deepcopy(self._original)
            candidate['array'] = self.array_name
            if not candidate['rows']:
                candidate['electrodes'] = self.electrodes.value()
                candidate['spacing'] = self.spacing.value()
                if candidate['electrodes']!=self._original['electrodes'] or candidate['spacing']!=self._original['spacing']:
                    candidate['binding']={'mode':'local'}
                if candidate['electrodes'] != self._original['electrodes']:
                    candidate.pop('electrode_ids', None)
            values = dict(abmn=[field.value() - 1 for field in self.abmn], rhoa=float(self.rhoa.text()))
            if self.index is None:
                candidate['rows'].append(dict(values,manual=True))
            else:
                row = candidate['rows'][self.index]
                if row['abmn'] != values['abmn'] or row['rhoa'] != values['rhoa']:
                    row.setdefault('source_record', copy.deepcopy(row))
                    # R2 already has a native-column source_record; enrich it
                    # before discarding derived values from the editable row.
                    if isinstance(row['source_record'],dict):
                        for field in ('abmn','rhoa','resistance','k'):
                            if field in row:row['source_record'].setdefault(field,copy.deepcopy(row[field]))
                    row.update(values, edited=True)
                    # A changed quadrupole or apparent resistivity invalidates
                    # the measured R and its K.  Keep them in source_record.
                    for field in ('voltage_mV', 'current_mA', 'k', 'resistance'):
                        row.pop(field, None)
            validate_packet(candidate)
            electrode_ids(candidate)
            changed=candidate!=self._original
            candidate['confirmed']=True
            if changed:candidate['modified']=True
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as error:
            self.error_label.setText(str(error))
            return
        self.packet = candidate
        self.error_label.clear()
        super().accept()
