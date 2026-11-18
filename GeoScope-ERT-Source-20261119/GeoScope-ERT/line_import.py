"""Import one survey line as local acquisition plus its observations.

This module owns the pure packet/workspace preparation for the second-step
"导入测线数据（含测点与观测）" action and the small UI mixin that reuses the
existing R2/instrument/generic parsers and confirmation dialog.  A local
import is never treated as a georeferenced acquisition: source XYZ and hashes
are retained, while the editable workspace uses explicit local station
coordinates on a flat reference.
"""
import copy
from pathlib import Path

from observed_ert import validate_packet
from workflow_geometry import electrode_ids

LOCAL_ELEVATION_LABEL = '局部距离/平地参考值，未配准'
LOCAL_FRAME_LABEL = '局部未配准'


def is_local_import(packet):
    """True only for an imported line explicitly marked local and unbound."""
    if not isinstance(packet, dict):
        return False
    metadata = packet.get('line_import')
    if not isinstance(metadata, dict) or metadata.get('frame') != 'local':
        return False
    binding = packet.get('binding')
    return isinstance(binding, dict) and binding.get('mode') == 'local'


def _source_entries(packet, source_files):
    entries = []
    if isinstance(source_files, dict):
        for role, path in source_files.items():
            if not path:
                continue
            path = str(path)
            entry = dict(role=str(role), name=Path(path).name, path=path)
            if role == 'protocol' and packet.get('source_sha256_protocol'):
                entry['sha256'] = packet['source_sha256_protocol']
            elif role == 'electrodes' and packet.get('source_sha256_electrodes'):
                entry['sha256'] = packet['source_sha256_electrodes']
            elif packet.get('source_sha256'):
                entry['sha256'] = packet['source_sha256']
            entries.append(entry)
    elif packet.get('source_file'):
        path = str(packet['source_file'])
        entry = dict(role='observations', name=packet.get('source_name') or Path(path).name,
                     path=path)
        if packet.get('source_sha256'):
            entry['sha256'] = packet['source_sha256']
        entries.append(entry)
    return entries


def mark_line_import(packet, source_files=None):
    """Return a copy marked as an unregistered local line import.

    The source XYZ (when the reader supplies real electrode coordinates) and
    all available source hashes are copied into ``packet['line_import']``.
    """
    if not isinstance(packet, dict):
        raise ValueError('测线导入包格式无效。')
    packet = copy.deepcopy(packet)
    files = source_files if source_files is not None else packet.get('source_files')
    source_xyz = packet.get('electrode_coordinates')
    count = packet.get('electrodes')
    if source_xyz is not None:
        source_xyz = [list(map(float, row)) for row in source_xyz]
        if not isinstance(count, int) or len(source_xyz) != count:
            raise ValueError('原始电极坐标数量与观测包电极数量不一致。')
    metadata = dict(
        schema=1,
        frame='local',
        source_name=packet.get('source_name') or (Path(str(packet['source_file'])).name
                                                  if packet.get('source_file') else '导入测线'),
        source_files=copy.deepcopy(files) if isinstance(files, dict) else copy.deepcopy(files),
        sources=_source_entries(packet, files),
        source_sha256=packet.get('source_sha256'),
        source_sha256_protocol=packet.get('source_sha256_protocol'),
        source_sha256_electrodes=packet.get('source_sha256_electrodes'),
        source_xyz=source_xyz,
        source_format=packet.get('source_format') or packet.get('import_format'),
        coordinate_label=LOCAL_ELEVATION_LABEL,
        note='原始 XYZ 与哈希保留；工作台使用局部电极距离和平地参考高程，未配准。',
    )
    packet['line_import'] = metadata
    packet['binding'] = {'mode': 'local'}
    return packet


def prepare_line_workspace(packet, terrain_source=None):
    """Build a complete local workspace candidate without mutating project state.

    The workspace contains exactly the packet's input electrodes, never a
    default 24-point line.  Station X is the local distance and Y/Z are zero,
    explicitly labelled as a flat reference that has not been registered.
    """
    packet = copy.deepcopy(packet)
    if not is_local_import(packet):
        packet = mark_line_import(packet)
    packet = validate_packet(packet)
    if not is_local_import(packet):
        raise ValueError('测线导入包未标记为局部未配准，不能创建本地测线。')
    count = packet['electrodes']
    spacing = float(packet['spacing'])
    length = float((count - 1) * spacing)
    ids = electrode_ids(packet)
    source_xyz = packet.get('line_import', {}).get('source_xyz')
    points = []
    for index, identifier in enumerate(ids):
        point = dict(
            id=identifier,
            x=float(index * spacing),
            y=0.,
            z=0.,
            elevation_source=LOCAL_ELEVATION_LABEL,
            frame='local',
            local_frame=True,
        )
        if source_xyz is not None:
            point['source_xyz'] = [float(value) for value in source_xyz[index]]
        points.append(point)
    line = [[0., 0.], [length, 0.]]
    packet['binding'] = {'mode': 'local'}
    return dict(
        schema=1,
        terrain=str(terrain_source) if terrain_source is not None else None,
        points=points,
        line=line,
        line_endpoint_overrides={},
        ert_packet=packet,
        case=None,
        points_follow_line=False,
        simulation_enabled=False,
        result_binding=None,
        workflow_step=1,
        opacity=40,
        local_frame=True,
        local_frame_label=LOCAL_FRAME_LABEL,
    )


