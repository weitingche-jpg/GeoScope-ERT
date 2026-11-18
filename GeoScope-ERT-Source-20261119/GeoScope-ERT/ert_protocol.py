"""Strict readers for R2 protocol and electrode files.

The published Borth example uses:

* ``protocol.dat``: first non-empty line is the number of observations, then
  one row per observation with six whitespace-separated columns
  ``index M N A B R`` (native R2 order: M and N before A and B);
* ``electrodes.dat``: three columns ``x y z`` per electrode.  Some R2 exports
  put the electrode count on the first line; that variant is accepted too.

R2's native order is M, N, A, B.  The packet stores the solver order
``[A, B, M, N]`` (zero based) but keeps the original six fields and native
``[M, N, A, B]`` order in every row.  Swapping current and potential dipoles is
reciprocity, not a change to the measured transfer resistance.
"""
import hashlib
import math
from pathlib import Path

from ert_arrays import array_label, classify_array, geometric_factor


def _text_and_bytes(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('gb18030')
    return path, text, data


def _nonempty_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}不是有效数值。') from None
    if not math.isfinite(number):
        raise ValueError(f'{label}须为有限数值。')
    return number


def _positive_integer(value, label):
    number = _finite(value, label)
    if number < 1 or number != int(number):
        raise ValueError(f'{label}须为从1开始的正整数。')
    return int(number)


def is_r2_protocol(path):
    """Return True when a .dat file has the R2 six-column protocol header."""
    path = Path(path)
    if path.suffix.lower() != '.dat':
        return False
    try:
        _, text, _ = _text_and_bytes(path)
    except (OSError, UnicodeError):
        return False
    lines = _nonempty_lines(text)
    if len(lines) < 2:
        return False
    first = lines[0].split()
    if len(first) != 1:
        return False
    try:
        count = int(first[0])
    except ValueError:
        return False
    if count < 1:
        return False
    columns = lines[1].split()
    if len(columns) != 6:
        return False
    try:
        int(columns[0])
        for value in columns[1:5]:
            int(value)
        float(columns[5])
    except ValueError:
        return False
    return True


def _read_protocol(protocol_path):
    path, text, raw = _text_and_bytes(protocol_path)
    lines = _nonempty_lines(text)
    if not lines:
        raise ValueError(f'{path.name} 为空。')
    header = lines[0].split()
    if len(header) != 1:
        raise ValueError(f'{path.name} 首行须仅包含观测条数。')
    try:
        count = int(header[0])
    except ValueError:
        raise ValueError(f'{path.name} 首行观测条数不是整数。') from None
    if count < 1 or count > 100000:
        raise ValueError(f'{path.name} 观测条数超出范围。')
    records = lines[1:]
    if len(records) != count:
        raise ValueError(
            f'{path.name} 首行声明 {count} 条观测，实际读取到 {len(records)} 条。')
    parsed = []
    for offset, line in enumerate(records, 2):
        fields = line.split()
        if len(fields) != 6:
            raise ValueError(f'{path.name} 第{offset}行须为6列（索引 M N A B R）。')
        source_index = _positive_integer(fields[0], f'第{offset}行观测序号')
        m = _positive_integer(fields[1], f'第{offset}行 M')
        n = _positive_integer(fields[2], f'第{offset}行 N')
        a = _positive_integer(fields[3], f'第{offset}行 A')
        b = _positive_integer(fields[4], f'第{offset}行 B')
        resistance = _finite(fields[5], f'第{offset}行 R')
        parsed.append(dict(source_row=offset, source_index=source_index,
                           native=(m, n, a, b), resistance=resistance,
                           raw=fields))
    return path, raw, count, parsed


def _read_electrodes(electrode_path, expected_count):
    path, text, raw = _text_and_bytes(electrode_path)
    lines = _nonempty_lines(text)
    if not lines:
        raise ValueError(f'{path.name} 为空。')
    declared = None
    first = lines[0].split()
    if len(first) == 1:
        declared = _positive_integer(first[0], f'{path.name} 首行电极数量')
        lines = lines[1:]
    if declared is not None and len(lines) != declared:
        raise ValueError(
            f'{path.name} 首行声明 {declared} 个电极，实际读取到 {len(lines)} 个。')
    if not 4 <= len(lines) <= 256:
        raise ValueError(f'{path.name} 电极数量须为4–256。')
    coordinates = []
    for offset, line in enumerate(lines, 2 if declared is not None else 1):
        fields = line.split()
        if len(fields) != 3:
            raise ValueError(f'{path.name} 第{offset}行须为3列（x y z）。')
        coordinates.append(tuple(_finite(value, f'{path.name} 第{offset}行坐标')
                                 for value in fields))
    if expected_count is not None and len(coordinates) != expected_count:
        # The protocol file count is the number of observations, not electrodes;
        # this check is only used by callers that pass an explicit expectation.
        raise ValueError(f'{path.name} 电极数量与预期不一致。')
    x = [row[0] for row in coordinates]
    y = [row[1] for row in coordinates]
    z = [row[2] for row in coordinates]
    tolerance = 1e-9
    if any(abs(value - y[0]) > tolerance for value in y) or any(
            abs(value - z[0]) > tolerance for value in z):
        raise ValueError(f'{path.name} 电极须位于平直测线（y、z 恒定）。')
    differences = [x[i + 1] - x[i] for i in range(len(x) - 1)]
    if any(value <= 0 for value in differences):
        raise ValueError(f'{path.name} 电极 x 须严格递增。')
    spacing = sum(differences) / len(differences)
    if spacing <= 0 or any(abs(value - spacing) > max(1e-9, abs(spacing) * 1e-9)
                           for value in differences):
        raise ValueError(f'{path.name} 电极 x 须等间距。')
    return path, raw, coordinates, spacing


