"""Linked 2-D section with stable cell IDs, without remeshing/interpolation."""
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.colors import LogNorm
from matplotlib.patches import Polygon


class SectionView(Q.QWidget):
    cell_selected = QtCore.Signal(int)
    point_selected = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(facecolor='#0d121a')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setStyleSheet('background:#151c26; color:#cbd9eb;')
        layout = Q.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.heading = Q.QLabel('二维剖面 · 反演后显示', objectName='badge')
        layout.addWidget(self.heading)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.toolbar)
        self.canvas.mpl_connect('pick_event', self._picked)
        self.cell_collection = None
        self.point_collection = None
        self.acquisition_collection = None
        self.acquisition_mode = False
        self.selected_cell = None
        self.selected_point = None
        self.polygons = None
        self.home_limits = None
        self._paint_axes()

    def _paint_axes(self):
        self.axes.set_facecolor('#0d121a')
        self.axes.tick_params(colors='#a4b6cf', labelsize=8)
        for spine in self.axes.spines.values():
            spine.set_color('#39475a')
        self.axes.set_xlabel('Distance along survey (m)', color='#a4b6cf', fontsize=9)
        self.axes.set_ylabel('Elevation (m)', color='#a4b6cf', fontsize=9)
        self.axes.grid(alpha=.12, color='#92a9c7')
        self.figure.subplots_adjust(left=.17, right=.96, top=.94, bottom=.13)

    def set_data(self, section, stale=False):
        self.clear_highlight()
        self.axes.clear()
        self._paint_axes()
        self.selected_cell = self.selected_point = None
        self.cell_collection = self.point_collection = None
        self.acquisition_collection = None
        self.acquisition_mode = False
        self.polygons = None
        if section is None:
            self.home_limits = None
            self.heading.setText('二维剖面 · 尚无反演结果')
            self.canvas.draw_idle()
            return
        self.section = section
        nodes = section['nodes'].copy()
        nodes[:,1] += section['z_reference']
        self.polygons = nodes[section['cells']]
        self.cell_collection = PolyCollection(self.polygons, cmap='turbo_r', norm=LogNorm(20,500),
            edgecolors='none', alpha=.43 if stale else 1., picker=True)
        self.cell_collection.set_array(section['resistivity'])
        self.axes.add_collection(self.cell_collection)
        self.axes.plot(section['station'], section['elevation'], color='#b4c8a4', lw=1.4)
        self.point_collection = self.axes.scatter(section['station'], section['elevation'],
            s=16, color='#f59e0b', picker=7, zorder=5)
        self.axes.autoscale_view()
        self.axes.set_aspect('equal', adjustable='box')
        self.home_limits = (self.axes.get_xlim(),self.axes.get_ylim())
        self.heading.setText('旧结果已过期 · 保留原位置，仅供对比' if stale else '二维剖面 · 点击单元 / 电极查询')
        self.heading.setStyleSheet('color:#ffbc66;' if stale else '')
        self.canvas.draw_idle()

    def set_acquisition(self, packet):
        """Acquisition-only 2-D preview; never draws or implies a model."""
        self.clear_highlight()
        self.axes.clear()
        self._paint_axes()
        self.selected_cell = self.selected_point = None
        self.cell_collection = self.point_collection = None
        self.polygons = None
        if packet is None:
            self.acquisition_mode = False
            self.acquisition_collection = None
            self.home_limits = None
            self.canvas.draw_idle()
            return
        count = int(packet['electrodes'])
        spacing = float(packet['spacing'])
        station = np.arange(count, dtype=float) * spacing
        length = float(station[-1])
        self.axes.plot([0., length], [0., 0.], color='#f59e0b', lw=1.8, zorder=4)
        self.acquisition_collection = self.axes.scatter(
            station, np.zeros(count), s=18, color='#70caff', zorder=5, picker=False)
        self.acquisition_mode = True
        margin_x = max(2., length * .02)
        margin_y = max(1., length * .06)
        self.axes.set_xlim(-margin_x, length + margin_x)
        self.axes.set_ylim(-margin_y, margin_y)
        self.axes.set_aspect('auto')
        self.axes.set_xlabel('局部距离 (m) · 平地参考，未配准', color='#a4b6cf', fontsize=9)
        self.axes.set_ylabel('相对高程 (m, 平地参考)', color='#a4b6cf', fontsize=9)
        self.home_limits = (self.axes.get_xlim(), self.axes.get_ylim())
        self.heading.setText('仅采集二维预览 · 局部距离/平地参考值 · 未配准 · 非反演图')
        self.heading.setStyleSheet('color:#8bc4ff;')
        self.canvas.draw_idle()

    def highlight_acquisition_point(self, index):
        self.clear_highlight()
        if not self.acquisition_mode or self.acquisition_collection is None:
            return
        offsets = self.acquisition_collection.get_offsets()
        if not 0 <= int(index) < len(offsets):
            return
        point = offsets[int(index)]
        self.selected_point = int(index)
        self.point_patch = self.axes.scatter(
            point[0], point[1], s=110, facecolors='none', edgecolors='#fff0b1',
            linewidths=2, zorder=9)
        self.canvas.draw_idle()

    def _picked(self, event):
        if self.toolbar.mode or not len(event.ind):
            return
        if event.artist is self.point_collection:
            self.point_selected.emit(int(event.ind[0]))
        elif event.artist is self.cell_collection:
            self.cell_selected.emit(int(event.ind[0]))

    def stop_navigation(self):
        if self.toolbar.mode == 'pan/zoom':
            self.toolbar.pan()
        elif self.toolbar.mode:
            self.toolbar.zoom()

    def reset_view(self):
        self.stop_navigation()
        if self.home_limits is not None:
            self.axes.set_xlim(self.home_limits[0])
            self.axes.set_ylim(self.home_limits[1])
        self.canvas.draw_idle()

    def highlight_cell(self, cell_id):
        self.clear_highlight()
        if self.polygons is None:
            return
        self.selected_cell = int(cell_id)
        self.cell_patch = Polygon(self.polygons[cell_id], closed=True, facecolor='none',
            edgecolor='#ffffff', lw=2.4, zorder=8)
        self.axes.add_patch(self.cell_patch)
        self.canvas.draw_idle()

    def highlight_point(self, index):
        self.clear_highlight()
        if self.point_collection is None:
            return
        self.selected_point = int(index)
        point = self.point_collection.get_offsets()[index]
        self.point_patch = self.axes.scatter(*point, s=100, facecolors='none', edgecolors='#fff0b1',
                                           linewidths=2, zorder=9)
        self.canvas.draw_idle()

    def clear_highlight(self):
        for name in ('cell_patch', 'point_patch'):
            artist = getattr(self, name, None)
            if artist is not None:
                artist.remove()
                setattr(self, name, None)
        self.selected_cell = self.selected_point = None
        self.canvas.draw_idle()
