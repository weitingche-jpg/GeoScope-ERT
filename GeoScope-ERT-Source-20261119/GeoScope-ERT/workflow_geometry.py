"""Numerical registration and signatures, with no GUI or solver dependencies."""
import hashlib
import json
import math
from ert_quality import active_indices
from ert_arrays import normalize_array, validate_abmn


MAP_LINE_ELEVATION_SOURCE = '地图测线布点（地形插值）'


def _number(value):
    value=float(value)
    if not math.isfinite(value):
        raise ValueError('数据必须是有限数值，不能含 NaN 或 Inf。')
    return 0.0 if value==0 else value


def _xyz(value):
    if len(value)!=3:
        raise ValueError('端点须包含 X、Y、Z 三个坐标。')
    return [_number(v) for v in value]


def _layout(packet):
    count=packet['electrodes']
    if isinstance(count,bool) or not isinstance(count,int) or not 4<=count<=256:
        raise ValueError('电极数量须为 4–256 的整数。')
    spacing=_number(packet['spacing'])
    if spacing<=0:
        raise ValueError('电极间距须大于零。')
    length=_number((count-1)*spacing)
    return count,spacing,length


def _tolerance(value):
    value=_number(value)
    if not 0<value<=10:
        raise ValueError('配准容差须大于零且不超过 10 米。')
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()


def physical_signature(points,line):
    """Hash row-ordered physical XYZ and line, excluding labels and metadata."""
    vertices=[]
    for vertex in line:
        if len(vertex) not in (2,3):
            raise ValueError('测线顶点须为二维或三维坐标。')
        vertices.append([_number(v) for v in vertex])
    return _digest(dict(points=[[_number(p[k]) for k in ('x','y','z')] for p in points],line=vertices))


def observation_signature(packet):
    """Hash only observation geometry/data and inversion parameters.

    Legacy Wenner payloads deliberately keep their original digest shape.  A
    dipole-dipole payload adds ``array`` so the same numbers under a different
    array can never share a result signature.
    """
    array=normalize_array(packet.get('array','wenner'))
    count,spacing,_=_layout(packet)
    rows=[]
    for row_index in active_indices(packet):
        row=packet['rows'][row_index]
        indices=[_number(v) for v in row['abmn']]
        try:
            indices=list(validate_abmn(array,indices,count))
        except ValueError as error:
            raise ValueError(str(error)) from error
        indices=[float(value) for value in indices]
        rho=_number(row['rhoa'])
        if rho<=0:
            raise ValueError('视电阻率须大于零。')
        rows.append(dict(abmn=indices,rhoa=rho))
    error=_number(packet.get('relative_error',.03))
    lam=_number(packet.get('lam',20.))
    iterations=_number(packet.get('max_iter',12))
    if error<=0 or lam<=0 or iterations<1 or iterations!=int(iterations):
        raise ValueError('反演参数无效。')
    payload=dict(count=count,spacing=spacing,rows=rows,
        relative_error=error,lam=lam,max_iter=iterations)
    if array!='wenner':
        payload['array']=array
    return _digest(payload)


def electrode_ids(packet):
    count,_,_=_layout(packet)
    ids=list(packet.get('electrode_ids',[f'E{i+1:02}' for i in range(count)]))
    if len(ids)!=count or any(not isinstance(v,str) or not v.strip() for v in ids) or len(set(ids))!=count:
        raise ValueError('电极名称须非空、唯一且与电极数量一致。')
    return ids


def _map_line(value):
    """Normalize a user map line to two finite horizontal endpoints."""
    if not isinstance(value,(list,tuple)) or len(value)!=2:
        raise ValueError('用户地图测线须由起点和终点两个端点组成。')
    endpoints=[]
    for vertex in value:
        if not isinstance(vertex,(list,tuple)) or len(vertex) not in (2,3):
            raise ValueError('用户地图测线端点须为二维或三维坐标。')
        endpoints.append([_number(v) for v in vertex][:2])
    return endpoints


def _terrain_bounds(terrain):
    xs=[_number(v) for v in terrain.x];ys=[_number(v) for v in terrain.y]
    if not xs or not ys or min(xs)>=max(xs) or min(ys)>=max(ys):
        raise ValueError('地形范围无效。')
    return min(xs),max(xs),min(ys),max(ys)


def _terrain_line_heights(terrain,endpoints,samples=257):
    """Check the whole user line and return sampled terrain heights."""
    if terrain is None:
        raise ValueError('请先加载地形。')
    xmin,xmax,ymin,ymax=_terrain_bounds(terrain)
    (x0,y0),(x1,y1)=endpoints
    if not (xmin<=x0<=xmax and ymin<=y0<=ymax and xmin<=x1<=xmax and ymin<=y1<=ymax):
        raise ValueError('地图测线超出地形范围。')
    count=max(2,min(2048,int(samples)))
    xy=[[x0+(x1-x0)*i/(count-1),y0+(y1-y0)*i/(count-1)] for i in range(count)]
    try:
        heights=[float(v) for v in terrain.height(xy)]
    except Exception as error:
        raise ValueError(f'地形高程采样失败：{error}') from error
    if len(heights)!=len(xy) or any(not math.isfinite(v) for v in heights):
        raise ValueError('地图测线超出有效地形覆盖范围。')
    return heights


