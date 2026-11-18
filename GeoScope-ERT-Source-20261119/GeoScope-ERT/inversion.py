"""Real pyGIMLi forward/inverse calculation, in a separate process."""
from pathlib import Path
import json
import numpy as np
from core import write_json


def run_case(request_path):
    import os
    runtime_dir = Path(request_path).resolve().parent / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ['APPDATA'] = str(runtime_dir)
    from worker_runtime import configure_matplotlib_cache
    configure_matplotlib_cache()
    os.environ['PYGIMLI_CACHE'] = str(runtime_dir / 'cache')
    import pygimli as pg
    pg.rc['globalCache'] = False
    import pygimli.meshtools as mt
    from pygimli.physics import ert
    req = json.loads(Path(request_path).read_text(encoding='utf-8'))
    out = Path(req['output'])
    out.mkdir(parents=True, exist_ok=True)
    station = np.asarray(req['station'])
    elevation = np.asarray(req['elevation'])
    zref = float(elevation.mean())
    relative_z = elevation - zref
    sensors = np.column_stack([station, relative_z])
    length = station[-1]
    imported=req.get('mode')=='observed'
    packet=None
    array_name='dipole-dipole' if not imported else 'wenner'
    if imported:
        from observed_ert import validate_packet
        packet=validate_packet(req['observations'])
        array_name=packet.get('array','wenner')
        if len(station)!=packet['electrodes'] or not np.allclose(station,np.arange(len(station))*packet['spacing']):
            raise ValueError('观测电极数量或间距与请求不一致。')
        if not np.allclose(elevation,0):raise ValueError('本阶段观测输入仅接受已确认的平地假设。')
        error=float(packet['relative_error']);lam=float(packet['lam']);iterations=int(packet['max_iter'])
        if not .001<=error<=.3 or not .1<=lam<=1000 or not 1<=iterations<=30:raise ValueError('反演参数超出允许范围。')
        data=pg.DataContainerERT()
        for s,z in sensors:data.createSensor(pg.Pos(float(s),float(z)))
        from ert_quality import active_indices
        source_indices=active_indices(packet)
        enabled_rows=[packet['rows'][i] for i in source_indices]
        for i,row in enumerate(enabled_rows):data.createFourPointData(i,*row['abmn'])
        data['rhoa']=np.array([r['rhoa'] for r in enabled_rows]);data['err']=np.full(len(enabled_rows),error)
        measured_k=np.array([r.get('k',np.nan) for r in enabled_rows],dtype=float)
        resistance_values=np.array([r.get('resistance',np.nan) for r in enabled_rows],dtype=float)
        if array_name=='dipole-dipole':
            if np.all(np.isfinite(measured_k)) and np.all(measured_k!=0):
                # Keep the exact signed K recorded with the DD packet.
                data['k']=measured_k
            else:
                from ert_arrays import signed_geometric_factor
                measured_k=np.array([signed_geometric_factor(row['abmn'],packet['spacing'])
                                     for row in enabled_rows],dtype=float)
                data['k']=measured_k
        else:
            # Wenner keeps the legacy pyGIMLi analytical path.
            data['k']=ert.createGeometricFactors(data)
            measured_k=np.asarray(data['k'],dtype=float)
    else:
        scheme = ert.createData(elecs=sensors, schemeName='dd')
        mesh = mt.createParaMesh(scheme.sensors(), paraDepth=length * .38,
                                paraMaxCellSize=(length / 25) ** 2,
                                paraDX=.5, boundary=2)
        cc = np.array([[c.center().x(), c.center().y()] for c in mesh.cells()])
        depth = np.interp(cc[:, 0], station, relative_z) - cc[:, 1]
        truth = np.full(mesh.cellCount(), 150.)
        truth[depth > length * .18] = 350.
        anomaly = ((cc[:, 0] - length * .52) / (length * .13)) ** 2 + ((depth - length * .12) / (length * .07)) ** 2 < 1
        truth[anomaly] = 30.
        data = ert.simulate(mesh, scheme=scheme, res=truth, noiseLevel=2,
                            noiseAbs=1e-6, seed=42, verbose=False)
        data.remove(data['rhoa'] <= 0)
        source_indices=list(range(data.size()))
        lam,iterations=20,6
    # The compiled solver's file writer does not accept Unicode absolute paths.
    # A Unicode-safe Python chdir plus ASCII relative filename avoids data loss.
    previous_dir = Path.cwd()
    os.chdir(out)
    try:
        data.save('imported_observations.dat' if imported else 'synthetic_observations.dat')
    finally:
        os.chdir(previous_dir)
    manager = ert.ERTManager(data, verbose=False)
    from inversion_diagnostics import InversionDiagnostics
    recorder=InversionDiagnostics();manager.inv.setPostStep(recorder.callback)
    recovered = manager.invert(lam=lam, paraDepth=length * .30,
                               paraMaxCellSize=(length / 25) ** 2,
                               maxIter=iterations, verbose=False)
    result_mesh = manager.paraDomain
    diagnostics,coverage=recorder.finish(manager,iterations)
    write_json(out/'diagnostics.json',diagnostics)
    nodes = np.array([[n.x(), n.y()] for n in result_mesh.nodes()])
    cells = np.array([[n.id() for n in c.nodes()] for c in result_mesh.cells()])
    np.savez_compressed(out / 'model.npz', nodes=nodes, cells=cells,
                        resistivity=np.asarray(recovered), station=station,
                        xy=np.asarray(req['xy']), elevation=elevation,
                        z_reference=zref, response=np.asarray(manager.inv.response),
                        observed=np.asarray(data['rhoa']),error=np.asarray(data['err']),
                        abmn=np.column_stack([np.asarray(data[k]) for k in ('a','b','m','n')]).astype(int),
                        resistance=(resistance_values if imported else np.full(data.size(),np.nan)),
                        geometric_factor=(measured_k if imported else np.full(data.size(),np.nan)),
                        source_row_indices=np.asarray(source_indices,dtype=int),coverage_log10=coverage)
    if imported:
        from workflow_geometry import observation_signature,electrode_ids
        write_json(out/'observations.json',packet)
    write_json(out / 'metadata.json', dict(
        kind='导入实验观测反演；平地假设、测线未地理定位' if imported else '合成观测 + 实际正反演计算；非本地区实测结果',
        data_kind='imported_observations' if imported else 'synthetic',
        geometry_hypothesis=bool(packet.get('geometry_hypothesis',False)) if imported else False,
        source_relation='同线重复观测，按假设平行测网定位；非真实多线采集' if imported and packet.get('geometry_hypothesis') else None,
        workflow_version=1 if imported and 'binding' in packet else None,
        observation_signature=observation_signature(packet) if imported else None,
        binding=packet.get('binding') if imported else None,
        electrode_ids=electrode_ids(packet) if imported else None,
        source_file=packet.get('source_file') if imported else None,
        source_sha256=packet.get('source_sha256') if imported else None,
        source_files=packet.get('source_files') if imported else None,
        source_format=packet.get('source_format') if imported else None,
        source_native_order=packet.get('source_native_order') if imported else None,
        raw_resistance_saved=bool(imported and any('resistance' in r for r in packet['rows'])),
        array=array_name,
        geometry_signature=req.get('geometry_signature'),
        assumed_relative_error=packet['relative_error'] if imported else None,
        flat_assumed=imported,coordinate_frame='LOCAL_ONLY_NOT_GEOGRAPHIC' if imported else 'projected',
        lam=lam,max_iter=iterations,
        engine='pyGIMLi', engine_version=pg.__version__,
        dimension='二维电阻率反演（2.5D 正演），三维场景中显示剖面',
        crs=req['crs'], terrain_source=req['terrain_source'],
        line_vertices=req['line_vertices'], chi2=float(manager.inv.chi2()),
        rrms_percent=float(manager.inv.relrms()), cells=len(cells),
        observations=data.size(), noise_percent=None if imported else 2, seed=None if imported else 42,
        total_observations=len(packet['rows']) if imported else data.size(),
        disabled_observations=len(packet['rows'])-len(source_indices) if imported else 0,
        station_span=[float(station[0]), float(station[-1])],
        terrain_aware=not imported, vertical_reference='平地相对高程0（假设、未实测）' if imported else '沿测线实际高程；整体平移参考值',
        geometry_note='保留求解器节点高程，不对反演结果作贴地变形'))


if __name__ == '__main__':
    import sys
    run_case(sys.argv[1])
