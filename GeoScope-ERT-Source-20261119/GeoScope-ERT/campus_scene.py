"""A deliberately fictional campus for testing survey layout editing."""
import json
from pathlib import Path
import numpy as np
import pyvista as pv


def create_campus(folder):
    import rasterio
    from rasterio.transform import from_origin
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'fictional-campus.tif'
    # A projected container only: all coordinates and elevations are invented.
    with rasterio.open(path,'w',driver='GTiff',width=801,height=601,count=1,
            dtype='float32',crs='EPSG:3857',transform=from_origin(-.5,600.5,1,1)) as dst:
        dst.write(np.zeros((601,801),dtype=np.float32),1)
    buildings=[]
    for name,x,y,w,d,h in [
        ('教学楼 A',80,410,130,55,16),('教学楼 B',80,310,130,55,16),
        ('实验楼',270,410,120,65,18),('图书馆',290,280,110,90,22),
        ('行政楼',90,180,110,65,13),('食堂',290,130,110,65,10),
        ('宿舍 1',530,450,80,40,18),('宿舍 2',650,450,80,40,18),
        ('宿舍 3',530,370,80,40,18),('宿舍 4',650,370,80,40,18),
        ('体育馆',620,70,100,65,12)]:
        buildings.append(dict(name=name,bounds=[x,x+w,y,y+d,0,h]))
    data=dict(schema=1,fictional=True,label='虚拟校园示意',
        note='布局、坐标、楼高及地面高程均为虚拟示意；非测绘成果，不代表任何真实校园。',
        buildings=buildings, roads=[[[40,250],[760,250]],[[450,40],[450,550]],
            [[40,530],[760,530]],[[240,80],[240,530]],[[500,330],[760,330]],
            [[40,80],[40,530]],[[760,80],[760,530]],[[40,80],[760,80]]],
        track=dict(center=[585,205],radii=[100,65]))
    Path(str(path)+'.scene.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


def read_scene(path):
    sidecar=Path(str(path)+'.scene.json')
    if not sidecar.exists():
        return None
    data=json.loads(sidecar.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or data.get('fictional') is not True:
        raise ValueError('不支持的校园场景。')
    return data


def draw_scene(window, *, origin=None, opacity=None):
    data=window.campus_scene
    if origin is None:origin=window.terrain.origin
    if opacity is None:opacity=window.opacity.value()/100
    window.campus_actors=[]
    if isinstance(data.get('render_meshes'),list):
        for item in data['render_meshes']:
            actor=window.plotter.add_mesh(pv.PolyData(np.asarray(item['vertices'])-origin,
                faces=np.asarray(item['faces'],dtype=np.int64)),color=item['color'],
                opacity=opacity,pickable=False,reset_camera=False)
            window.campus_actors.append(actor)
        positions=[];labels=[]
        for building in data['buildings']:
            bounds=np.array(building['bounds'],dtype=float)-np.repeat(origin,2)
            positions.append([(bounds[0]+bounds[1])/2,(bounds[2]+bounds[3])/2,bounds[5]+3])
            labels.append(building['code'])
        window.plotter.add_point_labels(np.asarray(positions),labels,font_size=11,
            text_color='#f0f5fb',point_size=0,shape_color='#17212c',always_visible=True,
            name='campus-labels',pickable=False,reset_camera=False)
        return
    def polygon(xy,z,color):
        pts=np.column_stack([xy,np.full(len(xy),z)])-origin
        mesh=pv.PolyData(pts,faces=np.r_[len(pts),np.arange(len(pts))])
        window.plotter.add_mesh(mesh,color=color,opacity=window.opacity.value()/100,pickable=False,reset_camera=False)
    for line in data['roads']:
        a,b=np.asarray(line,dtype=float)
        v=b-a; normal=np.array([-v[1],v[0]])/np.linalg.norm(v)*8
        polygon([a+normal,b+normal,b-normal,a-normal],.08,'#6b7785')
    track=data['track']; c=np.array(track['center']); radii=np.array(track['radii'])
    angles=np.linspace(0,2*np.pi,120,endpoint=False)
    circle=np.column_stack([np.cos(angles),np.sin(angles)])
    polygon(c+circle*radii,.12,'#bd6b55')
    polygon(c+circle*(radii-[14,14]),.15,'#448c65')
    for scale in (.96,.90):
        xy=c+circle*(radii*scale)
        points=np.column_stack([xy,np.full(len(xy),.18)])-origin
        window.plotter.add_mesh(pv.lines_from_points(np.vstack([points,points[0]])),
            color='#eed8bc',line_width=1,pickable=False,reset_camera=False)
    labels=[]; positions=[]
    codes=['A1','A2','LAB','LIB','ADM','CAF','D1','D2','D3','D4','GYM']
    for index,building in enumerate(data['buildings']):
        bounds=np.array(building['bounds'],dtype=float)
        bounds-=np.repeat(origin,2)
        window.plotter.add_mesh(pv.Box(bounds=bounds),color='#94adc7',opacity=opacity,show_edges=True,
            edge_color='#536c86',pickable=False,reset_camera=False)
        positions.append([(bounds[0]+bounds[1])/2,(bounds[2]+bounds[3])/2,bounds[5]+3])
        labels.append(codes[index])
    positions.append([c[0]-origin[0],c[1]-origin[1],1]);labels.append('FIELD')
    window.plotter.add_point_labels(np.asarray(positions),labels,font_size=11,
        text_color='#f0f5fb',point_size=0,shape_color='#17212c',always_visible=True,
        name='campus-labels',pickable=False,reset_camera=False)
