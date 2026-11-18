"""Convert reviewed generic observation tables into schema-1 packets.

The reader is intentionally local and rule based: no workbook macro is executed,
no network service is called, and no unit or electrode layout is guessed.  This
module only converts reviewed rows; the desktop dialog owns the review workflow.

Both Wenner and dipole-dipole arrays are supported.  The value column can be an
apparent resistivity (``rhoa``) or a raw transfer resistance (``resistance``).
For resistance input the signed geometric factor is computed from the reviewed
ABMN geometry and spacing, and ``rhoa=K*R`` must be positive.
"""
import hashlib
import math
import re
from pathlib import Path

from observed_ert import validate_packet
from point_files import read_tables
from ert_arrays import (array_label, classify_array, geometric_factor,
                        normalize_array, validate_abmn, geometry_metrics)


FIELDS=('a','b','m','n','rho')
TITLES={'a':'A','b':'B','m':'M','n':'N','rho':'视电阻率 / 电阻'}
UNIT_SCALES={'Ω·m':1.,'kΩ·m':1000.,'Ω':1.,'kΩ':1000.}
RHO_UNITS={'Ω·m','kΩ·m'}
RESISTANCE_UNITS={'Ω','kΩ'}
VALUE_KINDS=('rhoa','resistance')
ALIASES={
    'a':{'a','a供电','供电a','电极a','a极','供电电极a','am'},
    'b':{'b','b供电','供电b','电极b','b极','供电电极b','bm'},
    'm':{'m','m测量','测量m','电极m','m极','测量电极m'},
    'n':{'n','n测量','测量n','电极n','n极','测量电极n'},
    'rho':{'rhoa','ρs','ρa','视电阻率','电阻率','apparentresistivity','rho','ρ','rs',
           'r','resistance','电阻','原始电阻','transferresistance'},
}


def normalized(value):
    text=str(value if value is not None else '').strip().lower()
    text=text.replace('（','(').replace('）',')')
    text=re.sub(r'\s+','',text)
    text=re.sub(r'\(.*?\)','',text)
    return text


def normalize_value_kind(value_kind):
    if value_kind is None:
        return 'rhoa'
    text=str(value_kind).strip().lower()
    aliases={'rhoa':'rhoa','rho':'rhoa','视电阻率':'rhoa',
             'resistance':'resistance','r':'resistance','电阻':'resistance',
             '原始电阻':'resistance'}
    if text not in aliases:
        raise ValueError('数值类型仅支持视电阻率（rhoa）或原始电阻（resistance）。')
    return aliases[text]


def detect_header(rows):
    """Return (zero-based header row, field->column) using local header rules."""
    best_index,best_mapping,best_score=-1,{},-1
    for index,row in enumerate(rows[:30]):
        mapping={}
        for col,value in enumerate(row):
            key=normalized(value)
            if not key:continue
            for field in FIELDS:
                if field not in mapping and key in ALIASES[field]:
                    mapping[field]=col
                    break
        score=len(mapping)+5*int(len(mapping)==len(FIELDS))
        if score>best_score:
            best_index,best_mapping,best_score=index,mapping,score
    return best_index,best_mapping


def validate_mapping(mapping,width=None):
    if not isinstance(mapping,dict):raise ValueError('字段映射无效。')
    missing=[TITLES[field] for field in FIELDS if field not in mapping]
    if missing:raise ValueError('请为 A/B/M/N/视电阻率或电阻 选择列；缺少：'+'、'.join(missing))
    columns=[]
    for field in FIELDS:
        value=mapping[field]
        if isinstance(value,bool) or not isinstance(value,int) or value<0:
            raise ValueError(f'{TITLES[field]} 的列号无效。')
        if width is not None and value>=width:
            raise ValueError(f'{TITLES[field]} 的列号超出表格范围。')
        columns.append(value)
    if len(set(columns))!=len(columns):
        raise ValueError('多个字段使用了同一列，请为 A/B/M/N/视电阻率或电阻 选择不同列。')


def unit_scale(unit,value_kind=None):
    if isinstance(unit,(int,float)) and not isinstance(unit,bool):
        scale=float(unit)
    else:scale=UNIT_SCALES.get(str(unit))
    if scale is None or not math.isfinite(scale) or scale<=0:
        raise ValueError('仅支持 Ω·m、kΩ·m、Ω 与 kΩ 单位。')
    if value_kind is not None and str(unit) in UNIT_SCALES:
        kind=normalize_value_kind(value_kind)
        if kind=='rhoa' and str(unit) not in RHO_UNITS:
            raise ValueError('视电阻率请使用 Ω·m 或 kΩ·m。')
        if kind=='resistance' and str(unit) not in RESISTANCE_UNITS:
            raise ValueError('原始电阻请使用 Ω 或 kΩ。')
    return scale


