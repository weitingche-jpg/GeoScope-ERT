"""Parallel survey workspaces and explicit hypothetical acquisition geometry."""
import copy
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from observed_ert import validate_packet
from calibration import make_proposal
from workflow_geometry import is_map_line_binding,observation_signature,validate_binding
from ert_quality import active_indices
from ert_arrays import array_label,normalize_array,signed_geometric_factor
from endpoint_edit import effective_endpoint_overrides,endpoint_overrides_conflict
from line_import import is_local_import


def empty_survey():
    return dict(schema=1,lines=[],active_id=None,hypothesis=False,
        calibration=dict(origin=[0.,0.,0.],angle=0.,gap=2.),volume_case=None,view='sections')


def validate_survey(state,terrain):
    if state is None:return empty_survey()
    state=copy.deepcopy(state)
    if state.get('schema')!=1 or not isinstance(state.get('hypothesis'),bool):raise ValueError('测网格式或假设标志无效。')
    if not isinstance(state.get('lines'),list) or len(state['lines'])>30:raise ValueError('最多支持30条测线。')
    ids=set()
    for line in state['lines']:
        if not isinstance(line.get('id'),str) or not line['id'] or line['id'] in ids:raise ValueError('测线编号重复或为空。')
        ids.add(line['id'])
        if not isinstance(line.get('selected'),bool):raise ValueError('测线参与状态无效。')
        if 'visible' not in line:line['visible']=True
        if not isinstance(line.get('visible'),bool):raise ValueError('测线显示状态无效。')
        ws=line['workspace']
        local_import=is_local_import(ws.get('ert_packet'))
        if local_import:
            # Explicitly marked local imported lines may be stored without a
            # terrain.  Their station coordinates are not geographic.
            if terrain is not None and ws.get('terrain') is not None and ws.get('terrain')!=terrain.source:
                raise ValueError('局部测线属于另一地形。')
        elif terrain is None:
            raise ValueError('测线没有明确标记为局部导入，无地表时不能恢复。')
        elif ws.get('terrain')!=terrain.source:raise ValueError('测线属于另一地形。')
        overrides=ws.get('line_endpoint_overrides',{})
        if not isinstance(overrides,dict):raise ValueError('测线端点高程覆盖格式无效。')
        ws['line_endpoint_overrides']=copy.deepcopy(overrides)
        if ws.get('ert_packet') is not None:validate_packet(ws['ert_packet'],allow_empty=True)
        if not isinstance(ws.get('points'),list) or not isinstance(ws.get('line'),list):raise ValueError('测线几何无效。')
    if state.get('active_id') not in ids and ids:raise ValueError('活动测线不存在。')
    if state.get('view') not in ('sections','volume'):raise ValueError('测网视图无效。')
    return state


def make_survey(packets,terrain,origin,angle=0.,gap=2.,hypothesis=False):
    if not 1<=len(packets)<=30:raise ValueError('测线数量须为1–30。')
    state=empty_survey();state['hypothesis']=bool(hypothesis)
    for i,packet in enumerate(packets,1):
        packet=copy.deepcopy(validate_packet(packet));packet['confirmed']=True
        if packet.get('geometry_hypothesis') and not hypothesis:raise ValueError('已有假设几何标志不能静默转为真实采集。')
        packet['geometry_hypothesis']=bool(hypothesis)
        state['lines'].append(dict(id=f'L{i:02}',name=f'L{i:02}',selected=True,visible=True,
            workspace=dict(schema=1,terrain=terrain.source,points=[],line=[],line_endpoint_overrides={},case=None,
                ert_packet=packet,points_follow_line=True,simulation_enabled=False,result_binding=None,workflow_step=1,opacity=40)))
    state['active_id']=state['lines'][0]['id']
    return parallel_calibrate(state,terrain,origin,angle,gap)


def parallel_calibrate(state,terrain,origin,angle,gap):
    state=validate_survey(state,terrain);origin=np.asarray(origin,float);gap=float(gap);angle=float(angle)
    if origin.shape!=(3,) or not np.isfinite(origin).all() or not math.isfinite(angle) or not math.isfinite(gap) or gap<=0:raise ValueError('平行校准参数无效。')
    normal=np.array([-math.sin(math.radians(angle)),math.cos(math.radians(angle))])
    for i,line in enumerate(state['lines']):
        ws=line['workspace'];packet=ws.get('ert_packet')
        if packet is None:raise ValueError(line['name']+'尚无观测，请先导入观测以确定真实电极间距。')
        start=origin.copy();start[:2]+=normal*i*gap
        proposal=make_proposal(packet,start,angle,terrain)
        ws['points']=proposal['points'];ws['line']=proposal['line'];packet['binding']=proposal['binding']
        ws['line_endpoint_overrides']={}
        ws['points_follow_line']=True
    state['calibration']=dict(origin=origin.tolist(),angle=angle,gap=gap)
    state['view']='sections'
    return state


