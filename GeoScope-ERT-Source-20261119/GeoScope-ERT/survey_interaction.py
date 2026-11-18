"""Pure helpers for the same-scene multi-line survey display.

The functions here intentionally avoid Qt and PyVista so the rendering and
picking rules can be checked with a mock plotter in offscreen test runs.
"""
import hashlib
import numpy as np


# Fixed palette. A line keeps the same colour while the project is open and
# after save/reopen; duplicate hashes are resolved in list order.
LINE_PALETTE=(
    '#51bce1','#ffe38a','#7ee0a3','#f59ac2','#c7a6ff',
    '#ff9f6e','#8fd3ff','#d7e56e','#77e6d4','#ffb3a7')


def ensure_survey_visibility(state):
    """Give pre-visible-field projects the documented default of visible."""
    if not isinstance(state,dict):return state
    for line in state.get('lines') or []:
        if isinstance(line,dict):line.setdefault('visible',True)
    return state


def survey_line_colors(state):
    """Return {line_id: stable colour}, resolving palette collisions by order."""
    colors={};used=set()
    for line in (state or {}).get('lines') or []:
        lid=str(line.get('id',''))
        digest=hashlib.sha1(lid.encode('utf-8')).digest()
        start=int.from_bytes(digest[:4],'big')%len(LINE_PALETTE)
        color=LINE_PALETTE[start]
        for offset in range(len(LINE_PALETTE)):
            color=LINE_PALETTE[(start+offset)%len(LINE_PALETTE)]
            if color not in used:break
        used.add(color);colors[lid]=color
    return colors


def survey_line_xy(workspace_or_line):
    """Return an (n,2) float array for any two-or-more-vertex line."""
    value=workspace_or_line.get('line') if isinstance(workspace_or_line,dict) else workspace_or_line
    try:array=np.asarray(value,float)
    except (TypeError,ValueError):return np.empty((0,2))
    if array.size==0 or array.ndim!=2 or array.shape[1]<2:return np.empty((0,2))
    return array[:,:2]


def electrode_xyz(points):
    """Return station XYZ in file coordinates; caller filters non-finite rows."""
    try:return np.asarray([[p['x'],p['y'],p['z']] for p in points or []],float)
    except (KeyError,TypeError,ValueError):return np.empty((0,3))


def sparse_label_indices(count,limit=8):
    """Evenly spaced endpoints/interior indices, avoiding crowded labels."""
    if count<=0:return []
    limit=max(1,min(int(limit),count))
    if limit==1:return [0]
    return sorted(set(np.linspace(0,count-1,limit).round().astype(int).tolist()))


def electrode_label(line,point,index):
    """Labels such as ``L01:E01`` while tolerating legacy point names."""
    point_id=str((point or {}).get('id') or f'E{index+1:02}')
    return f"{line.get('id','')}:{point_id}"
