"""Actual 3-D ERT inversion in an isolated process, with explicit assumed geometry."""
from pathlib import Path
import json
import os
import numpy as np
from core import write_json


def run_volume_case(request_path):
    req=json.loads(Path(request_path).read_text(encoding='utf8'));out=Path(req['output']);out.mkdir(parents=True,exist_ok=True)
    runtime=out/'runtime';runtime.mkdir(exist_ok=True)
    os.environ['APPDATA']=str(runtime)
    from worker_runtime import configure_matplotlib_cache
    configure_matplotlib_cache()
    os.environ.setdefault('OMP_NUM_THREADS','2')
    import pygimli as pg
    import pygimli.meshtools as mt
    from pygimli.physics import ert
    pg.rc['globalCache']=False
    xyz=np.asarray(req['sensors'],float);abmn=np.asarray(req['abmn'],int)
    r=np.asarray(req['resistance'],float);errors=np.asarray(req['error'],float)
    if xyz.ndim!=2 or xyz.shape[1]!=3 or len(xyz)>720 or not np.isfinite(xyz).all():raise ValueError('三维电极坐标无效或规模超限。')
    if abmn.shape!=(len(r),4) or abmn.min()<0 or abmn.max()>=len(xyz) or len(r)>10000:raise ValueError('三维观测电极引用无效。')
    if errors.shape!=r.shape or not np.isfinite(r).all() or np.any(r<=0) or not np.isfinite(errors).all() or np.any(errors<=0):raise ValueError('三维观测或误差无效。')
    if np.ptp(xyz[:,2])>.1:raise ValueError('本阶段3D仅验证平地假设。')
    origin=np.r_[xyz[:,:2].mean(axis=0),xyz[:,2].mean()];local=xyz-origin
    data=pg.DataContainerERT()
    for p in local:data.createSensor(pg.Pos(*map(float,p)))
    for i,row in enumerate(abmn):data.createFourPointData(i,*map(int,row))
    data['r']=r;data['k']=ert.createGeometricFactors(data,dim=3)
    data['rhoa']=np.asarray(data['k'])*r;data['err']=errors
    if np.any(np.asarray(data['rhoa'])<=0):raise ValueError('几何因子与观测极性不一致。')
    print(f'3D: {len(xyz)} electrodes, {len(r)} observations. Building triangular-prism mesh.',flush=True)
    depth=float(req.get('depth',12.));cell=float(req.get('cell_volume',120.));iterations=int(req.get('max_iter',4));lam=float(req.get('lam',30.))
    if not 2<=depth<=50 or not 20<=cell<=1000 or not 1<=iterations<=12 or not 1<=lam<=1000:raise ValueError('三维网格或反演参数超出验证范围。')
    # Extruded triangles are genuine 3-D finite elements; no external tetgen is
    # required. Both the unknowns and the forward operator remain 3-dimensional.
    low=local[:,:2].min(axis=0);high=local[:,:2].max(axis=0)
    inner=mt.createRectangle(start=low-2.,end=high+2.,marker=2,area=cell/4.)
    outer=mt.createRectangle(start=low-50.,end=high+50.,marker=1,area=cell*3.,boundaryMarker=-2)
    plc=inner+outer
    for xy in local[:,:2]:plc.createNode(float(xy[0]),float(xy[1]),0.)
    mesh2d=mt.createMesh(plc,quality=30.)
    z=np.unique(np.r_[-depth*5,-depth*2,np.linspace(-depth,0,6)])
    mesh=mt.createMesh3D(mesh2d,z,pg.core.MARKER_BOUND_HOMOGEN_NEUMANN,pg.core.MARKER_BOUND_MIXED)
    mesh.createNeighbourInfos()
    for boundary in mesh.boundaries():
        if boundary.leftCell() is None or boundary.rightCell() is None:
            boundary.setMarker(-1 if abs(boundary.center().z())<1e-8 else -2)
    for c in mesh.cells():
        if c.center().z() < -depth:c.setMarker(1)
    if mesh.cellCount()>60000 or mesh.nodeCount()>18000:raise ValueError('3D网格规模超限，请增大单元体积。')
    print(f'3D mesh: {mesh.nodeCount()} nodes, {mesh.cellCount()} cells. Starting inversion.',flush=True)
    manager=ert.ERTManager(data,verbose=True)
    from inversion_diagnostics import InversionDiagnostics
    recorder=InversionDiagnostics()
    def callback(i,inv):
        recorder.callback(i,inv)
        print(f'3D iteration {i}: chi2={inv.chi2():.5g}, RRMS={inv.relrms():.4g}%',flush=True)
    manager.inv.setPostStep(callback)
    recovered=manager.invert(mesh=mesh,lam=lam,maxIter=iterations,verbose=True)
    result=manager.paraDomain
    cells=np.array([[n.id() for n in c.nodes()] for c in result.cells()],int)
    nodes=np.array([[n.x(),n.y(),n.z()] for n in result.nodes()])+origin
    if cells.ndim!=2 or cells.shape[1]!=6 or len(cells)!=len(recovered):raise ValueError('3D结果不是预期的三棱柱单元。')
    model=np.asarray(recovered,float);response=np.asarray(manager.inv.response,float)
    if not np.isfinite(model).all() or np.any(model<=0) or not np.isfinite(response).all():raise ValueError('3D求解返回非有限结果。')
    diagnostics,coverage=recorder.finish(manager,iterations)
    np.savez_compressed(out/'volume.npz',nodes=nodes,cells=cells,resistivity=model,sensors=xyz,abmn=abmn,
        observed=np.asarray(data['rhoa']),response=response,resistance=r,error=errors,coverage_log10=coverage)
    write_json(out/'diagnostics.json',diagnostics)
    write_json(out/'metadata.json',dict(schema=1,dimension='3D ERT triangular-prism inversion',engine='pyGIMLi',engine_version=pg.__version__,
        input_signature=req['signature'],geometry_hypothesis=bool(req['geometry_hypothesis']),source_relation=req['source_relation'],
        warning='假设测网几何下的实际三维求解；原观测采自同一条测线，不是现场三维地下结构结论。' if req['geometry_hypothesis'] else '三维反演结果需结合误差与空间约束解释。',
        flat_assumed=True,origin=origin.tolist(),crs=req['crs'],terrain_source=req['terrain_source'],
        sensors=len(xyz),observations=len(r),cells=len(model),mesh_cells=mesh.cellCount(),chi2=float(manager.inv.chi2()),
        rrms_percent=float(manager.inv.relrms()),lam=lam,max_iter=iterations,depth=depth,cell_volume=cell,
        source_mapping=req['source_mapping']))
    print('3D inversion complete; volume.npz and metadata.json written.',flush=True)


if __name__=='__main__':
    import sys
    run_volume_case(sys.argv[1])