def _index_value(value):
    text=str(value if value is not None else '').strip()
    if not text:raise ValueError('A/B/M/N 不能为空。')
    try:number=float(text)
    except (TypeError,ValueError):raise ValueError('A/B/M/N 须为从1开始的正整数。') from None
    if not math.isfinite(number) or number<1 or number!=int(number):
        raise ValueError('A/B/M/N 须为从1开始的正整数。')
    return int(number)-1


def _rho_value(value,scale):
    text=str(value if value is not None else '').strip()
    if not text:raise ValueError('视电阻率不能为空。')
    try:number=float(text)
    except (TypeError,ValueError):raise ValueError('视电阻率不是有效数值。') from None
    if not math.isfinite(number):raise ValueError('视电阻率须为有限数值。')
    if number<=0:raise ValueError('视电阻率须大于0。')
    result=number*scale
    if not math.isfinite(result):raise ValueError('单位换算后的视电阻率超出有效数值范围。')
    return result


def _resistance_value(value,scale):
    text=str(value if value is not None else '').strip()
    if not text:raise ValueError('原始电阻不能为空。')
    try:number=float(text)
    except (TypeError,ValueError):raise ValueError('原始电阻不是有效数值。') from None
    if not math.isfinite(number):raise ValueError('原始电阻须为有限数值。')
    result=number*scale
    if not math.isfinite(result):raise ValueError('单位换算后的原始电阻超出有效数值范围。')
    return result


def _raw_value(row,col):
    return row[col] if 0<=col<len(row) and row[col] is not None else ''


def _spacing_or_none(spacing):
    try:value=float(spacing)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and value>0 else None


def detect_array(rows,header_index,mapping,excluded=(),electrodes=None):
    """Identify the array from every non-blank, non-excluded ABMN row.

    Geometry alone decides: each row is checked with
    ``ert_arrays.classify_array`` and all active rows must agree.  A missing or
    invalid A/B/M/N mapping, an empty active set, an unsupported or malformed
    quadrupole and a mixed table all return a blocking ``status`` instead of
    guessing.  The value column is not inspected, so a bad rho value cannot
    change the recognised geometry (it still blocks import elsewhere).
    """
    excluded=set(excluded or ())
    if not isinstance(header_index,int) or isinstance(header_index,bool) or not -1<=header_index<len(rows):
        raise ValueError('表头行超出表格范围。')
    if electrodes is not None:
        if isinstance(electrodes,bool) or not isinstance(electrodes,int) or not 4<=electrodes<=256:
            raise ValueError('电极数量须为 4–256 的整数。')
    width=max((len(row) for row in rows),default=0)
    counts={'wenner':0,'dipole-dipole':0,'invalid':0,'excluded':0,'blank':0}
    source_rows={}
    if not isinstance(mapping,dict):
        return dict(array=None,status='missing_mapping',
            message='无法识别排列：字段映射无效。请为 A/B/M/N 选择有效列。',
            counts=counts,source_rows=source_rows)
    missing=[TITLES[field] for field in ('a','b','m','n') if field not in mapping]
    bad=[TITLES[field] for field in ('a','b','m','n') if field in mapping and (
        isinstance(mapping[field],bool) or not isinstance(mapping[field],int)
        or mapping[field]<0 or mapping[field]>=width)]
    if missing or bad:
        names='、'.join(dict.fromkeys(missing+bad))
        return dict(array=None,status='missing_mapping',
            message=f'无法识别排列：A/B/M/N 字段映射不完整或列号无效（{names}）。',
            counts=counts,source_rows=source_rows)
    active=0
    for index,row in enumerate(rows[header_index+1:]):
        source_row=header_index+index+2
        if not any(str(value if value is not None else '').strip() for value in row):
            counts['blank']+=1
            continue
        if index in excluded:
            counts['excluded']+=1
            continue
        active+=1
        try:
            abmn=[_index_value(_raw_value(row,mapping[field])) for field in ('a','b','m','n')]
            name=classify_array(abmn,electrodes)
        except ValueError as error:
            counts['invalid']+=1
            source_rows[source_row]=dict(array=None,status='invalid',error=str(error))
        else:
            counts[name]+=1
            source_rows[source_row]=dict(array=name,status='ok')
    present=[name for name in ('wenner','dipole-dipole') if counts[name]]
    bad_rows=[row for row,evidence in source_rows.items() if evidence['status']=='invalid']
    if not active:
        status='empty'
        message='未排除的非空观测行为 0，无法识别排列。' if counts['excluded'] else '表中没有非空观测行，无法识别排列。'
        return dict(array=None,status=status,message=message,counts=counts,source_rows=source_rows)
    if counts['invalid']:
        first=bad_rows[0]
        shown='、'.join(str(row) for row in bad_rows[:5])+('等' if len(bad_rows)>5 else '')
        mixed=' 同时包含温纳与偶极—偶极行，不能混合导入。' if len(present)>1 else ''
        return dict(array=None,status='invalid',
            message=(f'有 {counts["invalid"]} 条未排除行无法识别（源行 {shown}）；'
                     f'第 {first} 行：{source_rows[first]["error"]}'
                     f'请排除或修正这些行。{mixed}'),
            counts=counts,source_rows=source_rows)
    if len(present)>1:
        wenner_rows=[row for row,evidence in source_rows.items() if evidence.get('array')=='wenner']
        dd_rows=[row for row,evidence in source_rows.items() if evidence.get('array')=='dipole-dipole']
        return dict(array=None,status='mixed',
            message=(f'排列识别冲突：温纳 {counts["wenner"]} 条（源行 {wenner_rows[0]}–{wenner_rows[-1]}）、'
                     f'偶极—偶极 {counts["dipole-dipole"]} 条（源行 {dd_rows[0]}–{dd_rows[-1]}）；'
                     '未排除行必须一致，请排除错误行或修正源表。'),
            counts=counts,source_rows=source_rows)
    name=present[0]
    return dict(array=name,status='ok',
        message=f'自动识别为{array_label(name)}，共 {counts[name]} 条未排除观测。',
        counts=counts,source_rows=source_rows)


