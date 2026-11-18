"""Spatial/data boundary for the disposable desktop integration spike."""
from dataclasses import dataclass
from pathlib import Path
import csv
import json
import numpy as np
import pyvista as pv
from scipy.interpolate import RegularGridInterpolator


@dataclass
class Terrain:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray  # increasing y, x; elevations, metres
    crs: str
    source: str

    @property
    def origin(self):
        return np.array([self.x.mean(), self.y.mean(), 0.0])

    def height(self, xy):
        xy = np.atleast_2d(xy)
        return RegularGridInterpolator((self.y, self.x), self.z, bounds_error=False,
                                      fill_value=np.nan)(xy[:, [1, 0]])

    def mesh(self):
        xx, yy = np.meshgrid(self.x, self.y)
        grid = pv.StructuredGrid(xx - self.origin[0], yy - self.origin[1], self.z)
        grid['elevation'] = self.z.ravel(order='F')
        return grid


def read_terrain(path, max_side=240):
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError('地形缺少坐标系。请先给文件指定投影坐标系。')
        if not src.crs.is_projected or src.crs.linear_units not in ('metre', 'meter'):
            raise ValueError('验证版要求以米为单位的投影坐标系。请先将地形重投影为当地 UTM / 高斯投影。')
        if abs(src.transform.b) > 1e-9 or abs(src.transform.d) > 1e-9:
            raise ValueError('暂不支持旋转栅格，请先转换为北向上的 DEM。')
        scale = max(src.width / max_side, src.height / max_side, 1)
        h, w = max(2, int(src.height / scale)), max(2, int(src.width / scale))
        zz = src.read(1, out_shape=(h, w), masked=True,
                      resampling=Resampling.bilinear).astype(float).filled(np.nan)
        tr = src.transform * src.transform.scale(src.width / w, src.height / h)
        x = tr.c + (np.arange(w) + .5) * tr.a
        y = tr.f + (np.arange(h) + .5) * tr.e
        if x[0] > x[-1]:
            x, zz = x[::-1], zz[:, ::-1]
        if y[0] > y[-1]:
            y, zz = y[::-1], zz[::-1]
        if np.isfinite(zz).sum() < 4:
            raise ValueError('地形有效高程不足。')
        return Terrain(x, y, zz, src.crs.to_string(), str(Path(path).resolve()))


def stations_along(vertices, count):
    vertices = np.asarray(vertices, dtype=float)
    lengths = np.linalg.norm(np.diff(vertices[:, :2], axis=0), axis=1)
    if len(vertices) < 2 or np.any(lengths < 1e-6):
        raise ValueError('测线至少需要两个不同的位置。')
    knots = np.r_[0, np.cumsum(lengths)]
    s = np.linspace(0, knots[-1], count)
    xy = np.column_stack([np.interp(s, knots, vertices[:, k]) for k in (0, 1)])
    return s, xy


def read_stations(path, terrain):
    points = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not {'x', 'y'} <= set(reader.fieldnames or []):
            raise ValueError('CSV 需包含 x,y 列，可选 id,z。坐标须与当前地形一致。')
        ids = set()
        for n, row in enumerate(reader, 1):
            name = row.get('id') or f'P{n:03}'
            if name in ids:
                raise ValueError(f'测点编号重复：{name}')
            ids.add(name)
            x, y = float(row['x']), float(row['y'])
            ground = float(terrain.height([[x, y]])[0])
            if not np.isfinite(ground):
                raise ValueError(f'{name} 不在有效地形范围内。')
            measured = bool(row.get('z', '').strip())
            z = float(row['z']) if measured else ground
            if not np.all(np.isfinite([x, y, z])):
                raise ValueError(f'{name} 含无效坐标。')
            points.append(dict(id=name, x=x, y=y, z=z,
                               elevation_source='实测/文件' if measured else '地形插值'))
    return points


def place_section(nodes, cells, values, xy, station, origin, z_reference):
    """Rigid placement: station + absolute elevation, never terrain-drape results."""
    nodes = np.asarray(nodes)
    station, xy = np.asarray(station), np.asarray(xy)
    # Solver parameter domains extend beyond the first/last electrode. A straight
    # survey has a well-defined rigid coordinate transform there, not clamping.
    direction = (xy[-1] - xy[0]) / (station[-1] - station[0])
    predicted = xy[0] + (station-station[0])[:, None] * direction
    if not np.allclose(predicted, xy, atol=.001):
        raise ValueError('此二维案例仅支持直测线的刚性坐标定位。')
    positioned = xy[0] + (nodes[:, 0]-station[0])[:, None] * direction
    xx = positioned[:, 0] - origin[0]
    yy = positioned[:, 1] - origin[1]
    xyz = np.column_stack([xx, yy, nodes[:, 1] + z_reference])
    faces = np.asarray([[3, *c] for c in cells], dtype=np.int64).ravel()
    mesh = pv.PolyData(xyz, faces)
    mesh.cell_data['resistivity'] = np.asarray(values)
    return mesh


def load_result_arrays(path):
    """Read every array from an NPZ result off the GUI thread.

    The file read and array copies run inside ``run_project_step``; the
    callback must never create Qt or VTK objects.
    """
    from project_tasks import run_project_step

    def read():
        with np.load(path, allow_pickle=False) as data:
            return {key: data[key].copy() for key in data.files}

    return run_project_step(read)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
