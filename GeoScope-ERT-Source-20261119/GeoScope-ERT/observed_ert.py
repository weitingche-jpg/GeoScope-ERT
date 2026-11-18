"""Validated Wenner and dipole-dipole observations, independent of Qt.

Schema 1 packets keep positive apparent resistivity (``rhoa``) for the solver.
A dipole-dipole packet may also retain the measured transfer resistance and the
signed geometric factor; these are never used to hide a polarity error by
sorting electrode roles or taking an absolute value.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from point_files import read_tables
from ert_quality import active_indices
from ert_arrays import (normalize_array, validate_abmn,
                        signed_geometric_factor)


def number(value):
    value=float(value)
    if not math.isfinite(value):raise ValueError('数据含非有限数值。')
    return value


def index(value):
    value=number(value)
    if value<1 or value!=int(value):raise ValueError('电极编号须为从1开始的正整数。')
    return int(value)-1


def validate_packet(packet,allow_empty=False):
    array=normalize_array(packet.get('array','wenner'))
    if packet.get('schema')!=1:raise ValueError('仅支持 schema 1 观测包。')
    count=packet['electrodes'];spacing=number(packet['spacing'])
    if not isinstance(count,int) or not 4<=count<=256 or spacing<=0:raise ValueError('电极数量或间距无效。')
    if not (0 if allow_empty else 1)<=len(packet['rows'])<=10000:raise ValueError('观测数量须为1–10000；无观测可保存草稿，但不可反演。')
    from workflow_geometry import electrode_ids
    electrode_ids(packet)
    error=number(packet.get('relative_error',.03));lam=number(packet.get('lam',20));iterations=number(packet.get('max_iter',12))
    if not .001<=error<=.3 or not .1<=lam<=1000 or iterations!=int(iterations) or not 1<=iterations<=30:
        raise ValueError('反演参数无效。')
    seen=set()
    active=set(active_indices(packet))
    if not active and not allow_empty:raise ValueError('至少启用一条观测才能反演；全部停用可保存为草稿。')
    for row_index,row in enumerate(packet['rows']):
        if row_index not in active and not str(row.get('qc_note','')).strip():
            raise ValueError('停用观测必须记录处理原因。')
        if not all(type(value) is int for value in row['abmn']):
            raise ValueError('观测包的电极编号须为整数类型，不能是字符串、浮点数或布尔值。')
        try:
            abmn=list(validate_abmn(array,row['abmn'],count))
        except ValueError as error:
            raise ValueError(str(error)) from error
        if number(row['rhoa'])<=0:raise ValueError('视电阻率须大于0。')
        if 'resistance' in row:
            if 'k' not in row:raise ValueError('原始电阻须与几何因子一同保留。')
            resistance=number(row['resistance']);factor=number(row['k'])
            expected=signed_geometric_factor(abmn,spacing)
            if not math.isclose(factor,expected,rel_tol=.01,abs_tol=1e-9):
                raise ValueError('几何因子与电极排列/间距不一致。')
            if not math.isclose(number(row['rhoa']),factor*resistance,
                                rel_tol=.005,abs_tol=1e-6):
                raise ValueError('视电阻率与原始电阻×几何因子不一致。')
        elif 'k' in row:
            factor=number(row['k'])
            if array=='dipole-dipole':
                expected=signed_geometric_factor(abmn,spacing)
                if not math.isclose(factor,expected,rel_tol=.01,abs_tol=1e-9):
                    raise ValueError('几何因子与电极排列/间距不一致。')
        key=tuple(abmn)
        if key in seen:raise ValueError('同一电极组合重复，请分别处理重复测量。')
        seen.add(key)
    return packet


def read_observations(path):
    path=Path(path).resolve();rows=[];spacing=2.;electrodes=0;start=0.
    if path.suffix.lower()=='.csv':
        _,table=read_tables(path)[0]
        table=[r for r in table if any(str(v).strip() for v in r)]
        header=[str(h).strip() for h in table[0]]
        def column(name):
            found=[i for i,h in enumerate(header) if h==name]
            if not found:raise ValueError(f'仪器CSV缺少字段 {name}。请选择含电压、电流、ρs及末尾A/M/N/B的原始导出表。')
            return found[-1]
        columns=[column(k) for k in ('A','B','M','N')]
        rho=next((i for i,h in enumerate(header) if h.startswith('ρs') or h.lower().startswith('rhoa')),None)
        if rho is None:raise ValueError('缺少视电阻率字段。')
        vp,ip,k=column('vp(mv)'),column('ip(mA)'),column('K')
        metadata=path.with_name(path.stem+'_rt.dat')
        spacing_confirmed=False
        if metadata.exists():
            text=metadata.read_bytes().decode('gb18030')
            fields={line.split(':',1)[0].strip():line.split(':',1)[1].strip() for line in text.splitlines() if ':' in line}
            spacing=number(fields['SPACING']);electrodes=int(fields['NUM_POLES'])
            if fields.get('ARRAY')!='1':raise ValueError('伴随RT文件不是温纳装置。')
            start=number(fields.get('X_START',0));spacing_confirmed=True
        for line,record in enumerate(table[1:],2):
            try:
                abmn=[index(record[c]) for c in columns]
                validate_abmn('wenner',abmn)
                value=number(record[rho]);v=number(record[vp]);i=number(record[ip]);factor=number(record[k])
                if i==0 or not math.isclose(value,factor*v/i,rel_tol=.005,abs_tol=.001):
                    raise ValueError('ρs 与 K×电压/电流不一致。')
                rows.append(dict(abmn=abmn,rhoa=value,source_row=line,voltage_mV=v,current_mA=i,k=factor))
            except (ValueError,IndexError) as error:raise ValueError(f'CSV第{line}行：{error}') from error
        if not electrodes:electrodes=max(i for r in rows for i in r['abmn'])+1
        # K=2*pi*a is independent evidence for the physical spacing.
        if not spacing_confirmed:
            inferred=[r['k']/(2*math.pi*(r['abmn'][2]-r['abmn'][0])) for r in rows]
            spacing=float(np.median(inferred))
        for row in rows:
            expected=2*math.pi*(row['abmn'][2]-row['abmn'][0])*spacing
            if not math.isclose(row['k'],expected,rel_tol=.005,abs_tol=.001):raise ValueError('几何因子与间距/温纳排列不一致。')
    elif path.suffix.lower()=='.dat':
        try:text=path.read_text(encoding='utf-8-sig')
        except UnicodeError:text=path.read_text(encoding='gb18030')
        lines=[s.strip() for s in text.splitlines() if s.strip()]
        if len(lines)<7 or lines[2]!='1' or lines[4]!='1' or lines[5]!='0':
            raise ValueError('仅支持温纳、中心位置、视电阻率模式的Res2DInv样式DAT；请优先选择仪器CSV。')
        spacing=number(lines[1]);count=int(lines[3]);triples=[]
        if spacing<=0:raise ValueError('DAT电极间距须大于0。')
        if not 1<=count<=10000 or len(lines)<6+count:raise ValueError('DAT观测数量不完整。')
        if any(number(t)!=0 for t in lines[6+count:]):raise ValueError('含地形/其他扩展的DAT暂不支持，请提供仪器CSV及独立高程。')
        for line in lines[6:6+count]:
            triple=[number(v) for v in line.split()]
            if len(triple)!=3:raise ValueError('DAT每条观测应有中心位置、间距、视电阻率三列。')
            triples.append(triple)
        start=min(x-1.5*a for x,a,rho in triples)
        for row_index,(x,a,rho) in enumerate(triples,7):
            first=(x-1.5*a-start)/spacing;step=a/spacing
            if not math.isclose(first,round(first),abs_tol=1e-5) or not math.isclose(step,round(step),abs_tol=1e-5):
                raise ValueError('DAT位置不符合电极网格。')
            first,step=int(round(first)),int(round(step))
            rows.append(dict(abmn=[first,first+3*step,first+step,first+2*step],rhoa=rho,source_row=row_index))
        electrodes=max(i for row in rows for i in row['abmn'])+1
    else:raise ValueError('请选择R2 protocol.dat、仪器CSV或温纳Res2DInv样式DAT。')
    packet=dict(schema=1,array='wenner',source_file=str(path),source_name=path.name,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),electrodes=electrodes,
        spacing=spacing,start_distance=start,rows=rows,flat_assumed=True,
        coordinate_frame='测线局部坐标；未地理定位；无实测高程，平地假设',
        relative_error=.03,lam=20.,max_iter=12)
    return validate_packet(packet)


def geometry_signature(points,line):
    data=dict(points=[{key:p[key] for key in ('id','x','y','z')} for p in points],line=line)
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()


def create_local_dataset(folder,packet):
    import rasterio
    from rasterio.transform import from_origin
    packet=validate_packet(copy.deepcopy(packet));folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    length=(packet['electrodes']-1)*packet['spacing'];pad=max(5.,length*.1)
    path=folder/'local-profile.tif'
    step=max(.25,(length+2*pad)/200)
    width=int(math.ceil((length+2*pad)/step));height=21
    with rasterio.open(path,'w',driver='GTiff',count=1,dtype='float32',width=width,height=height,
            crs='EPSG:3857',transform=from_origin(-pad,pad,step,2*pad/height)) as dst:
        dst.write(np.zeros((height,width),dtype=np.float32),1)
        dst.update_tags(coordinate_frame='LOCAL_ONLY_NOT_GEOGRAPHIC',elevation='ASSUMED_FLAT_NOT_MEASURED')
    Path(str(path)+'.ert.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    points=[dict(id=f'E{i+1:02}',x=float(i*packet['spacing']),y=0.,z=0.,elevation_source='平地假设（未测高程）') for i in range(packet['electrodes'])]
    return path,points,[[0.,0.],[float(length),0.]]