def _points_on_map_line(points,line,tolerance):
    (x0,y0),(x1,y1)=line
    dx,dy=x1-x0,y1-y0
    span=math.hypot(dx,dy)
    ux,uy=dx/span,dy/span
    first=points[0]
    if math.hypot(_number(first['x'])-x0,_number(first['y'])-y0)>tolerance:
        return False
    for point in points:
        px=_number(point['x'])-x0;py=_number(point['y'])-y0
        along=px*ux+py*uy
        if abs(px*uy-py*ux)>tolerance or along<-tolerance or along>span+tolerance:
            return False
    return True


def fit_endpoints(packet,start_xyz,end_xyz,tolerance=.1):
    """Fit a scale-one horizontal rigid transform; never stretch spacing."""
    _,_,length=_layout(packet)
    tolerance=_tolerance(tolerance)
    start,end=_xyz(start_xyz),_xyz(end_xyz)
    dx,dy=end[0]-start[0],end[1]-start[1]
    distance=_number(math.hypot(dx,dy))
    residual=abs(distance-length)
    height=abs(end[2]-start[2])
    if distance<=0 or residual>tolerance:
        raise ValueError(f'端点水平距离须与电极总长 {length:g} 米一致，当前差值 {residual:g} 米。')
    if height>tolerance:
        raise ValueError('端点高差超过容差；当前引擎仅支持平地测线。')
    return dict(mode='registered',origin=start,angle=math.atan2(dy,dx),length=length,
                endpoint_residual=residual,height_residual=height,tolerance=tolerance)


def generate_bound_points(packet,binding):
    count,spacing,length=_layout(packet)
    if binding.get('mode')!='registered':
        raise ValueError('需要已配准的测线。')
    origin=_xyz(binding['origin']);angle=_number(binding['angle'])
    tolerance=_tolerance(binding['tolerance'])
    if abs(_number(binding['length'])-length)>1e-8*max(1.,length):
        raise ValueError('配准总长已与观测包不一致，请重新配准。')
    for field in ('endpoint_residual','height_residual'):
        residual=_number(binding[field])
        if not 0<=residual<=tolerance:
            raise ValueError('配准残差超过容差。')
    if _number(binding.get('scale',1))!=1:
        raise ValueError('配准不允许缩放。')
    dx,dy=math.cos(angle),math.sin(angle)
    map_line=None
    if 'map_line' in binding:
        map_line=_map_line(binding['map_line'])
        vx=map_line[1][0]-map_line[0][0];vy=map_line[1][1]-map_line[0][1]
        map_length=math.hypot(vx,vy)
        if map_length==0:
            raise ValueError('地图测线起点和终点不能重合。')
        dx,dy=vx/map_length,vy/map_length
    points=[dict(id=identifier,x=_number(origin[0]+i*spacing*dx),
        y=_number(origin[1]+i*spacing*dy),z=origin[2],
        elevation_source='配准定位（平地假设）') for i,identifier in enumerate(electrode_ids(packet))]
    if map_line is not None and abs(map_length-length)<=1e-9:
        points[-1].update(x=map_line[1][0],y=map_line[1][1])
    line=[[points[0]['x'],points[0]['y']],[points[-1]['x'],points[-1]['y']]]
    return points,line


def is_map_line_binding(packet):
    """True only for a registered packet carrying original map-line endpoints."""
    if not isinstance(packet,dict):
        return False
    binding=packet.get('binding')
    return (isinstance(binding,dict) and binding.get('mode')=='registered'
            and 'map_line' in binding)