def analyze_rows(rows,header_index,mapping,unit='Ω·m',excluded=(),electrodes=None,
                 array='wenner',value_kind='rhoa',spacing=None):
    """Return one review record per non-empty source row.

    ``header_index`` is zero based; -1 means the first row is data.  Duplicate
    electrode combinations are recalculated only among non-excluded valid rows,
    so either member of a duplicate pair can be excluded and the other imported.
    ``array='auto'`` recognises the geometry row by row; a mixed table still
    returns visible records (each row keeps its own recognised array) so the
    dialog can show and exclude the offending row.  Explicit ``array`` and
    ``value_kind`` defaults preserve the old Wenner/rhoa behaviour.
    """
    auto=isinstance(array,str) and array.strip().lower()=='auto'
    if not auto:array=normalize_array(array)
    value_kind=normalize_value_kind(value_kind)
    if not isinstance(header_index,int) or isinstance(header_index,bool) or not -1<=header_index<len(rows):
        raise ValueError('表头行超出表格范围。')
    width=max((len(row) for row in rows),default=0)
    validate_mapping(mapping,width)
    if electrodes is not None:
        if isinstance(electrodes,bool) or not isinstance(electrodes,int) or not 4<=electrodes<=256:
            raise ValueError('电极数量须为 4–256 的整数。')
    if value_kind=='rhoa' and str(unit) in RESISTANCE_UNITS:
        raise ValueError('视电阻率请使用 Ω·m 或 kΩ·m，不能使用电阻单位 Ω。')
    if value_kind=='resistance' and str(unit) in RHO_UNITS:
        raise ValueError('原始电阻请使用 Ω 或 kΩ，不能使用电阻率单位 Ω·m。')
    scale=unit_scale(unit,value_kind)
    spacing_value=_spacing_or_none(spacing)
    excluded=set(excluded)
    if auto:
        array=detect_array(rows,header_index,mapping,excluded,electrodes)['array']
    records=[]
    for index,row in enumerate(rows[header_index+1:]):
        raw=list(row)
        if not any(str(value if value is not None else '').strip() for value in raw):
            continue
        record=dict(index=index,source_row=header_index+index+2,raw=raw,
            abmn=None,abmn_input=None,rhoa=None,rhoa_input='',
            resistance=None,resistance_input='',k=None,array=array,value_kind=value_kind,
            error='',excluded=index in excluded)
        try:
            abmn=[_index_value(_raw_value(row,mapping[field])) for field in ('a','b','m','n')]
            if array is None:
                record['array']=classify_array(abmn,electrodes)
            try:validate_abmn(record['array'],abmn,electrodes)
            except ValueError as error:raise ValueError(str(error)) from error
            value_input=_raw_value(row,mapping['rho'])
            if value_kind=='rhoa':
                rhoa=_rho_value(value_input,scale)
                record.update(abmn=abmn,abmn_input=[i+1 for i in abmn],
                    rhoa=rhoa,rhoa_input=str(value_input).strip())
            else:
                if spacing_value is None:
                    raise ValueError('原始电阻换算需要已核实的基础间距。')
                resistance=_resistance_value(value_input,scale)
                k=geometric_factor(abmn,spacing_value)
                rhoa=k*resistance
                if not math.isfinite(rhoa) or rhoa<=0:
                    raise ValueError(
                        f'K={k:.8g}, R={resistance:.8g} 换算视电阻率 {rhoa:.8g} Ω·m 不为正；'
                        '请检查电极角色或测量极性，不能用排序或绝对值修复。')
                record.update(abmn=abmn,abmn_input=[i+1 for i in abmn],
                    rhoa=rhoa,rhoa_input='',resistance=resistance,
                    resistance_input=str(value_input).strip(),k=k)
        except ValueError as error:
            record['error']=str(error)
        records.append(record)
    groups={}
    for record in records:
        if record['excluded'] or record['error']:continue
        groups.setdefault(tuple(record['abmn']),[]).append(record)
    for combo,group in groups.items():
        if len(group)<2:continue
        label='-'.join(str(index+1) for index in combo)
        message=f'电极组合 A/B/M/N={label} 重复；请排除其中一行。'
        for record in group:record['error']=message
    return records