class LineImportMixin:
    def has_map_import_line(self):
        return (self.terrain is not None and len(getattr(self, 'line', [])) == 2
                and not is_local_import(getattr(self, 'ert_packet', None)))

    """Second-step primary action and atomic workspace commit."""

    def _line_import_busy(self):
        busy = getattr(self, 'survey_busy', None)
        return (getattr(self, 'process', None) is not None
                or (callable(busy) and busy()))

    def _read_line_import_packet(self, path):
        from PySide6 import QtWidgets as Q
        from observation_tables import is_instrument_csv
        from observation_import_dialog import ObservationImportDialog
        from ert_protocol import is_r2_protocol, read_r2_protocol
        from observed_ert import read_observations

        path = str(path)
        if is_r2_protocol(path):
            # New line import auto-pairs the R2 electrode file in the same
            # folder; only ask when it is genuinely absent.  The old
            # import_observations flow keeps its explicit double-file prompt.
            electrode = Path(path).with_name('electrodes.dat')
            if not electrode.is_file():
                electrode_path, _ = Q.QFileDialog.getOpenFileName(
                    self, '选择R2电极坐标文件 electrodes.dat', str(electrode),
                    'R2 电极文件 (electrodes.dat *.dat);;所有文件 (*)')
                if not electrode_path:
                    self.say('未导入：缺少电极坐标文件electrodes.dat，原项目保持')
                    return None
                electrode = Path(electrode_path)
            return read_r2_protocol(path, str(electrode))
        if is_instrument_csv(path):
            return read_observations(path)
        dialog = ObservationImportDialog(self, path)
        if dialog.exec() != Q.QDialog.Accepted:
            return None
        return dialog.packet

    def import_line_data(self):
        from PySide6 import QtWidgets as Q
        from observation_workflow import ObservationDialog

        if self._line_import_busy():
            self.say('计算或测线批次进行中，暂不导入测线。')
            return
        if not self.require_committed_edit():
            return
        path, _ = Q.QFileDialog.getOpenFileName(
            self, '导入测线数据（R2 protocol.dat、原始仪器CSV/DAT或通用表格）', '',
            '测线数据 (*.csv *.dat *.xlsx);;R2 protocol (protocol.dat);;'
            '原始仪器 (*.csv *.dat);;通用表格 (*.xlsx *.csv)')
        if not path:
            return
        try:
            packet = self._read_line_import_packet(path)
            if packet is None:
                return
            on_map = self.has_map_import_line()
            if not on_map:
                packet = mark_line_import(packet)
            dialog = ObservationDialog(self, packet, line_import=not on_map)
            if dialog.exec() == Q.QDialog.Accepted:
                if on_map:
                    self.open_observation_packet(dialog.packet)
                else:
                    self.apply_imported_line(dialog.packet)
        except Exception as error:
            self.error(error)

    def apply_imported_line(self, packet):
        """Create one survey line atomically; cancel/invalid leaves state alone."""
        from shortcuts import edit_snapshot, restore_edit

        if self._line_import_busy():
            raise ValueError('计算或测线批次进行中，不能导入测线。')
        if not self.require_committed_edit():
            return None
        packet = copy.deepcopy(packet)
        if not is_local_import(packet):
            packet = mark_line_import(packet)
        workspace = prepare_line_workspace(packet)  # validate the full candidate first
        state = getattr(self, 'survey_state', None)
        if not isinstance(state, dict):
            raise ValueError('当前项目没有可用的测线状态。')
        if len(state.get('lines') or []) >= 30:
            raise ValueError('最多支持30条测线；请先移除不需要的测线。')
        sync = getattr(self, 'sync_survey_line', None)
        if callable(sync):
            sync()
        before = edit_snapshot(self)
        try:
            ensure = getattr(self, 'ensure_current_workspace_line', None)
            if callable(ensure):
                ensure()
            sid = self._next_survey_line_id()
            self.survey_state['lines'].append(
                dict(id=sid, name=sid, selected=True, visible=True, workspace=workspace))
            self.activate_survey_line(sid, sync=False, clear_history=False)
            self.record_edit(before, '导入测线数据（含测点与观测）')
            self.redraw()
            self.set_workflow_step(1)
            if hasattr(self, 'data_tabs'):
                self.data_tabs.setCurrentIndex(0)
            dock = getattr(self, 'survey_dock', None)
            if dock is not None:
                dock.show()
        except Exception:
            restore_edit(self, before)
            raise
        length = (packet['electrodes'] - 1) * packet['spacing']
        self.say(f'已导入测线 {sid}：{packet["electrodes"]} 个电极、'
                 f'{len(packet["rows"])} 条观测、长 {length:g} m；'
                 '坐标为局部距离/平地参考值，未配准，未叠加到地形。'
                 '旧测线保留；Ctrl+Z 可撤销整次导入。')
        return sid