def read_r2_protocol(protocol_path, electrode_path):
    """Read an R2 protocol plus its electrode coordinates.

    Every native M,N,A,B row is mapped to solver order and classified with the
    shared geometry validators, so both standard Wenner and dipole-dipole
    protocols are accepted while mixed or invalid geometry is rejected.
    Returns a schema-1 packet accepted by ``observed_ert.validate_packet``.
    The source hash covers both files; every row retains the original R2
    column values, the native M,N,A,B order, the signed geometric factor and
    the measured transfer resistance.
    """
    protocol_path, protocol_raw, count, records = _read_protocol(protocol_path)
    electrode_path, electrode_raw, coordinates, spacing = _read_electrodes(
        electrode_path, None)
    electrode_count = len(coordinates)
    max_index = max(max(record['native']) for record in records)
    if max_index > electrode_count:
        raise ValueError(
            f'protocol.dat 引用了电极 {max_index}，但 electrodes.dat 只有 {electrode_count} 个。')
    rows = []
    row_arrays = []
    for record in records:
        m, n, a, b = record['native']
        abmn = [a - 1, b - 1, m - 1, n - 1]
        try:
            row_array = classify_array(abmn, electrode_count)
        except ValueError as error:
            raise ValueError(f'protocol.dat 第{record["source_row"]}行：{error}') from error
        try:
            k = geometric_factor(abmn, spacing)
        except ValueError as error:
            raise ValueError(f'protocol.dat 第{record["source_row"]}行：{error}') from error
        rhoa = k * record['resistance']
        if not math.isfinite(rhoa) or rhoa <= 0:
            raise ValueError(
                f'protocol.dat 第{record["source_row"]}行：K={k:.8g}, R={record["resistance"]:.8g} '
                f'换算视电阻率 {rhoa:.8g} Ω·m 不为正；请检查电极角色或测量极性，'
                '不会通过排序或取绝对值修复。')
        rows.append(dict(
            abmn=abmn,
            rhoa=rhoa,
            resistance=record['resistance'],
            k=k,
            source_row=record['source_row'],
            source_index=record['source_index'],
            source_abmn=[m - 1, n - 1, a - 1, b - 1],
            source_native=[m, n, a, b],
            source_values=list(record['raw']),
            source_record=dict(index=record['source_index'], M=m, N=n, A=a, B=b,
                               R=record['resistance']),
        ))
        row_arrays.append(row_array)
    present = [name for name in ('wenner', 'dipole-dipole')
               if name in row_arrays]
    if len(present) > 1:
        counts = {name: row_arrays.count(name)
                  for name in ('wenner', 'dipole-dipole')}
        raise ValueError(
            'protocol.dat 排列混合：温纳 {0} 条、偶极—偶极 {1} 条；'
            'R2 协议必须使用单一受支持排列，不能猜测。'.format(
                counts['wenner'], counts['dipole-dipole']))
    array = present[0]
    array_detection = dict(
        array=array, status='ok',
        message=f'按原生 M,N,A,B 映射后识别为{array_label(array)}，共 {len(rows)} 条观测。',
        counts={'wenner': row_arrays.count('wenner'),
                'dipole-dipole': row_arrays.count('dipole-dipole'),
                'invalid': 0, 'excluded': 0},
        source_rows={record['source_row']: dict(array=name, status='ok')
                     for record, name in zip(records, row_arrays)})
    combined_hash = hashlib.sha256(
        protocol_raw + b'\x00' + electrode_raw).hexdigest()
    protocol_hash = hashlib.sha256(protocol_raw).hexdigest()
    electrode_hash = hashlib.sha256(electrode_raw).hexdigest()
    packet = dict(
        schema=1,
        array=array,
        array_detection=array_detection,
        source_file=str(protocol_path),
        source_name=protocol_path.name,
        source_files={'protocol': str(protocol_path),
                      'electrodes': str(electrode_path)},
        source_sha256=combined_hash,
        source_sha256_protocol=protocol_hash,
        source_sha256_electrodes=electrode_hash,
        source_hash_note='sha256(protocol.dat bytes + b"\\0" + electrodes.dat bytes)',
        source_format='R2_protocol_2D',
        source_native_order='M,N,A,B',
        source_columns=['index', 'M', 'N', 'A', 'B', 'R'],
        source_reciprocity='原生 M,N,A,B 按角色重排为求解顺序 A,B,M,N；未交换物理角色，原始列保留。',
        import_format='r2_protocol',
        electrodes=electrode_count,
        spacing=spacing,
        start_distance=coordinates[0][0],
        electrode_coordinates=[list(value) for value in coordinates],
        rows=rows,
        flat_assumed=True,
        coordinate_frame='R2二维测线局部坐标；平地假设；未地理定位',
        relative_error=.03,
        lam=20.,
        max_iter=12,
    )
    from observed_ert import validate_packet
    return validate_packet(packet)
