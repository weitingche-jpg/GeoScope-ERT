"""Coordinate and provenance rules shared by both interactive views."""
import numpy as np


def result_is_stale(result_line, current_line):
    if result_line is None:
        return False
    original, current = np.asarray(result_line), np.asarray(current_line)
    return original.shape != current.shape or not np.allclose(original, current, rtol=0, atol=.001)


def cell_properties(mesh, terrain, cell_id):
    if not 0 <= cell_id < mesh.n_cells:
        raise IndexError('单元编号超出范围')
    center = np.asarray(mesh.get_cell(cell_id).points).mean(axis=0)
    world = center + terrain.origin
    ground = float(terrain.height([world[:2]])[0])
    return dict(cell_id=int(cell_id), resistivity=float(mesh.cell_data['resistivity'][cell_id]),
                world=world, ground=ground, depth=ground-world[2])