def fit_map_line(packet,line,terrain):
    """Place the observed electrode row from the user map-line start.

    The row keeps the packet's real spacing and identifiers.  The user line
    supplies the start point and direction only; it must be long enough to
    contain the fixed metric span, but it is not stretched or shortened.
    """
    count,spacing,length=_layout(packet)
    if terrain is None:
        raise ValueError('请先加载地形，再按地图测线布设电极。')
    endpoints=_map_line(line)
    dx=endpoints[1][0]-endpoints[0][0];dy=endpoints[1][1]-endpoints[0][1]
    line_length=math.hypot(dx,dy)
    if line_length+1e-9<length:
        reason='地图测线起点和终点重合' if line_length==0 else '地图测线过短'
        raise ValueError(f'{reason}：{count} 个电极按 {spacing:g} 米间距所需长度 '
                         f'{length:g} 米，当前仅 {line_length:g} 米。')
    heights=_terrain_line_heights(terrain,endpoints)
    start=[endpoints[0][0],endpoints[0][1],heights[0]]
    binding=dict(mode='registered',origin=start,angle=math.atan2(dy,dx),length=length,
        endpoint_residual=0.,height_residual=0.,tolerance=.1,map_line=endpoints,
        terrain_source=getattr(terrain,'source',None))
    points,_=generate_bound_points(packet,binding)
    heights=terrain.height([[point['x'],point['y']] for point in points])
    if len(heights)!=count or any(not math.isfinite(float(z)) for z in heights):
        raise ValueError('部分电极位置没有有效地形高程。')
    for point,height in zip(points,heights):
        point.update(z=_number(height),elevation_source=MAP_LINE_ELEVATION_SOURCE)
    binding['geometry_signature']=physical_signature(points,endpoints)
    problems=validate_binding(packet,binding,points,endpoints,terrain)
    if problems:
        raise ValueError('\n'.join(problems))
    return binding,points


def validate_binding(packet,binding,points,line,terrain):
    """Return user-facing errors, including malformed inputs; local is unbound."""
    try:
        if binding.get('mode')=='local':
            return []
        if binding.get('mode')!='registered':
            return ['尚未完成地理配准。']
        map_line=_map_line(binding['map_line']) if 'map_line' in binding else None
        expected,expected_line=generate_bound_points(packet,binding)
        tolerance=_tolerance(binding['tolerance'])
        _,_,required=_layout(packet)
        errors=[]
        if binding.get('terrain_source') is not None and binding['terrain_source']!=getattr(terrain,'source',None):
            errors.append('目标地形已改变，请重新配准。')
        if physical_signature(points,line)!=binding.get('geometry_signature'):
            errors.append('测点或测线坐标已改变，请重新配准。')
        if [p['id'] for p in points]!=electrode_ids(packet):
            errors.append('电极名称或行顺序与观测包不一致。')
        if len(points)!=len(expected):
            errors.append('电极数量与观测包不一致。')
        else:
            reference_heights=(
                [p['z'] for p in expected] if map_line is None
                else terrain.height([[p['x'],p['y']] for p in expected]))
            if len(reference_heights)!=len(expected):
                raise ValueError('地形高程采样数量不一致。')
            for actual,predicted,height in zip(points,expected,reference_heights):
                xyz=[_number(actual[k]) for k in ('x','y','z')]
                if math.hypot(xyz[0]-predicted['x'],xyz[1]-predicted['y'])>tolerance or abs(xyz[2]-_number(height))>tolerance:
                    errors.append('电极位置不符合固定米制间距的配准测线。')
                    break
        if map_line is None:
            if len(line)!=2 or any(len(vertex)!=2 for vertex in line):
                errors.append('配准测线须为两个二维端点。')
            elif any(math.hypot(_number(a[0])-b[0],_number(a[1])-b[1])>tolerance for a,b in zip(line,expected_line)):
                errors.append('测线端点与配准参数不一致。')
            xs=[_number(v) for v in terrain.x];ys=[_number(v) for v in terrain.y]
            if not xs or not ys or min(xs)>=max(xs) or min(ys)>=max(ys):
                raise ValueError('地形范围无效。')
            xy=[[_number(p['x']),_number(p['y'])] for p in points]
            if any(not (min(xs)<=x<=max(xs) and min(ys)<=y<=max(ys)) for x,y in xy):
                errors.append('部分电极超出地形范围。')
            heights=[_number(v) for v in terrain.height(xy)]
            if len(heights)!=len(points):
                raise ValueError('地形高程采样数量不一致。')
            if any(abs(_number(p['z'])-z)>tolerance for p,z in zip(points,heights)):
                errors.append('电极与地面高程差超过容差；平地假设不适用。')
        else:
            if len(line)!=2 or any(len(vertex)!=2 for vertex in line):
                errors.append('地图测线须为两个二维端点。')
            else:
                try:
                    user_line=_map_line(line)
                except ValueError:
                    errors.append('地图测线端点无效。')
                else:
                    if any(math.hypot(a[0]-b[0],a[1]-b[1])>tolerance for a,b in zip(user_line,map_line)):
                        errors.append('测线端点与用户地图测线不一致。')
                    try:
                        if not _points_on_map_line(points,user_line,tolerance):
                            errors.append('电极位置不在用户地图测线上。')
                    except (KeyError,TypeError,ValueError):
                        errors.append('电极位置无法与用户地图测线核对。')
            map_length=math.hypot(map_line[1][0]-map_line[0][0],map_line[1][1]-map_line[0][1])
            if map_length+1e-9<required:
                errors.append(f'用户地图测线长度不足：所需长度 {required:g} 米。')
            try:
                _terrain_line_heights(terrain,map_line)
            except ValueError as error:
                errors.append(str(error))
        return errors
    except Exception as error:
        return [f'配准验证失败：{error}']
