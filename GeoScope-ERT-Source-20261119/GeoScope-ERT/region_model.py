"""Conservative interpolation BETWEEN independent parallel 2-D ERT sections.

Coordinates and vertical samples are absolute metres. No extrapolation, 3-D
inversion, inferred DOI, or copying a single line into a volume is performed.
"""
import copy
import hashlib
import json
import math
import numpy as np
from matplotlib.tri import Triangulation

MAX_CELLS=200000


def _array(value,shape_end,label,integer=False):
    raw=np.asarray(value)
    if integer and (raw.dtype.kind not in 'iu' or raw.dtype.kind=='b'):
        raise ValueError(label+'必须是整数索引。')
    a=np.asarray(value,dtype=int if integer else float)
    if a.ndim!=2 or a.shape[1]!=shape_end or not len(a) or not np.isfinite(a).all():
        raise ValueError(label+'形状或数值无效。')
    return a


def validate_sections(sections):
    if not isinstance(sections,list) or len(sections)>30:raise ValueError('区域最多支持30条剖面。')
    seen=set();identities=set();kinds=set()
    for section in sections:
        sid=section['id']
        if not isinstance(sid,str) or not sid.strip() or sid in seen:raise ValueError('剖面编号为空或重复。')
        seen.add(sid)
        if not isinstance(section.get('enabled',True),bool):raise ValueError('剖面启用状态无效。')
        kind=section['kind']
        if kind not in ('synthetic','imported_observations'):raise ValueError('剖面来源类别无效。')
        kinds.add(kind);identities.add((section['crs'],section['terrain_source']))
        if not all(isinstance(section[k],str) and section[k] for k in ('crs','terrain_source')):raise ValueError('缺少地形/坐标系身份。')
        nodes=_array(section['nodes'],3,'剖面节点');cells=_array(section['cells'],3,'剖面单元',True)
        ends=_array(section['endpoints'],2,'测线端点')
        if ends.shape!=(2,2):raise ValueError('只支持直测线的两个端点。')
        rho=np.asarray(section['rho'],float)
        if len(nodes)>100000 or len(cells)>50000:raise ValueError('单条剖面规模超限。')
        if rho.shape!=(len(cells),) or not np.isfinite(rho).all() or np.any(rho<=0):raise ValueError('电阻率须为有限正值且对应每个单元。')
        if cells.min()<0 or cells.max()>=len(nodes):raise ValueError('单元引用了不存在的节点。')
        delta=ends[1]-ends[0];length=np.linalg.norm(delta)
        if length<.001:raise ValueError('测线长度不足。')
        direction=delta/length;normal=np.array([-direction[1],direction[0]])
        if np.max(np.abs((nodes[:,:2]-ends[0])@normal))>.01:raise ValueError('节点不在所属直剖面上。')
        local=(nodes[:,:2]-ends[0])@direction
        verts=np.column_stack([local,nodes[:,2]])[cells]
        cross=(verts[:,1,0]-verts[:,0,0])*(verts[:,2,1]-verts[:,0,1])-(verts[:,1,1]-verts[:,0,1])*(verts[:,2,0]-verts[:,0,0])
        if np.any(np.abs(cross)<1e-12):raise ValueError('存在退化剖面单元。')
    if len(identities)>1:raise ValueError('所有剖面必须属于同一地形和坐标系。')
    if len(kinds)>1:raise ValueError('实测与模拟剖面不能混合生成区域模型。')
    return copy.deepcopy(sections)


def region_signature(sections,settings):
    fields=('id','kind','crs','terrain_source','endpoints','nodes','cells','rho')
    payload=[dict({k:s[k] for k in fields},enabled=s.get('enabled',True)) for s in sections]
    data=json.dumps(dict(sections=payload,settings=settings),sort_keys=True,allow_nan=False,separators=(',',':'))
    return hashlib.sha256(data.encode('utf8')).hexdigest()


def default_settings(sections):
    if not sections:return dict(bounds=[0,100,0,100,-20,0],horizontal_step=2.,vertical_step=1.,max_gap=30.)
    xy=np.concatenate([np.asarray(s['endpoints'],float) for s in sections]);nodes=np.concatenate([np.asarray(s['nodes'],float) for s in sections])
    low=xy.min(axis=0);high=xy.max(axis=0);padding=np.maximum((high-low)*.15,2.)
    return dict(bounds=[float(low[0]-padding[0]),float(high[0]+padding[0]),float(low[1]-padding[1]),float(high[1]+padding[1]),float(nodes[:,2].min()),float(nodes[:,2].max())],
        horizontal_step=2.,vertical_step=1.,max_gap=30.)


