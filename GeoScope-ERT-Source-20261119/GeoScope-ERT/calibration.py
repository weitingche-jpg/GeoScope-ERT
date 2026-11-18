"""Pure geometry inspection and one-click calibration proposals.

This module deliberately contains no Qt or solver code.  It compares an
observed Wenner packet with the electrodes currently on the terrain and can
rebuild the electrode row from a fixed start point and direction.  The
rebuild keeps the observed count, spacing and total length exactly; it never
scales or edits the observations to fit a hand-drawn line.
"""
import math

from workflow_geometry import (electrode_ids, fit_endpoints,
    generate_bound_points, is_map_line_binding, physical_signature, validate_binding)


CALIBRATED_ELEVATION_SOURCE = '校准生成（非实测）'


def _number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}必须是数值。') from None
    if not math.isfinite(number):
        raise ValueError(f'{label}必须是有限数值，不能含 NaN 或 Inf。')
    return number


def _tolerance(value):
    value = _number(value, '校核容差')
    if not 0 < value <= 10:
        raise ValueError('配准容差须大于零且不超过 10 米。')
    return value


def _packet_layout(packet):
    if not isinstance(packet, dict):
        raise ValueError('观测包无效。')
    count = packet.get('electrodes')
    if isinstance(count, bool) or not isinstance(count, int) or not 4 <= count <= 256:
        raise ValueError('电极数量须为 4–256 的整数。')
    spacing = _number(packet.get('spacing'), '电极间距')
    if spacing <= 0:
        raise ValueError('电极间距须大于零。')
    return count, spacing, (count - 1) * spacing


def _point_coordinates(point, index, issues):
    if not isinstance(point, dict):
        issues.append(f'第 {index + 1} 个测点不是有效记录。')
        return None
    try:
        return tuple(_number(point[key], f'测点第 {index + 1} 行的 {key.upper()} 坐标')
                     for key in ('x', 'y', 'z'))
    except KeyError as error:
        issues.append(f'测点第 {index + 1} 行缺少坐标字段 {error.args[0]}。')
    except ValueError as error:
        issues.append(str(error))
    return None


def _line_length(line, issues):
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        issues.append('测线不足两个端点，无法确定实际线长。')
        return 0.0
    vertices = []
    for index, vertex in enumerate(line):
        if not isinstance(vertex, (list, tuple)) or len(vertex) < 2:
            issues.append(f'测线第 {index + 1} 个端点无效。')
            return 0.0
        try:
            vertices.append(tuple(_number(vertex[key], f'测线第 {index + 1} 个端点的坐标')
                                  for key in (0, 1)))
        except (KeyError, TypeError, ValueError) as error:
            issues.append(f'测线第 {index + 1} 个端点无效：{error}')
            return 0.0
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                 for a, b in zip(vertices, vertices[1:]))
    if length <= 0:
        issues.append('实际线长为零；测线端点不能重合。')
    return length


def _has_file_coordinates(point):
    if not isinstance(point, dict):
        return False
    source_file = point.get('source_file')
    if source_file:
        return True
    source = point.get('elevation_source')
    if isinstance(source, str) and (('实测' in source and '非实测' not in source) or '文件' in source):
        return True
    return False


