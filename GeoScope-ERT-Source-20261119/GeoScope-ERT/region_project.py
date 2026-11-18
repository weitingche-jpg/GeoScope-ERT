"""Self-contained, auditable section snapshots for a terrain project."""
import copy
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
from core import place_section
from observed_ert import validate_packet
from workflow_geometry import observation_signature,validate_binding,generate_bound_points
from region_model import validate_sections,default_settings


def empty_region():
    return dict(schema=1,sections=[],settings=default_settings([]),generated_signature=None,
        display=dict(sections=True,volume=True,missing=True,mode='resistivity',opacity=.65,slice_z=-5.))


def validate_region_state(value,terrain):
    if value is None:return empty_region()
    if not isinstance(value,dict) or value.get('schema')!=1:raise ValueError('区域项目版本无效。')
    state=copy.deepcopy(value);state['sections']=validate_sections(state['sections'])
    for section in state['sections']:
        if section['terrain_source']!=terrain.source or section['crs']!=terrain.crs:raise ValueError('区域剖面属于另一地形或坐标系。')
    settings=state['settings'];bounds=np.asarray(settings['bounds'],float)
    if bounds.shape!=(6,) or not np.isfinite(bounds).all() or np.any(bounds[1::2]<=bounds[::2]):raise ValueError('区域范围无效。')
    for key in ('horizontal_step','vertical_step','max_gap'):
        if isinstance(settings[key],bool) or not np.isfinite(float(settings[key])) or float(settings[key])<=0:raise ValueError('区域参数无效。')
    display=state.get('display',{})
    for key in ('sections','volume','missing'):
        if not isinstance(display.get(key,True),bool):raise ValueError('区域显示状态无效。')
    if display.get('mode','resistivity') not in ('resistivity','classification','slice'):raise ValueError('区域显示方式无效。')
    if not np.isfinite(float(display.get('slice_z',-5))) or not .05<=float(display.get('opacity',.65))<=1:raise ValueError('区域显示参数无效。')
    state['display']={**empty_region()['display'],**display}
    return state


def workspace_document(window):
    terrain=getattr(window,'terrain',None)
    return dict(schema=1,terrain=terrain.source if terrain is not None else None,
        points=copy.deepcopy(window.points),line=copy.deepcopy(window.line),
        line_endpoint_overrides=copy.deepcopy(getattr(window,'line_endpoint_overrides',{}) or {}),
        case=str(window.case_path) if window.case_path else None,opacity=window.opacity.value(),
        points_follow_line=window.points_follow_line,ert_packet=copy.deepcopy(window.ert_packet),
        simulation_enabled=window.simulation_enabled,workflow_step=2,result_binding=copy.deepcopy(window.result_binding))


def _section(nodes,cells,rho,endpoints,kind,terrain,workspace,provenance,name):
    snapshot=dict(id=uuid4().hex,name=name or '测线剖面',kind=kind,crs=terrain.crs,terrain_source=terrain.source,
        enabled=True,nodes=np.asarray(nodes,float).tolist(),cells=np.asarray(cells,int).tolist(),
        rho=np.asarray(rho,float).tolist(),endpoints=np.asarray(endpoints,float).tolist(),
        workspace=copy.deepcopy(workspace),provenance=copy.deepcopy(provenance))
    validate_sections([snapshot]);return snapshot


def capture_current(window,name):
    if window.process is not None:raise ValueError('请等待计算完成。')
    if window.model is None:raise ValueError('请先完成测线反演。')
    if window.model_local_only:raise ValueError('请先将剖面配准到当前地形。')
    if window.result_stale:raise ValueError('当前结果已过期，请重新反演或恢复原计算输入。')
    if window.case_kind=='imported_observations':
        if not window.ert_packet or observation_signature(window.ert_packet)!=window.result_observation_signature:
            raise ValueError('无法验证观测输入与结果一致。')
        errors=validate_binding(window.ert_packet,window.ert_packet.get('binding',{}),window.points,window.line,window.terrain)
        if errors:raise ValueError('请先配准：'+'；'.join(errors))
    metadata_path=window.case_path/'metadata.json'
    meta=json.loads(metadata_path.read_text(encoding='utf8'))
    return _section(window.model.points+window.terrain.origin,window.section_data['cells'],window.section_data['resistivity'],
        window.section_data['xy'][[0,-1]],window.case_kind,window.terrain,workspace_document(window),
        dict(case=str(window.case_path),metadata=meta,fit_warning=window.fit_warning),name)


def section_from_project(path,terrain):
    path=Path(path);document=json.loads(path.read_text(encoding='utf8'))
    if document.get('schema')!=1 or document.get('terrain')!=terrain.source:raise ValueError('测线项目须使用当前同一地形文件。')
    case=Path(document.get('case') or '')
    if not case.is_absolute():case=(path.parent/case).resolve()
    meta=json.loads((case/'metadata.json').read_text(encoding='utf8'))
    if meta.get('crs')!=terrain.crs:raise ValueError('测线项目与当前坐标系不一致。')
    with np.load(case/'model.npz',allow_pickle=False) as raw:arrays={k:raw[k].copy() for k in raw.files}
    kind=meta.get('data_kind','synthetic');offset=0.
    packet=document.get('ert_packet')
    if kind=='imported_observations':
        packet=validate_packet(packet)
        if meta.get('observation_signature')!=observation_signature(packet):raise ValueError('测线结果已过期或缺少计算输入签名，请重新反演。')
        binding=packet.get('binding',{})
        if binding.get('mode')!='registered':raise ValueError('测线项目尚未地理配准。')
        errors=validate_binding(packet,binding,document['points'],document['line'],terrain)
        if errors:raise ValueError('配准失效：'+'；'.join(errors))
        points,_=generate_bound_points(packet,binding);xy=np.array([[p['x'],p['y']] for p in points]);offset=float(binding['origin'][2])
    else:
        xy=arrays['xy']
        actual=terrain.height(xy)
        if not np.isfinite(actual).all() or not np.allclose(actual,arrays['elevation'],rtol=0,atol=.05):raise ValueError('合成案例高程与当前地形不一致。')
        if not np.allclose(document['line'],meta['line_vertices'],rtol=0,atol=1e-6):raise ValueError('合成案例测线已改变，请重新计算。')
    station=np.asarray(arrays['station'],float)
    if station.ndim!=1 or len(station)<2 or not np.isfinite(station).all() or np.any(np.diff(station)<=0) or np.asarray(xy).shape!=(len(station),2):
        raise ValueError('测线里程或电极位置无效。')
    length=station[-1]-station[0];direction=(xy[-1]-xy[0])/length
    if not np.isclose(np.linalg.norm(xy[-1]-xy[0]),length,rtol=0,atol=.001):raise ValueError('测线定位必须保持反演里程实际尺寸，不能拉伸。')
    if not np.allclose(xy,xy[0]+(station-station[0])[:,None]*direction,rtol=0,atol=.001):raise ValueError('只支持直测线的刚性定位。')
    mesh=place_section(arrays['nodes'],arrays['cells'],arrays['resistivity'],xy,arrays['station'],np.zeros(3),float(arrays['z_reference'])+offset)
    document['case']=str(case)
    return _section(mesh.points,arrays['cells'],arrays['resistivity'],xy[[0,-1]],kind,terrain,document,
        dict(project=str(path.resolve()),case=str(case),metadata=meta),path.name.replace('.geoscope.json',''))