def survey_signature(state):
    if not state or not state.get('lines'):return None
    payload=[]
    for line in state['lines']:
        if not line['selected']:continue
        ws=line['workspace'];packet=ws.get('ert_packet')
        entry=dict(id=line['id'],points=[{k:p[k] for k in ('id','x','y','z')} for p in ws['points']],line=ws['line'],
            observations=observation_signature(packet) if packet else None)
        endpoint_overrides=effective_endpoint_overrides(ws['line'],ws.get('line_endpoint_overrides') or {})
        if endpoint_overrides:entry['endpoint_overrides']=endpoint_overrides
        payload.append(entry)
    raw=json.dumps(dict(hypothesis=state['hypothesis'],lines=payload),sort_keys=True,allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def volume_inputs(state,terrain,require_sections=True):
    state=validate_survey(state,terrain);lines=[line for line in state['lines'] if line['selected']]
    # Explicitly gate dipole-dipole before any Wenner-specific count/K logic,
    # including the three-line minimum, so users see the real reason.
    for line in lines:
        packet=line['workspace'].get('ert_packet')
        if packet is None:continue
        array=normalize_array(packet.get('array','wenner'))
        if array!='wenner':
            raise ValueError(
                f'{line["name"]}为{array_label(array)}观测：该排列的三维验收尚未完成，'
                '本版三维入口只接受温纳排列；不能套用温纳几何因子K。请先按二维剖面单独验收。')
    if len(lines)<3:raise ValueError('首版三维求解至少选择3条不同位置的平行测线；此门槛不保证分辨率。')
    sensors=[];abmn=[];resistance=[];error=[];mapping=[];starts=[];directions=[];sourcekeys=set()
    for line in lines:
        ws=line['workspace']
        if ws.get('ert_packet') is None:raise ValueError(line['name']+'尚未导入观测，请先补充当前测线观测。')
        packet=validate_packet(ws['ert_packet'])
        array=normalize_array(packet.get('array','wenner'))
        if array!='wenner':
            raise ValueError(
                f'{line["name"]}为{array_label(array)}观测：该排列的三维验收尚未完成，'
                '本版三维入口只接受温纳排列；不能套用温纳几何因子K。请先按二维剖面单独验收。')
        if packet.get('binding',{}).get('mode')!='registered':raise ValueError(line['name']+'尚未配准到当前地表，不能进入整体三维反演。')
        errors=validate_binding(packet,packet.get('binding',{}),ws['points'],ws['line'],terrain)
        if errors:raise ValueError(line['name']+'需校准：'+'；'.join(errors))
        if require_sections:
            folder=Path(ws.get('case') or '')
            if not (folder/'metadata.json').is_file():raise ValueError(line['name']+'尚未生成当前剖面。')
            meta=json.loads((folder/'metadata.json').read_text(encoding='utf8'))
            if meta.get('observation_signature')!=observation_signature(packet):raise ValueError(line['name']+'的剖面已过期。')
            overrides=ws.get('line_endpoint_overrides') or {}
            if not is_map_line_binding(packet) and endpoint_overrides_conflict(ws['line'],overrides,ws['points']):
                raise ValueError(line['name']+'端点坐标与实际电极不同，请重新校准并生成剖面。')
        if packet.get('geometry_hypothesis') and not state['hypothesis']:raise ValueError('假设测线不能作为真实采集几何计算。')
        sourcekey=(packet.get('source_sha256'),json.dumps([(r['abmn'],r['rhoa']) for r in packet['rows']]))
        if not state['hypothesis'] and sourcekey[0] and sourcekey in sourcekeys:raise ValueError('重复采集来源不能当成独立测线。')
        sourcekeys.add(sourcekey)
        coordinates=np.array([[p['x'],p['y'],p['z']] for p in ws['points']],float)
        start=coordinates[0];direction=coordinates[-1,:2]-coordinates[0,:2];direction/=np.linalg.norm(direction)
        starts.append(start);directions.append(direction)
        offset=len(sensors);sensors.extend(coordinates.tolist())
        for row_index in active_indices(packet):
            row=packet['rows'][row_index];a,b,m,n=row['abmn'];k=signed_geometric_factor(row['abmn'],packet['spacing'])
            if row.get('voltage_mV') is not None and row.get('current_mA') is not None:
                if float(row['current_mA'])==0:raise ValueError('供电电流不能为0。')
                r=float(row['voltage_mV'])/float(row['current_mA'])
                if not math.isclose(r*k,row['rhoa'],rel_tol=.005,abs_tol=.001):raise ValueError(line['name']+'视电阻率与电压电流矛盾，请检查编辑值。')
            else:r=float(row['rhoa'])/k
            if not math.isfinite(r) or r<=0:raise ValueError('三维观测电阻须为有限正值。')
            abmn.append([offset+v for v in (a,b,m,n)]);resistance.append(r);error.append(float(packet['relative_error']))
            mapping.append(dict(line_id=line['id'],source_row_index=row_index,source_file=packet.get('source_file')))
    if len(sensors)>720 or len(abmn)>10000:raise ValueError('首版三维计算规模上限为720个电极、10000条启用观测，请减少参与测线。')
    xyz=np.asarray(sensors);starts=np.asarray(starts);axis=directions[0];normal=np.array([-axis[1],axis[0]])
    if any(abs(d@axis)<math.cos(math.radians(1)) for d in directions):raise ValueError('首版三维工作流要求平行直测线。')
    cross=np.sort(starts[:,:2]@normal)
    if np.any(np.diff(cross)<.01):raise ValueError('测线重合或间距不足，不能作为区域测网。')
    if np.ptp(xyz[:,2])>.1:raise ValueError('首版三维验证仅支持已确认的平地电极高程，不能把起伏高程直接当平地。')
    return dict(sensors=sensors,abmn=abmn,resistance=resistance,error=error,source_mapping=mapping,
        geometry_hypothesis=state['hypothesis'],source_relation='same-line repeated measurements, assigned hypothetical parallel geometry' if state['hypothesis'] else 'operator-provided independent survey geometry',
        signature=survey_signature(state),crs=terrain.crs,terrain_source=terrain.source)