def inspect_layout(packet, points, line, tolerance=.1):
    """Return observation requirements, current geometry and conflict messages.

    The inputs are never mutated.  Malformed coordinates and a zero-length line
    are reported as user-facing issues; an invalid observation packet or
    tolerance is rejected with ``ValueError`` because no meaningful comparison
    can be made without those values.
    """
    count, spacing, expected_length = _packet_layout(packet)
    tolerance = _tolerance(tolerance)
    expected_ids = electrode_ids(packet)
    issues = []

    if isinstance(points, (list, tuple)):
        point_list = list(points)
    else:
        point_list = []
        issues.append('测点数据不是列表。')

    actual_count = len(point_list)
    if actual_count != count:
        issues.append(f'电极数量不符：观测要求 {count} 个，当前 {actual_count} 个。')

    seen = []
    seen_set = set()
    coordinates = []
    for index, point in enumerate(point_list):
        identifier = point.get('id') if isinstance(point, dict) else None
        if not isinstance(identifier, str) or not identifier.strip():
            issues.append(f'第 {index + 1} 个测点编号为空或不是文本。')
        elif identifier in seen_set:
            issues.append(f'电极编号重复：{identifier}。')
        else:
            seen.append(identifier)
            seen_set.add(identifier)
        coordinates.append(_point_coordinates(point, index, issues))

    expected_set = set(expected_ids)
    missing = [identifier for identifier in expected_ids if identifier not in seen_set]
    extra = [identifier for identifier in seen if identifier not in expected_set]
    if missing:
        issues.append('缺少电极编号：' + '、'.join(missing[:8])
                      + (' 等' if len(missing) > 8 else '') + '。')
    if extra:
        issues.append('存在观测包之外的电极编号：' + '、'.join(extra[:8])
                      + (' 等' if len(extra) > 8 else '') + '。')

    actual_length = _line_length(line, issues)
    if is_map_line_binding(packet):
        if 0 < actual_length < expected_length - 1e-9:
            issues.append(f'地图测线长 {actual_length:g} 米，观测需要至少 {expected_length:g} 米。')
    elif actual_length > 0 and abs(actual_length - expected_length) > tolerance:
        issues.append(f'实际线长 {actual_length:g} 米与观测总长 '
                      f'{expected_length:g} 米相差超过容差 {tolerance:g} 米。')

    valid = [item for item in coordinates if item is not None]
    if len(valid) >= 2:
        gaps = []
        for index, (first, second) in enumerate(zip(valid, valid[1:])):
            gap = math.hypot(second[0] - first[0], second[1] - first[1])
            gaps.append(gap)
            if abs(gap - spacing) > tolerance:
                issues.append(f'第 {index + 1}–{index + 2} 个测点间距 {gap:g} 米，'
                              f'与观测间距 {spacing:g} 米相差超过容差。')
        if gaps and max(gaps) - min(gaps) > tolerance:
            issues.append(f'测点不等距：最小间距 {min(gaps):g} 米，'
                          f'最大间距 {max(gaps):g} 米。')

    return dict(expected_count=count, expected_spacing=float(spacing),
                expected_length=float(expected_length), actual_count=actual_count,
                actual_length=float(actual_length), issues=issues,
                has_file_coordinates=any(_has_file_coordinates(point)
                                         for point in point_list))


def make_proposal(packet, start_xyz, angle_degrees, terrain, tolerance=.1):
    """Rebuild the electrode row from the observed count and spacing.

    ``angle_degrees`` is measured counter-clockwise from +X.  The endpoint is
    calculated from the fixed metric layout, the start elevation is retained,
    and the proposal is validated against the current terrain.  The packet and
    terrain are not modified.
    """
    count, spacing, length = _packet_layout(packet)
    tolerance = _tolerance(tolerance)
    if terrain is None:
        raise ValueError('请先加载地形，再按观测重建布设。')
    if (isinstance(start_xyz, (str, bytes)) or not hasattr(start_xyz, '__len__')
            or len(start_xyz) != 3):
        raise ValueError('起点须包含 X、Y、Z 三个坐标。')
    start = [_number(value, '起点坐标') for value in start_xyz]
    angle = _number(angle_degrees, '方向角')
    radians = math.radians(angle)
    end = [start[0] + math.cos(radians) * length,
           start[1] + math.sin(radians) * length, start[2]]

    binding = fit_endpoints(packet, start, end, tolerance)
    points, line = generate_bound_points(packet, binding)
    for point in points:
        point['elevation_source'] = CALIBRATED_ELEVATION_SOURCE
    binding['terrain_source'] = getattr(terrain, 'source', None)
    binding['geometry_signature'] = physical_signature(points, line)
    problems = validate_binding(packet, binding, points, line, terrain)
    if problems:
        raise ValueError('\n'.join(problems))
    return dict(binding=binding, points=points, line=line)