def _sampler(section,axis,base,t):
    ends=np.asarray(section['endpoints'],float);nodes=np.asarray(section['nodes'],float)
    direction=ends[1]-ends[0];length=np.linalg.norm(direction);direction/=length
    along=(t-(ends[0]-base)@axis)/(direction@axis)
    local=(nodes[:,:2]-ends[0])@direction
    triangles=np.asarray(section['cells'],int).copy()
    x=local[triangles];z=nodes[:,2][triangles]
    clockwise=(x[:,1]-x[:,0])*(z[:,2]-z[:,0])-(x[:,2]-x[:,0])*(z[:,1]-z[:,0])<0
    triangles[clockwise]=triangles[clockwise][:,[0,2,1]]
    try:finder=Triangulation(local,nodes[:,2],triangles).get_trifinder()
    except (RuntimeError,ValueError) as error:raise ValueError('剖面三角网无效或重叠：'+str(error)) from error
    def sample(z_query):
        index=finder(along,z_query);valid=(index>=0)&(along>=0)&(along<=length)
        values=np.full(len(t),np.nan);values[valid]=np.asarray(section['rho'])[index[valid]]
        return values
    xy=ends[0]+along[:,None]*direction
    return sample,xy


def build_region(sections,settings,terrain_height=None):
    sections=validate_sections(sections)
    selected=[s for s in sections if s.get('enabled',True)]
    if len(selected)<2:raise ValueError('至少需要两条不同位置的已配准剖面；单线不能生成区域体。')
    sources=set()
    for section in selected:
        if section['kind']!='imported_observations':continue
        provenance=section.get('provenance',{});packet=section.get('workspace',{}).get('ert_packet') or {}
        source=packet.get('source_sha256') or provenance.get('metadata',{}).get('source_sha256')
        rows=[dict(abmn=row['abmn'],rhoa=row['rhoa']) for row in packet.get('rows',[]) if row.get('enabled',True)]
        key=(source,json.dumps(rows,sort_keys=True,allow_nan=False)) if source else ('case',provenance.get('case'))
        if not any(key):continue
        if key==('case',None):continue
        if key in sources:raise ValueError('实测来源重复：同一组观测重新定位不能作为独立测线生成区域。')
        sources.add(key)
    bounds=np.asarray(settings['bounds'],float)
    if bounds.shape!=(6,) or not np.isfinite(bounds).all() or np.any(bounds[1::2]<=bounds[::2]):raise ValueError('区域范围须为有效的X/Y/绝对高程上下限。')
    h=float(settings['horizontal_step']);v=float(settings['vertical_step']);gap=float(settings['max_gap'])
    if not all(math.isfinite(x) and x>0 for x in (h,v,gap)):raise ValueError('网格步长与允许线距须为有限正数。')
    size=(bounds[1::2]-bounds[::2]);counts=np.ceil(size/[h,h,v])
    if np.prod(counts)>MAX_CELLS:raise ValueError(f'网格超过{MAX_CELLS}单元，请增大步长或缩小范围。')
    shape=counts.astype(int);spacing=size/shape;origin=bounds[::2]
    axes=[origin[i]+(np.arange(shape[i])+.5)*spacing[i] for i in range(3)]
    xyz=np.column_stack([a.ravel(order='F') for a in np.meshgrid(*axes,indexing='ij')])
    base=np.asarray(selected[0]['endpoints'][0],float)
    axis=np.subtract(*np.asarray(selected[0]['endpoints'])[::-1]);axis=axis/np.linalg.norm(axis)
    normal=np.array([-axis[1],axis[0]]);t=(xyz[:,:2]-base)@axis;c=(xyz[:,:2]-base)@normal
    candidates=[]
    for s in selected:
        d=np.subtract(*np.asarray(s['endpoints'])[::-1]);d=d/np.linalg.norm(d)
        if abs(d@axis)<math.cos(math.radians(1.)):raise ValueError('首版区域插值要求近似平行直测线，夹角须不超过1度。')
        sample,xy=_sampler(s,axis,base,t)
        cross=(xy-base)@normal;candidates.append((float(np.mean(cross)),cross,sample(xyz[:,2])))
    candidates.sort(key=lambda item:item[0]);rho=np.full(len(xyz),np.nan);near=np.full(len(xyz),np.inf)
    for item in candidates:near=np.minimum(near,np.abs(c-item[1]))
    for left,right in zip(candidates,candidates[1:]):
        width=right[1]-left[1]
        if np.any(width<=.001):raise ValueError('测线位置重复或在区域范围内交叉，不能插值。')
        valid=(c>=left[1])&(c<=right[1])&(width<=gap)&np.isfinite(left[2])&np.isfinite(right[2])
        f=(c[valid]-left[1][valid])/width[valid]
        rho[valid]=np.exp((1-f)*np.log(left[2][valid])+f*np.log(right[2][valid]))
    if terrain_height is not None:
        ground=np.asarray(terrain_height(xyz[:,:2]),float)
        if ground.shape!=(len(xyz),):raise ValueError('地形采样形状无效。')
        rho[(~np.isfinite(ground))|(xyz[:,2]>ground)]=np.nan
    classification=np.zeros(len(xyz),dtype=np.uint8);valid=np.isfinite(rho)
    classification[valid]=2;classification[valid&(near<=min(spacing[:2])*.5+1e-9)]=1
    if not valid.any():raise ValueError('没有可插值的有效单元：请检查共同深度、区域范围、线间距和地形。')
    stats={name:int(np.count_nonzero(classification==i)) for i,name in enumerate(('missing','near_section','interpolated'))}
    return dict(origin=origin,spacing=spacing,shape=shape,centers=xyz,rho=rho,classification=classification,
        stats=stats,signature=region_signature(sections,settings))