def build_packet(path,sheet_name,rows,header_index,mapping,unit,electrodes,spacing,excluded=(),
                 array='wenner',value_kind='rhoa'):
    """Build and validate a packet from reviewed table rows."""
    auto=isinstance(array,str) and array.strip().lower()=='auto'
    detection=None
    if auto:
        array=None
    else:
        array=normalize_array(array)
    value_kind=normalize_value_kind(value_kind)
    path=Path(path).resolve()
    if isinstance(electrodes,bool) or not isinstance(electrodes,int) or not 4<=electrodes<=256:
        raise ValueError('电极数量须为 4–256 的整数。')
    try:spacing=float(spacing)
    except (TypeError,ValueError):raise ValueError('基础间距须为有效数值。') from None
    if not math.isfinite(spacing) or spacing<=0:raise ValueError('基础间距须大于0，并由操作者核实。')
    if auto:
        detection=detect_array(rows,header_index,mapping,excluded,electrodes)
        if detection['status']!='ok':
            raise ValueError(detection['message'])
        array=detection['array']
    records=analyze_rows(rows,header_index,mapping,unit,excluded,electrodes,
                         array=array,value_kind=value_kind,spacing=spacing)
    if not records:raise ValueError('表中没有可导入的观测行。')
    bad=[record for record in records if not record['excluded'] and record['error']]
    if bad:
        first=bad[0]
        raise ValueError(f'第 {first["source_row"]} 行仍有错误：{first["error"]} 请排除该行或修正源表。')
    output=[];excluded_count=0
    for record in records:
        if record['excluded']:
            excluded_count+=1
            continue
        entry=dict(abmn=record['abmn'],rhoa=record['rhoa'],
            source_row=record['source_row'],source_table=str(sheet_name),
            source_values=record['raw'],value_kind=value_kind)
        if value_kind=='resistance':
            entry.update(resistance=record['resistance'],k=record['k'])
        output.append(entry)
    if not output:raise ValueError('至少需要一条未排除的有效观测。')
    packet=dict(schema=1,array=array,source_file=str(path),source_name=path.name,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_table=str(sheet_name),source_header_row=header_index+1,
        source_unit=str(unit),source_mapping=dict(mapping),source_excluded_count=excluded_count,
        import_format='generic_table',electrodes=electrodes,spacing=spacing,
        start_distance=0.,rows=output,flat_assumed=True,value_kind=value_kind,
        coordinate_frame='测线局部坐标；未地理定位；无实测高程，平地假设',
        relative_error=.03,lam=20.,max_iter=12)
    if detection is not None:packet['array_detection']=detection
    return validate_packet(packet)


def is_instrument_csv(path):
    """Recognise the strict instrument export without validating its values.

    A recognised export must be parsed by ``observed_ert.read_observations``.
    Callers must not fall back to the generic table path when that strict parser
    rejects it.  ``.dat`` files are handled by the explicit R2 or strict
    Res2DInv parser at the workflow layer.
    """
    path=Path(path)
    suffix=path.suffix.lower()
    if suffix=='.dat':return True
    if suffix!='.csv':return False
    tables=read_tables(path)
    if not tables:return False
    for row in tables[0][1][:30]:
        headers=[str(value).strip() for value in row]
        lower={header.lower() for header in headers}
        if 'vp(mv)' in lower or 'ip(ma)' in lower:
            return True
        if 'k' in lower and all(name in headers for name in ('A','B','M','N')):
            return True
    return False
