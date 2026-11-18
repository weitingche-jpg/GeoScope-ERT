"""GeoScope: disposable Windows terrain/ERT integration prototype."""
import os
os.environ.setdefault('QT_API', 'pyside6')
os.environ.setdefault('OMP_NUM_THREADS', '2')
import sys
from pathlib import Path
import json
import copy
import traceback
import numpy as np

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
# Must precede any GUI/worker import so matplotlib's config dir is set first.
from worker_runtime import configure_matplotlib_cache
configure_matplotlib_cache(ROOT)
DATA = ROOT / 'data'
if getattr(sys, 'frozen', False):
    # Windowed executables do not have standard streams. Provide local logs for
    # both Python diagnostics and the numerical engine's progress messages.
    runtime_log = open(ROOT / 'application.log', 'a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = runtime_log
    if sys.stderr is None:
        sys.stderr = runtime_log
    print('GeoScope startup:', sys.argv, flush=True)

if '--worker' in sys.argv:
    request_path=sys.argv[sys.argv.index('--worker') + 1]
    request=json.loads(Path(request_path).read_text(encoding='utf-8'))
    if request.get('mode')=='survey3d':
        from inversion3d import run_volume_case
        run_volume_case(request_path)
    else:
        from inversion import run_case
        run_case(request_path)
    sys.exit(0)

# 电法便携版不包含重磁 worker 入口。

from PySide6 import QtCore, QtGui, QtWidgets as Q
import pyvista as pv
from pyvistaqt import QtInteractor
from core import read_terrain, read_stations, stations_along, place_section, load_result_arrays, write_json
from interaction_state import result_is_stale, cell_properties
from section_view import SectionView
from scene_interactions import SceneInteractions
from coordinate_hints import CoordinateHintsMixin
from drag_line import DragLineMixin
from endpoint_edit import (EndpointEditMixin, effective_endpoint_override,
                           endpoint_geometry_signature, line_render_xyz,
                           prune_endpoint_overrides,endpoint_result_is_stale)
from shortcuts import ShortcutController, edit_snapshot, restore_edit
from edit_history import GeometryHistory
from campus_scene import create_campus, read_scene, draw_scene
from point_management import PointManagement
from observation_workflow import ObservationWorkflow
from observed_ert import validate_packet
from step_workflow import StepWorkflow
from calibration_workflow import CalibrationWorkflow
from scalar_legend import ScalarLegendController
from ert_workflow import ERTQualityWorkflow
from region_workflow import RegionWorkflow
from region_project import empty_region,validate_region_state
from survey_project import empty_survey,validate_survey
from survey_workflow import SurveyWorkflow
from quick_parallel import QuickParallelMixin
from workflow_geometry import is_map_line_binding,observation_signature,validate_binding
from history_actions import HistoryActions
from line_import import is_local_import
from vtkmodules.vtkRenderingCore import vtkCellPicker
from camera_navigation import install_navigation


STYLE = '''
QMainWindow, QWidget {background:#0b0d11; color:#dce4ef; font-family:'Microsoft YaHei UI'; font-size:12px;}
QLabel {background:transparent;}
QFrame#sidebar {background:#111419; border-right:1px solid #282e38;}
QLabel#brand {font-size:25px; font-weight:700; color:#f5f8ff;}
QLabel#eyebrow {font-size:10px; color:#7e92ac; letter-spacing:2px;}
QLabel#heading {font-size:14px; font-weight:700; margin-top:10px; color:#ecf2fb;}
QLabel#hint {color:#9aaabe; font-size:11px;}
QLabel#badge {background:#182536; color:#8dc6ff; border:1px solid #263e58; border-radius:5px; padding:6px;}
QPushButton {background:#1a1f27; color:#dce4ef; border:1px solid #303947; border-radius:6px; padding:9px; text-align:left;}
QPushButton:hover {background:#242e3b; border-color:#658ab3;}
QPushButton:pressed {background:#121a25;}
QPushButton:checked {background:#172e46; color:#a4d5ff; border-color:#58a6f1;}
QPushButton:focus {border-color:#6bb5ff;}
QPushButton#primary {background:#428af0; color:#ffffff; border:0; font-weight:600;}
QPushButton#primary:hover {background:#5a9cf6;}
QPushButton:disabled {background:#171b21; color:#687585; border-color:#252d37;}
QPushButton#primary:disabled {background:#171b21; color:#687585; border:1px solid #252d37;}
QSlider::groove:horizontal {height:4px;background:#303a49;border-radius:2px;}
QSlider::sub-page:horizontal {background:#579bef;border-radius:2px;}
QSlider::handle:horizontal {width:13px;margin:-5px 0;background:#8bc4ff;border-radius:6px;}
QPlainTextEdit {background:#11161e; border:1px solid #252f3c; border-radius:5px; color:#9dafc5; font-size:11px; selection-background-color:#2a4e75;}
QTableWidget {background:#10151c; alternate-background-color:#151c25; color:#dce4ef; gridline-color:#252e3b; border:1px solid #283242; selection-background-color:#234364; selection-color:#ffffff;}
QTableWidget::item:selected {background:#234364; color:#ffffff;}
QHeaderView {background:#181f29;}
QHeaderView::section {background:#181f29; padding:5px; border:0; border-bottom:1px solid #303d4f; color:#aebed2;}
QTableCornerButton::section {background:#181f29;border:0;}
QStatusBar {background:#10141b; color:#9aaabe; border-top:1px solid #252e3b;}
QMenuBar,QMenu {background:#11161d; color:#dce4ef;}
QMenuBar::item:selected,QMenu::item:selected {background:#263c57; color:#ffffff;}
QScrollArea {background:#111419; border:0;}
QScrollBar:vertical {background:#10151b; width:9px; margin:0;}
QScrollBar::handle:vertical {background:#3a4758; min-height:30px; border-radius:4px;}
QScrollBar:horizontal {background:#10151b; height:9px; margin:0;}
QScrollBar::handle:horizontal {background:#3a4758; min-width:30px; border-radius:4px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;}
QScrollBar::add-page,QScrollBar::sub-page {background:none;}
QToolTip {background:#202c3c; color:#eff6ff; border:1px solid #4a6382;}
'''


class Window(QuickParallelMixin, EndpointEditMixin, DragLineMixin, SurveyWorkflow, RegionWorkflow, ERTQualityWorkflow, CalibrationWorkflow, StepWorkflow, ObservationWorkflow, PointManagement, HistoryActions, SceneInteractions, CoordinateHintsMixin, Q.QMainWindow):
    def __init__(self, manager=None, demo=True):
        super().__init__()
        self.setWindowTitle('GeoScope 地下勘测工作台 · 桌面组合验证版')
        self.resize(1420, 930)
        self.terrain = None
        self.campus_scene = None
        self.ert_packet = None
        self.case_kind = 'synthetic'
        self.result_geometry_signature = None
        self.fit_warning = ''
        self.workflow_step=0
        self.simulation_enabled=False
        self.case_arrays=None
        self.region_state=empty_region()
        self.region_cache=None
        self.survey_state=empty_survey()
        self.survey_queue=[]
        self.volume_arrays=None
        self.volume_metadata=None
        self._activating_survey=False
        self._survey_batch_running=False
        self._region_dialog=None
        self.model_local_only=False
        self.result_observation_signature=None
        self.result_binding=None
        self.result_profile_packet=None
        self._placement_key=None
        self.points = []
        self.line = []
        self.line_endpoint_overrides = {}
        self.case_path = None
        self.model = None
        self.section_data = None
        self.result_line = None
        self.result_stale = False
        self.points_follow_line = False
        self.selected = None
        self.drag_endpoint = None
        self.press_position = None
        self.drag_snapshot = None
        self.line_snapshot = None
        self.project_path = None
        self.mode = 'browse'
        self.process = None
        self.current_request = None
        self.dirty = False
        self.history = GeometryHistory()
        self.saved_geometry_key = None
        self.init_drag_line()
        self.init_coordinate_hints()
        self._setup()
        self.setup_survey_ui()
        self.shortcut_controller = ShortcutController(self)
        self.setup_history_actions()
        from window_manager import WindowManager
        self.manager = manager or WindowManager(type(self))
        self.manager.register(self)
        self.mark_saved()
        if demo and (DATA / 'terrain.tif').exists():
            QtCore.QTimer.singleShot(150, self.demo)

    def _setup(self):
        root = Q.QWidget()
        row = Q.QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        side = Q.QFrame(objectName='sidebar')
        side.setFixedWidth(310)
        side.setSizePolicy(Q.QSizePolicy.Preferred,Q.QSizePolicy.Minimum)
        layout = Q.QVBoxLayout(side)
        layout.setSizeConstraint(Q.QLayout.SetMinimumSize)
        layout.setContentsMargins(20, 23, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(Q.QLabel('GEOPHYSICS / DESKTOP LAB', objectName='eyebrow'))
        layout.addWidget(Q.QLabel('GeoScope', objectName='brand'))
        layout.addWidget(Q.QLabel('地形 · 观测 · 地下模型', objectName='hint'))
        layout.addWidget(Q.QLabel('组合验证原型  /  本地运行', objectName='badge'))
        self.button(layout,'区域插值工具（剖面插值，非三维反演）',self.open_region_dialog)
        self.region_status=Q.QLabel('区域建模：收录至少两条不同位置的平行剖面。',objectName='hint')
        self.region_status.setWordWrap(True);layout.addWidget(self.region_status)
        self.setup_sidebar_workflow(layout)
        self.heading(layout, '显示控制')
        layout.addWidget(Q.QLabel('地形透明度', objectName='hint'))
        self.opacity = Q.QSlider(QtCore.Qt.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.setValue(58)
        self.opacity.valueChanged.connect(self.set_opacity)
        layout.addWidget(self.opacity)
        views = Q.QHBoxLayout()
        for title, callback in [('三维', self.view_iso), ('俯视', self.view_top), ('剖面', self.view_section)]:
            b = Q.QPushButton(title)
            b.setFixedHeight(40)
            b.setToolTip({'三维':'三维斜视（1）','俯视':'俯视（2）','剖面':'正对剖面（3）'}[title])
            b.clicked.connect(callback)
            views.addWidget(b)
        layout.addLayout(views)
        layout.addStretch()
        self.button(layout, '保存项目 / 导出截图', self.save_menu)
        scroll = Q.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(326)
        self.sidebar_scroll = scroll
        scroll.setWidget(side)
        scroll.setFrameShape(Q.QFrame.NoFrame)
        row.addWidget(scroll)
        center = Q.QVBoxLayout()
        center.setContentsMargins(16, 12, 16, 12)
        self.setup_step_bar(center)
        title = Q.QHBoxLayout()
        self.title = Q.QLabel('先导入地形，开始勘测项目', objectName='heading')
        self.title.setWordWrap(True)
        title.addWidget(self.title)
        title.addStretch()
        self.scene_badge = Q.QLabel('尚未加载地形', objectName='badge')
        title.addWidget(self.scene_badge)
        center.addLayout(title)
        self.plotter = QtInteractor(root)
        install_navigation(self.plotter)
        self.scalar_legend=ScalarLegendController(self.plotter,self)
        self.plotter.set_background('#07090d', top='#151d28')
        self.plotter.interactor.installEventFilter(self)
        self.views = Q.QSplitter(QtCore.Qt.Horizontal)
        self.views.addWidget(self.plotter.interactor)
        self.section_view = SectionView(root)
        self.section_view.cell_selected.connect(self.select_cell)
        self.section_view.point_selected.connect(self.select_result_point)
        self.views.addWidget(self.section_view)
        self.views.setSizes([650, 420])
        center.addWidget(self.views, stretch=1)
        self.result_status = Q.QLabel('按顶部步骤完成地形、数据和反演。', objectName='badge')
        center.addWidget(self.result_status)
        self.inspector = Q.QLabel('对象查询：尚未选择对象', objectName='hint')
        self.inspector.setWordWrap(True)
        self.inspector.setMinimumHeight(42)
        self.inspector.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        center.addWidget(self.inspector)
        self.table = Q.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['测点', 'E / X (m)', 'N / Y (m)', '高程 (m)', '高程来源'])
        self.table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        self.table.setEditTriggers(Q.QAbstractItemView.DoubleClicked|Q.QAbstractItemView.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(Q.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(Q.QAbstractItemView.SingleSelection)
        self.table.cellClicked.connect(lambda row, _col: self.select_point(row))
        self.table.itemChanged.connect(self.point_cell_changed)
        self.table.setMaximumHeight(138)
        point_tools=Q.QHBoxLayout()
        point_tools.setSpacing(6)
        point_tools.addWidget(Q.QLabel('测点表',objectName='hint'))
        for text,callback,tip in [('导入测点',self.import_points,'导入测点坐标：Excel / Word / CSV，预览后确认'),
                ('新增测点',lambda:self.edit_point_dialog(),'手动输入编号及XYZ坐标新增测点；地图点选布点请用左侧工具'),
                ('编辑测点',self.edit_selected_point,'编辑选中测点；也可双击表格单元格'),
                ('删除',self.delete_selected,'删除选中测点或测线，Ctrl+Z可撤销'),
                ('定位',self.focus_selected,'将视图聚焦到选中测点或对象')]:
            button=Q.QPushButton(text);button.setToolTip(tip);button.clicked.connect(callback)
            button.setSizePolicy(Q.QSizePolicy.Minimum,Q.QSizePolicy.Fixed)
            button.setMinimumWidth(button.sizeHint().width())
            point_tools.addWidget(button)
        point_tools.addStretch()
        self.setup_data_tabs(center,point_tools)
        self.log = Q.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(72)
        center.addWidget(self.log)
        row.addLayout(center, stretch=1)
        self.setCentralWidget(root)
        self.mode_indicator = Q.QLabel('模式：浏览 / 查询 [V]')
        self.statusBar().addPermanentWidget(self.mode_indicator)
        self.statusBar().showMessage('左键旋转 / 滚轮缩放；选择布点或测线模式后点击地形。')
        self.set_workflow_step(0)
        menu = self.menuBar().addMenu('项目')
        self.project_menu = menu
        for text, callback in [('打开项目\tCtrl+O', self.open_project), ('保存项目\tCtrl+S', self.save_project),
                               ('另存为\tCtrl+Shift+S', self.save_project_as),
                               ('导出场景 PNG\tCtrl+Shift+E', self.export_image)]:
            action = menu.addAction(text)
            action.triggered.connect(callback)

    def button(self, layout, text, callback, check=False):
        b = Q.QPushButton(text)
        b.setFixedHeight(42)
        b.setSizePolicy(Q.QSizePolicy.Expanding,Q.QSizePolicy.Fixed)
        b.setCheckable(check)
        b.clicked.connect(callback)
        layout.addWidget(b)
        return b

    def heading(self, layout, text):
        layout.addWidget(Q.QLabel(text, objectName='heading'))

    def refresh_sidebar_geometry(self):
        if not hasattr(self,'sidebar_scroll') or not hasattr(self,'step_stack'):return
        page=self.step_stack.currentWidget()
        if page is None:return
        box=page.layout()
        width=max(1,self.sidebar_scroll.widget().width()-40)
        box.activate()
        height=box.heightForWidth(width)
        if height<0:height=box.sizeHint().height()
        height=max(height,box.minimumSize().height())
        if self.step_stack.height()!=height:self.step_stack.setFixedHeight(height)

    def say(self, text):
        self.log.appendPlainText(text)
        self.statusBar().showMessage(text)

    def error(self, e):
        self.say(str(e))
        Q.QMessageBox.warning(self, '请检查数据', str(e))

    def _local_acquisition(self, packet=None):
        return is_local_import(self.ert_packet if packet is None else packet)

    def _has_local_import_lines(self):
        state=getattr(self,'survey_state',None)
        return any(is_local_import((line.get('workspace') or {}).get('ert_packet'))
                   for line in (state.get('lines') if isinstance(state,dict) else []) or [])

    def _can_save_unlocated(self):
        return self._local_acquisition() or self._has_local_import_lines()

    def _refresh_point_table(self):
        local=(self._local_acquisition()
               or any(p.get('local_frame') for p in getattr(self,'points',[]) or []))
        self.table.setHorizontalHeaderLabels(
            ['电极','局部 X (m)','局部 Y (m)','局部 Z (m)','坐标帧 / 说明'] if local
            else ['测点','E / X (m)','N / Y (m)','高程 (m)','高程来源'])
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.points))
        for i,p in enumerate(self.points):
            values=[p['id'],str(p['x']),str(p['y']),str(p['z']),
                    ('局部距离/平地参考值，未配准' if p.get('local_frame')
                     else p.get('elevation_source', '未记录'))]
            for j,value in enumerate(values):
                item=Q.QTableWidgetItem(value)
                if p.get('source_file'):
                    item.setToolTip(f"原始来源：{p['source_file']}\n{p.get('source_table','')} · 第{p.get('source_row','?')}行")
                if p.get('source_xyz'):
                    item.setToolTip(item.toolTip()+f"\n原始 XYZ：{p['source_xyz']}" if item.toolTip() else f"原始 XYZ：{p['source_xyz']}")
                if j==4 or (local and p.get('local_frame')):
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(i,j,item)
        self.table.blockSignals(False)

    def _redraw_unlocated(self):
        """Updated table, acquisition preview and stale state without a terrain."""
        local=self._local_acquisition()
        self.model_actor=None;self.line_actor=None
        self.result_stale=result_is_stale(self.result_line,self.line)
        if self.result_geometry_signature and self.case_kind=='imported_observations':
            self.result_stale=self.result_stale or endpoint_geometry_signature(
                self.points,self.line,getattr(self,'line_endpoint_overrides',{}))!=self.result_geometry_signature
        if self.case_arrays:
            try:self.update_workflow_result_placement()
            except (ValueError,KeyError,TypeError,AttributeError):self.result_stale=True
        if self.model is not None and self.case_kind=='imported_observations' and self.result_observation_signature is not None:
            try:self.result_stale=self.result_stale or observation_signature(self.ert_packet)!=self.result_observation_signature
            except (ValueError,KeyError,TypeError):self.result_stale=True
        self.section_view.set_data(self.section_data,self.result_stale)
        if self.section_data is None and local:
            self.section_view.set_acquisition(self.ert_packet)
        self._refresh_point_table()
        self.local_observation_labels()
        if self.plotter is not None:
            try:
                self.plotter.add_text('NO TERRAIN / LOCAL ACQUISITION ONLY',
                                      position='upper_left',font_size=11,color='#a7bdd9')
                self.plotter.add_axes(color='#c3d1e4')
            except (AttributeError,RuntimeError):pass
        if local:
            self.scene_badge.setText('局部测线导入 / 未配准 / 无地形')
            if self.section_data is None:
                self.result_status.setText('仅采集二维预览：局部距离/平地参考值，未配准；'
                    '不是反演图，未生成地下单元。可保存/重开，配准后才进入地形与三维。')
            else:
                self.result_status.setText(('⚠ 局部二维结果已过期。' if self.result_stale else
                    '局部二维反演结果：未配准，未生成或叠加地下三维单元；'
                    '采集预览不会覆盖已载入结果。'))
        elif self.result_stale:
            self.result_status.setText('⚠ 旧结果已过期：当前无地形，仅保留局部二维结果。')
        else:
            self.result_status.setText('尚无地形：请先导入地形，或导入一条局部测线。')
        self.result_status.setWordWrap(True)
        self.inspector.setText('对象查询：无地表时不在地形上高亮局部坐标。')
        self.update_history_actions()
        self.refresh_workflow()

    def confirm_replace(self):
        if not self.require_committed_edit():
            return False
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.error(ValueError('计算或测线批次进行中，请等待完成或取消后再切换项目或地形。'))
            return False
        if not self.dirty:
            return True
        return Q.QMessageBox.question(self, '切换场景', '当前修改尚未保存，是否放弃并切换？') == Q.QMessageBox.Yes

    def require_committed_edit(self):
        if self.line_drag_active() or self.drag_endpoint is not None or self.mode == 'line':
            self.say('请先完成编辑，或按 Esc 取消，再执行文件操作或计算。')
            return False
        return True

    def load_terrain(self, path):
        if self.line_drag_active():
            self.cancel_line_drag()
        terrain = read_terrain(path)
        campus = read_scene(path)
        observation_sidecar=Path(str(path)+'.ert.json')
        packet=validate_packet(json.loads(observation_sidecar.read_text(encoding='utf-8')),allow_empty=True) if observation_sidecar.exists() else None
        self.terrain = terrain
        self.region_state=empty_region();self.region_cache=None
        center=np.array([float(np.mean(terrain.x)),float(np.mean(terrain.y))])
        planned=center-np.array([23.,8.])
        planned_z=terrain.height([planned])
        if len(planned_z)!=1 or not np.isfinite(planned_z[0]):
            planned=center
            planned_z=terrain.height([planned])
        if len(planned_z)!=1 or not np.isfinite(planned_z[0]):planned_z=np.array([0.])
        self.survey_state=empty_survey()
        self.survey_state['calibration']=dict(origin=[float(planned[0]),float(planned[1]),float(planned_z[0])],angle=0.,gap=2.)
        self.survey_queue=[];self.volume_arrays=None;self.volume_metadata=None
        self._activating_survey=False;self._survey_batch_running=False
        if hasattr(self,'survey_dock'):self.survey_dock.hide()
        self.campus_scene = campus
        self.ert_packet = packet
        self.case_kind = 'synthetic'
        self.result_geometry_signature = None
        self.fit_warning = ''
        self.simulation_enabled=False
        self.case_arrays=None
        self.model_local_only=False
        self.result_observation_signature=None
        self.result_binding=None
        self.result_profile_packet=None
        self._placement_key=None
        self.run_btn.setText('反演导入观测（Ctrl+Enter）' if packet else '在当前测线运行合成反演')
        self.points, self.line = [], []
        self.line_endpoint_overrides = {}
        self.model, self.case_path = None, None
        self.case_info.setText('尚无地下模型；请加载案例或运行合成演示。')
        self.section_data, self.result_line = None, None
        self.points_follow_line = False
        self.selected = None
        self.project_path = None
        self.line_snapshot = None
        self.drag_snapshot = None
        self.dirty = False
        self.set_mode('browse')
        self.redraw(reset=True)
        self.info.setText(f'{terrain.crs} · XY 单位 m\n高程：文件值，基准未核实\n{terrain.z.shape[1]} × {terrain.z.shape[0]} 显示网格')
        self.title.setText(Path(path).name + ' / 地形与测量工作台')
        self.setWindowTitle(f'GeoScope · 窗口 {self.window_number:02} · {Path(path).name}')
        if campus:
            self.title.setText(campus['label'])
            self.info.setText(campus.get('info','虚拟坐标 m · 地面 Z=0\n楼高示意，非真实位置\nA1/A2 教学楼 · LAB 实验楼\nLIB 图书馆 · ADM 行政楼\nCAF 食堂 · D1–D4 宿舍\nGYM 体育馆 · FIELD 操场'))
            self.setWindowTitle(f'GeoScope · 窗口 {self.window_number:02} · 虚拟校园示意')
        self.local_observation_labels()
        self.say(f'已导入 {Path(path).name}；' + ('测线局部参考面，未地理定位，高程采用平地假设。' if packet else '全局坐标保留，渲染使用局部原点。'))
        self.reset_history()
        self.set_workflow_step(1)

    def load_campus(self):
        if not self.confirm_replace():
            return
        try:
            path=DATA/'campus'/'shah-campus.tif'
            if not path.exists() or not Path(str(path)+'.scene.json').exists():
                raise FileNotFoundError('校园数据缺失：需要 shah-campus.tif 及相邻 .scene.json 文件。')
            self.load_terrain(path)
            self.say('已加载虚拟校园：P 布点，L 拖动绘线，E 移动端点；Ctrl+Z/Y 撤销/重做。布局、坐标与楼高均为示意。')
        except Exception as error:
            self.error(error)

    def demo(self):
        if not self.confirm_replace():
            return
        try:
            self.load_terrain(DATA / 'terrain.tif')
            self.simulation_enabled=True
            self.points = read_stations(DATA / 'stations.csv', self.terrain)
            for point in self.points:
                point['elevation_source'] = '地形插值（示例）'
            self.points_follow_line = True
            self.line = [[p['x'], p['y']] for p in (self.points[0], self.points[-1])]
            if (DATA / 'case' / 'model.npz').exists():
                self.load_case(DATA / 'case')
            self.redraw(reset=True)
            self.title.setText('圣海伦斯火山真实地形 / 二维电阻率合成反演')
            self.say('示例地形来自 VTK/PyVista Saint Helens DEM；地下异常为合成案例，与该地区实测地质无关。')
            self.mark_saved()
            self.set_workflow_step(2)
        except Exception as e:
            self.error(e)

    def import_terrain(self):
        if not self.confirm_replace():
            return
        path, _ = Q.QFileDialog.getOpenFileName(self, '导入投影坐标地形（米）', '', '地形 (*.tif *.tiff *.dem)')
        if path:
            try:
                self.load_terrain(path)
            except Exception as e:
                self.error(e)

    def set_mode(self, mode):
        if mode!='browse' and self._local_acquisition():
            self.say('局部导入测线未配准：源电极与测线只读；请先配准再编辑地理坐标。');mode='browse'
        if mode!='browse' and hasattr(self,'survey_busy') and self.survey_busy():
            self.say('计算或测线批次进行中，暂不切换编辑工具。');mode='browse'
        if self.line_drag_active():
            self.cancel_line_drag()
        if self.drag_endpoint is not None:
            self.cancel_current_action()
            if mode == 'browse':
                return
        if self.mode == 'line' and mode != 'line' and self.line_snapshot is not None:
            if len(self.line) < 2:
                restore_edit(self,self.line_snapshot)
            self.line_snapshot = None
        if mode == 'line' and (self.mode != 'line' or self.line_snapshot is None):
            self.line_snapshot = edit_snapshot(self)
        self.mode = mode
        self.mode_indicator.setText('模式：'+{'browse':'浏览 / 查询 [V]','point':'布点 [P]',
            'line':'拖动绘线 · 起点拖至终点 [L]','edit':'编辑端点 [E]'}[mode])
        self.point_btn.setChecked(mode == 'point')
        self.line_btn.setChecked(mode == 'line')
        self.edit_btn.setChecked(mode == 'edit')
        if hasattr(self,'plotter'):
            self.redraw()
        self.statusBar().showMessage({'browse':'浏览模式', 'point':'点击地形添加测点；再次点击按钮退出。',
            'line':'拖动绘线：在起点按住左键拖至终点后松开；Esc 取消，旧结果保留并标为过期。',
            'edit':'拖动蓝色端点，松开完成；无效地形不接受落点。计算期间禁止修改。'}[mode])

    def picked(self, point):
        if self.line_drag_active():
            return
        if self._local_acquisition():
            self.say('局部导入测线未配准：源电极只读，不在地形上新增或绘线；请先配准。');return
        if self.terrain is None or self.mode not in ('point','line') or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            return
        before = edit_snapshot(self) if self.mode == 'point' else self.line_snapshot
        if self.mode == 'line' and before is None:
            self.line_snapshot = before = edit_snapshot(self)
        x, y = (np.asarray(point) + self.terrain.origin)[:2]
        z = float(self.terrain.height([[x, y]])[0])
        if not np.isfinite(z):
            return
        if self.mode == 'point':
            self.points_follow_line = False
            number = 1
            existing_ids = {p['id'] for p in self.points}
            while f'P{number:03}' in existing_ids:
                number += 1
            self.points.append(dict(id=f'P{number:03}', x=float(x), y=float(y), z=z,
                                    elevation_source='地形插值'))
            self.record_edit(before,'添加测点')
        else:
            if len(self.line) != 1:
                self.line = []
                self.line_endpoint_overrides = {}
            if self.line and np.linalg.norm(np.array(self.line[0]) - [x, y]) < 1:
                return
            self.line.append([float(x), float(y)])
            if len(self.line) == 2:
                self.set_mode('browse')
                self.record_edit(before,'绘制测线')
        if self.mode == 'line' and len(self.line) == 1:
            self.dirty = True
        self.redraw()

    def generate_points(self):
        if not self.require_committed_edit():
            return
        if self._local_acquisition():
            self.say('局部导入测线已包含真实输入电极，不能混入默认24点；请先配准。');return
        if self.terrain is None or len(self.line) < 2 or self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            return self.error(ValueError('请先绘制一条完整测线，并等待计算或测线批次结束。'))
        _, xy = stations_along(self.line, 24)
        z = self.terrain.height(xy)
        if not np.isfinite(z).all():
            return self.error(ValueError('测线经过地形空值区域。'))
        before = edit_snapshot(self)
        self.points = [dict(id=f'E{i+1:02}', x=float(p[0]), y=float(p[1]), z=float(h),
                            elevation_source='地形插值') for i, (p, h) in enumerate(zip(xy, z))]
        self.points_follow_line = True
        self.dirty = True
        self.record_edit(before,'沿测线自动布点')
        self.redraw()

    def redraw(self, reset=False):
        # Build a complete frame before asking VTK to render. In particular,
        # result loading must not render once per road, building or survey line.
        suppressed=self.plotter.suppress_rendering
        self.plotter.suppress_rendering=True
        try:
            self._redraw_scene(reset)
        finally:
            self.plotter.suppress_rendering=suppressed
        if not suppressed:self.plotter.render()

    def _redraw_scene(self, reset=False):
        if hasattr(self,'_reset_coordinate_hints_for_redraw'):
            self._reset_coordinate_hints_for_redraw()
        if hasattr(self,'sync_survey_line'):
            self.sync_survey_line()
        self.line_endpoint_overrides=prune_endpoint_overrides(
            getattr(self,'line',[]) or [],getattr(self,'line_endpoint_overrides',{}))
        self.update_workflow_result_placement()
        camera = self.plotter.camera_position if not reset else None
        self.plotter.clear()
        self.survey_line_actors=[];self.survey_electrode_actors=[];self.survey_pick_entries=[]
        self.scalar_legend.clear()
        self.selected = None
        self.model_actor = None
        self.line_actor = None
        if self.terrain is None:
            self._redraw_unlocated()
            return
        local_acquisition=is_local_import(self.ert_packet)
        survey_active=self.survey_active() if hasattr(self,'survey_active') else False
        survey_active_line_visible=bool(survey_active and any(
            line.get('id')==self.survey_state.get('active_id') and line.get('visible',True)
            for line in self.survey_state.get('lines') or []))
        survey_volume_mode=bool(survey_active and self.survey_state.get('view')=='volume' and self.survey_volume_current())
        display_sections=self.current_survey_sections() if survey_active else self.region_state['sections']
        archived_current=any(s.get('provenance',{}).get('case')==str(self.case_path) for s in display_sections)
        map_profile = self.case_kind=='imported_observations' and is_map_line_binding(self.ert_packet)
        self.result_stale = False if map_profile else result_is_stale(self.result_line, self.line)
        if self.model is not None and not map_profile:
            self.result_stale = self.result_stale or endpoint_result_is_stale(self.line,self.line_endpoint_overrides,self.section_data)
        if self.result_geometry_signature and self.case_kind=='imported_observations':
            self.result_stale = self.result_stale or endpoint_geometry_signature(
                self.points,self.line,getattr(self,'line_endpoint_overrides',{}))!=self.result_geometry_signature
        if self.model is not None and self.case_kind=='imported_observations' and self.result_observation_signature is not None:
            try:self.result_stale=self.result_stale or observation_signature(self.ert_packet)!=self.result_observation_signature or bool(validate_binding(self.ert_packet,self.ert_packet.get('binding',{}),self.points,self.line,self.terrain))
            except (ValueError,KeyError,TypeError):self.result_stale=True
        elif self.model is not None and (self.case_kind=='imported_observations' or self.ert_packet and 'binding' in self.ert_packet):self.result_stale=True
        if self.campus_scene:
            self.terrain_actor = self.plotter.add_mesh(self.terrain.mesh(), color='#41654c',
                opacity=self.opacity.value()/100, pickable=True)
            draw_scene(self)
            self.scene_badge.setText('虚拟校园布局 / 非实测地形')
        else:
            self.terrain_actor = self.plotter.add_mesh(self.terrain.mesh(), scalars='elevation', cmap='gist_earth',
                opacity=self.opacity.value()/100, show_scalar_bar=False, pickable=True)
            self.scene_badge.setText('真实地形 / 合成反演案例' if self.model is not None else '真实地形 / 尚无反演结果')
        self.local_observation_labels()
        if self.model is not None and not self.model_local_only and not archived_current and not survey_volume_mode:
            self.model_actor = self.plotter.add_mesh(self.model, scalars='resistivity', cmap='turbo_r', log_scale=True,
                opacity=.35 if self.result_stale else 1., clim=[20, 500], pickable=True, scalar_bar_args={'title':'',
                'vertical':False, 'position_x':.14, 'position_y':.05, 'height':.08, 'width':.72,
                'color':'#dce8f7', 'label_font_size':9, 'fmt':'%.3g'})
            self.scalar_legend.attach(self.plotter.scalar_bars[''])
        original_sections=self.region_state['sections'];original_display=copy.deepcopy(self.region_state['display']);original_cache=self.region_cache
        original_generated=self.region_state.get('generated_signature')
        if survey_active:
            self.region_state['sections']=[] if survey_volume_mode else display_sections
            self.region_state['generated_signature']=None
            display=copy.deepcopy(original_display);display['volume']=False;display['missing']=False
            display['sections']=not survey_volume_mode
            self.region_state['display']=display
        try:self.draw_region_layers()
        finally:
            self.region_state['sections']=original_sections
            self.region_state['display']=original_display
            self.region_state['generated_signature']=original_generated
            self.region_cache=original_cache
        self.section_view.setVisible(False if survey_volume_mode else (
            self.model is not None or not display_sections or local_acquisition))
        if len(self.line) > 1 and not local_acquisition:
            if not survey_active:
                xyz = line_render_xyz(self.terrain, self.line,
                    getattr(self,'line_endpoint_overrides',{}), 150)
                if xyz is not None:
                    p = xyz + np.array([0., 0., 1.]) - self.terrain.origin
                    self.line_actor = self.plotter.add_mesh(pv.lines_from_points(p), color='#f59e0b', line_width=4, pickable=True)
            if self.mode == 'edit' and (not survey_active or survey_active_line_visible):
                records = self.line_endpoint_records()
                if len(records) >= 2:
                    ep = np.asarray([[r['x'], r['y'], r['z']] for r in records[:2]]) + np.array([0., 0., 3.]) - self.terrain.origin
                    self.plotter.add_points(ep, color='#70caff', point_size=23,
                        render_points_as_spheres=True, pickable=False)
            if hasattr(self, 'draw_line_endpoint_labels'):
                self.draw_line_endpoint_labels()
        if self.points and not survey_active and not local_acquisition:
            xyz = np.array([[p['x'], p['y'], p['z'] + 2] for p in self.points]) - self.terrain.origin
            self.plotter.add_points(xyz, color='#f47d28', point_size=10, render_points_as_spheres=True,
                                   pickable=False)
            # Point IDs/coordinates are shown on hover or selection only.
        self.plotter.add_axes(color='#c3d1e4')
        caption=('TARGET TERRAIN / UNREGISTERED OBSERVATIONS' if self.ert_packet['binding'].get('mode')=='local' else 'REGISTERED ERT / TARGET TERRAIN') if self.ert_packet and 'binding' in self.ert_packet else ('LOCAL PROFILE / IMPORTED ERT' if self.ert_packet else ('REAL TERRAIN / SYNTHETIC ERT' if self.model is not None else 'TERRAIN / SURVEY GEOMETRY'))
        if local_acquisition:caption='LOCAL IMPORT / NOT REGISTERED / NOT DRAWN ON DEM'
        if survey_active:
            caption='ACTUAL 3D ERT / ASSUMED PARALLEL NETWORK' if survey_volume_mode and self.survey_state['hypothesis'] else ('ACTUAL 3D ERT / SURVEY NETWORK' if survey_volume_mode else 'SAME-SURFACE SURVEY SECTIONS / NOT 3D INVERSION')
        elif self.region_state['sections']:caption='REGIONAL INTERPOLATION / NOT 3D INVERSION'
        if local_acquisition:caption='LOCAL IMPORT / NOT REGISTERED / NOT DRAWN ON DEM'
        self.plotter.add_text(caption,
                              position='upper_left', font_size=11, color='#a7bdd9')
        if camera:
            self.plotter.camera_position = camera
        else:
            self.plotter.view_isometric()
            self.plotter.reset_camera()
        self.plotter.render()
        if hasattr(self,'rebuild_coordinate_targets'):
            self.rebuild_coordinate_targets()
        self.section_view.set_data(self.section_data, self.result_stale)
        if self.section_data is None and local_acquisition:
            self.section_view.set_acquisition(self.ert_packet)
        stale_text=('⚠ 旧结果未记录计算输入签名，无法验证与当前数据一致；请重新反演。' if self.case_kind=='imported_observations' and self.result_observation_signature is None else
                    '⚠ 观测、启用状态、参数或采集布局已改变：旧结果已过期，保留原位置。请恢复或重新反演。')
        if local_acquisition and self.section_data is None:
            self.result_status.setText('仅采集二维预览：局部距离/平地参考值，未配准；'
                '不是反演图，未生成地下单元，不叠加到地形。')
        else:
            self.result_status.setText(stale_text if self.result_stale else
                                      (('仅二维显示局部剖面，未叠加到地形。' if self.model_local_only else '')+(self.fit_warning or '点击查询：测点 / 地下单元')))
        self.result_status.setWordWrap(bool(display_sections or local_acquisition))
        if display_sections and self.model is None:self.result_status.setText(self.region_status.text())
        self.result_status.setStyleSheet('color:#ffc472; background:#332719; border:1px solid #70532d;' if self.result_stale or self.fit_warning else '')
        self.inspector.setText('对象查询：点击三维场景、二维剖面或表格行。')
        self._refresh_point_table()
        if survey_active:
            self.draw_survey_layers()
            self.plotter.render()
        self.update_history_actions()
        self.refresh_workflow()

    def set_opacity(self, value):
        if hasattr(self, 'terrain_actor'):
            self.terrain_actor.prop.opacity = value/100
            if self.campus_scene:
                for actor in getattr(self,'campus_actors',[]):
                    actor.prop.opacity=value/100
            self.plotter.render()

    def view_iso(self):
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def view_top(self):
        self.plotter.view_xy()
        self.plotter.reset_camera()

    def view_section(self):
        if len(self.line) < 2 or self.terrain is None:
            return
        direction = np.array(self.line[-1]) - self.line[0]
        normal = np.array([direction[1], -direction[0], 0.])
        normal /= np.linalg.norm(normal)
        center = np.array(self.plotter.center)
        self.plotter.camera_position = [center + normal * self.plotter.length, center, [0,0,1]]
        self.plotter.reset_camera()

    def open_case(self):
        if not self.require_committed_edit():
            return
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.say('计算或测线批次进行中，请等待结束后加载其他案例。')
            return
        path = Q.QFileDialog.getExistingDirectory(self, '选择包含 model.npz 与 metadata.json 的案例文件夹')
        if path:
            try:
                self.load_case(path)
                self.history.clear()
                self.redraw(reset=True)
                self.dirty = True
            except Exception as e:
                self.error(e)

    def load_case(self, path):
        path = Path(path)
        if self.terrain is None:
            meta = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
            if meta.get('data_kind')!='imported_observations':
                raise ValueError('没有地形时只允许加载局部二维观测结果。')
            packet=self.ert_packet
            if not is_local_import(packet):
                raise ValueError('没有地形时只允许加载明确标记为局部未配准的测线结果。')
            if self.load_workflow_case(path,meta):return
            raise ValueError('该局部结果缺少工作流签名，无法在无地形时安全恢复。')
        meta = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
        if self.load_workflow_case(path,meta):return
        imported=meta.get('data_kind')=='imported_observations'
        if imported and (not self.ert_packet or meta.get('source_sha256')!=self.ert_packet.get('source_sha256')):
            raise ValueError('局部测线案例需配合原观测对应的局部参考面打开，不能直接叠加到校园或真实地形。')
        if meta['crs'] != self.terrain.crs:
            raise ValueError('案例与地形坐标系不一致，不能直接叠加。')
        arrays = load_result_arrays(path/'model.npz')
        xy = arrays['xy']
        actual = self.terrain.height(xy)
        if not np.isfinite(actual).all() or not np.allclose(actual, arrays['elevation'], atol=.05):
            raise ValueError('当前地形与反演使用的测线高程不一致，拒绝自动贴地或挪动结果。')
        placed = place_section(arrays['nodes'], arrays['cells'], arrays['resistivity'], xy,
                               arrays['station'], self.terrain.origin, float(arrays['z_reference']))
        self.section_data = {key:arrays[key].copy() for key in
            ('nodes','cells','resistivity','station','xy','elevation','z_reference')}
        self.model, self.case_path = placed, path.resolve()
        self.case_arrays={key:arrays[key].copy() for key in arrays}
        self.model_local_only=False
        self.case_kind=meta.get('data_kind','synthetic')
        self.result_geometry_signature=meta.get('geometry_signature')
        self.fit_warning=(f"观测反演拟合偏差较大：χ² {meta['chi2']:.2f} / RRMS {meta['rrms_percent']:.2f}%。请复核数据、误差假设与参数，暂供流程验证。" if imported and meta['chi2']>2 else '')
        self.line = [list(p) for p in meta['line_vertices']]
        kept_overrides={}
        for key,value in (getattr(self,'line_endpoint_overrides',{}) or {}).items():
            try:edge=int(key)
            except (TypeError,ValueError):continue
            if edge in (0,1) and effective_endpoint_override(self.line,{str(edge):value},edge) is not None:
                kept_overrides[str(edge)]=value
        self.line_endpoint_overrides=kept_overrides
        self.result_line = [list(p) for p in meta['line_vertices']]
        self.result_stale = False
        from ert_arrays import array_label
        category=f"导入实验观测 · {array_label(meta.get('array','wenner'))}\n平地假设 · 未地理定位" if imported else '合成案例 · 二维剖面 · 非实测结论'
        self.case_info.setText(f"{meta['engine']} · {meta['cells']} 单元\nχ² {meta['chi2']:.2f} / RRMS {meta['rrms_percent']:.2f}%\n{category}")
        self.say('已定位反演剖面：保留求解器节点高程；未作贴地变形。')

    def run_inversion(self):
        if hasattr(self,'survey_busy') and self.survey_busy():
            self.say('计算或测线批次进行中，不重复提交。');return
        if self.ert_packet:
            self.run_observed_inversion()
            return
        if not self.simulation_enabled:
            self.say('请先导入/新增观测，或在第三步明确选择合成演示。');self.show_checks();return
        if self.process is not None:
            self.say('反演已经在运行，不重复提交。')
            return
        if not self.require_committed_edit():
            return
        try:
            if self.terrain is None or len(self.line) < 2:
                raise ValueError('请先导入地形并绘制测线。')
            if len(self.line) > 2:
                raise ValueError('此二维验证引擎仅接受两个端点的直测线；请绘制直线。')
            station, xy = stations_along(self.line, 24)
            if not 50 <= station[-1] <= 2000:
                raise ValueError('验证版案例测线长度需在 50–2000 m 之间。')
            z = self.terrain.height(xy)
            if not np.isfinite(z).all():
                raise ValueError('测线含地形空值。')
            self.set_workflow_step(2)
            output = ROOT / 'runs' / QtCore.QDateTime.currentDateTime().toString('yyyyMMdd-HHmmss-zzz')
            request = dict(station=station.tolist(), xy=xy.tolist(), elevation=z.tolist(),
                crs=self.terrain.crs, terrain_source=self.terrain.source, line_vertices=self.line,
                output=str(output))
            req_path = output / 'request.json'
            write_json(req_path, request)
            self.current_request = output
            self.process = QtCore.QProcess(self)
            self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.worker_log)
            self.process.finished.connect(self.worker_done)
            self.process.errorOccurred.connect(self.worker_error)
            self.run_btn.setEnabled(False)
            self.run_btn.setText('正在计算，可继续旋转与查看')
            args = ['--worker', str(req_path)] if getattr(sys, 'frozen', False) else [str(Path(__file__)), '--worker', str(req_path)]
            self.process.start(sys.executable, args)
            self.update_history_actions()
            self.refresh_workflow()
            self.say('已启动独立计算进程：在当前地形高程下进行合成正演与反演。')
        except Exception as e:
            self.error(e)

    def worker_error(self, error):
        if error==QtCore.QProcess.FailedToStart and self.process is not None:
            message=self.process.errorString()
            self.process.deleteLater();self.process=None
            self.survey_queue=[];self._survey_batch_running=False
            self.run_btn.setEnabled(True)
            self.run_btn.setText('反演导入观测（Ctrl+Enter）' if self.ert_packet else '在当前测线运行合成反演')
            self.update_history_actions()
            self.refresh_workflow()
            if hasattr(self,'refresh_survey_ui'):self.refresh_survey_ui()
            self.say('计算进程未能启动，可重试或关闭窗口：'+message)
        else:self.say('计算进程错误，请查看日志。')

    def worker_log(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        with open(self.current_request/'worker.log', 'a', encoding='utf-8') as f:
            f.write(text)
        if text.strip():
            self.say(text.strip()[-350:])

    def worker_done(self, code, _status):
        if self.process is None:return
        process=self.process
        from project_tasks import project_task_active
        if project_task_active():
            def retry():
                if self.process is process:self.worker_done(code,_status)
            QtCore.QTimer.singleShot(50,self,retry)
            return
        self._loading_result=True
        try:
            return self._finish_worker_result(code,_status)
        finally:
            if self.process is process:
                self.process=None;process.deleteLater()
            self._loading_result=False
            self.refresh_workflow()
            self.update_history_actions()
            if hasattr(self,'refresh_survey_ui'):self.refresh_survey_ui()

    def _finish_worker_result(self, code, _status):
        if self.process is None:return
        self.worker_log()
        self.run_btn.setEnabled(True)
        self.update_history_actions()
        self.run_btn.setText('反演导入观测（Ctrl+Enter）' if self.ert_packet else '在当前测线运行合成反演')
        batch=getattr(self,'_survey_batch_running',False)
        success=False
        if code == 0:
            try:
                self.load_case(self.current_request)
                self.dirty = True
                self.redraw(reset=True)
                self.set_workflow_step(2)
                if self.ert_packet and not batch:self.focus_result_view()
                success=True
            except Exception as e:
                self.say('反演结果无法载入，旧结果已保留：'+str(e))
                if not batch:self.error(e)
        else:
            message=f'计算失败（{code}），详见 {self.current_request / "worker.log"}'
            self.say(message)
            if not batch:self.error(ValueError(message))
        # Keep the public process/busy marker until result IO is complete. Qt
        # timers may run while the NPZ loader waits for its background thread.
        self.process.deleteLater();self.process=None
        if batch:
            self.survey_section_finished(success)
        else:
            self.refresh_workflow()
            if hasattr(self,'refresh_survey_ui'):self.refresh_survey_ui()

    def save_menu(self):
        menu = Q.QMenu(self)
        menu.addAction('保存项目', self.save_project)
        menu.addAction('另存为', self.save_project_as)
        menu.addAction('导出 PNG', self.export_image)
        menu.exec(QtGui.QCursor.pos())

    def save_project(self):
        if not self.require_committed_edit():
            return
        if self.terrain is None and not self._can_save_unlocated():
            self.say('当前没有可保存的地形或局部导入测线。')
            return
        if self.project_path is not None:
            self.save_project_to(self.project_path)
        else:
            self.save_project_as()

    def save_project_as(self):
        if not self.require_committed_edit():
            return
        if self.terrain is None and not self._can_save_unlocated():
            self.say('当前没有可保存的地形或局部导入测线。')
            return
        path, _ = Q.QFileDialog.getSaveFileName(self, '保存验证项目（引用原数据，移动时需同时保留）', '', 'GeoScope (*.geoscope.json)')
        if path:
            self.save_project_to(path)

    def save_project_to(self, path):
        if not self.require_committed_edit():
            return
        self.sync_survey_line()
        if self.terrain is None:
            # Refuse to write a project that could not be reopened: only
            # explicitly marked local imported lines are valid without terrain.
            validate_survey(self.survey_state,None)
        write_json(path, dict(schema=1, terrain=self.terrain.source if self.terrain is not None else None,
            points=self.points, line=self.line,
            line_endpoint_overrides=copy.deepcopy(getattr(self,'line_endpoint_overrides',{}) or {}),
            case=str(self.case_path) if self.case_path else None, opacity=self.opacity.value(),
            points_follow_line=self.points_follow_line,ert_packet=self.ert_packet,
            simulation_enabled=self.simulation_enabled,workflow_step=self.workflow_step,
            result_binding=self.result_binding,region=self.region_state,survey=self.survey_state))
        self.dirty = False
        self.project_path = Path(path).resolve()
        self.mark_saved()
        self.say(f'项目已保存：{path}；原数据以路径引用。')

    def open_project(self):
        if not self.confirm_replace():
            return
        path, _ = Q.QFileDialog.getOpenFileName(self, '打开验证项目', '', 'GeoScope (*.json)')
        if path:
            try:
                self.load_project_from(path)
            except Exception as e:
                self.error(e)

    def load_project_from(self, path):
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):raise ValueError('计算或测线批次中不能打开其他项目。')
        if not self.require_committed_edit():return
        names=('terrain','campus_scene','ert_packet','points','line','line_endpoint_overrides','model','case_path','section_data','result_line',
            'result_stale','points_follow_line','project_path','dirty','saved_geometry_key','case_kind','result_geometry_signature',
            'fit_warning','simulation_enabled','case_arrays','model_local_only','result_observation_signature','result_binding',
            'result_profile_packet','_placement_key','workflow_step','mode','line_snapshot','drag_snapshot','region_state','region_cache',
            'survey_state','survey_queue','volume_arrays','volume_metadata','_survey_batch_running','_activating_survey')
        previous={name:getattr(self,name) for name in names}
        history=copy.deepcopy((self.history.past,self.history.future));opacity=self.opacity.value()
        texts={name:getattr(self,name).text() for name in ('info','title','case_info','scene_badge')}
        camera=copy.deepcopy(self.plotter.camera_position);window_title=self.windowTitle()
        survey_visible=self.survey_dock.isVisible() if hasattr(self,'survey_dock') else False
        try:self._load_project_document(path)
        except Exception:
            for name,value in previous.items():setattr(self,name,value)
            if hasattr(self,'survey_dock'):self.survey_dock.setVisible(survey_visible)
            self.history.past,self.history.future=history
            self.opacity.blockSignals(True);self.opacity.setValue(opacity);self.opacity.blockSignals(False)
            self.set_mode(previous['mode']);self.plotter.camera_position=camera
            for name,value in texts.items():getattr(self,name).setText(value)
            self.setWindowTitle(window_title);self.refresh_workflow();self.update_history_actions()
            self.redraw()
            raise

    def _load_project_document(self, path):
        p = json.loads(Path(path).read_text(encoding='utf-8'))
        if p.get('schema') != 1:
            raise ValueError('不支持的项目版本。')
        candidate_packet=validate_packet(p['ert_packet'],allow_empty=True) if p.get('ert_packet') is not None else None
        if p.get('terrain'):
            self.load_terrain(p['terrain'])
        else:
            if candidate_packet is None and not any(
                    (line.get('workspace') or {}).get('ert_packet')
                    for line in (p.get('survey') or {}).get('lines') or []):
                raise ValueError('项目没有地形，也没有局部导入测线。')
            self._prepare_unlocated_project()
        if candidate_packet is not None:
            self.ert_packet=candidate_packet
            self.ert_packet.setdefault('binding',{'mode':'local'})
        if p.get('case'):
            self.load_case(p['case'])
        if self.case_kind=='imported_observations' and p.get('result_binding'):
            binding=p['result_binding']
            if binding.get('mode')=='registered' and (
                    self.terrain is None or binding.get('terrain_source')!=self.terrain.source):
                raise ValueError('保存的旧结果定位属于其他地形。')
            self.result_binding=copy.deepcopy(binding);self._placement_key=None
        self.points, self.line = p['points'], p['line']
        restored_survey=validate_survey(p.get('survey'),self.terrain)
        if p.get('survey') is None:restored_survey['calibration']=copy.deepcopy(self.survey_state['calibration'])
        try:
            calibration=restored_survey['calibration'];origin=list(calibration['origin'])
            if len(origin)!=3 or not np.isfinite(origin).all() or not np.isfinite(float(calibration['angle'])) or not np.isfinite(float(calibration['gap'])) or float(calibration['gap'])<=0:
                raise ValueError
        except (KeyError,TypeError,ValueError):
            raise ValueError('测网校准参数无效。') from None
        self.survey_state=restored_survey
        self.survey_queue=[];self.volume_arrays=None;self.volume_metadata=None;self._survey_batch_running=False
        saved_overrides=p.get('line_endpoint_overrides')
        if saved_overrides is None and self.survey_state['lines']:
            active_line=next((line for line in self.survey_state['lines']
                if line['id']==self.survey_state.get('active_id')),None)
            if active_line is not None:
                saved_overrides=(active_line.get('workspace') or {}).get('line_endpoint_overrides')
        if not isinstance(saved_overrides,dict):saved_overrides={}
        self.line_endpoint_overrides=copy.deepcopy(saved_overrides)
        if self.survey_state['lines']:
            self.sync_survey_line()
            if hasattr(self,'survey_dock'):self.survey_dock.show()
            case=self.survey_state.get('volume_case')
            if case:
                self.load_survey_volume(case)
                if self.survey_state['view']=='volume' and not self.survey_volume_current():
                    self.survey_state['view']='sections'
        self.region_state=validate_region_state(p.get('region'),self.terrain);self.region_cache=None
        self.simulation_enabled=p.get('simulation_enabled',self.case_kind=='synthetic' and self.model is not None)
        self.points_follow_line = p.get('points_follow_line',False)
        self.opacity.setValue(p.get('opacity', 58))
        self.redraw(reset=True)
        self.project_path = Path(path).resolve()
        self.mark_saved()
        self.set_workflow_step(p.get('workflow_step',2 if self.model is not None else 1))

    def _prepare_unlocated_project(self):
        """Reset to an empty no-terrain workspace before restoring local lines."""
        self.terrain=None;self.campus_scene=None
        self.region_state=empty_region();self.region_cache=None
        self.survey_state=empty_survey();self.survey_queue=[]
        self.volume_arrays=None;self.volume_metadata=None
        self._activating_survey=False;self._survey_batch_running=False
        if hasattr(self,'survey_dock'):self.survey_dock.hide()
        self.ert_packet=None;self.points=[];self.line=[]
        self.line_endpoint_overrides={};self.points_follow_line=False
        self.model=None;self.case_path=None;self.section_data=None;self.result_line=None
        self.case_arrays=None;self.case_kind='imported_observations';self.model_local_only=True
        self.result_observation_signature=None;self.result_binding=None
        self.result_profile_packet=None;self.result_geometry_signature=None
        self._placement_key=None;self.fit_warning='';self.result_stale=False
        self.simulation_enabled=False;self.project_path=None;self.dirty=False
        self.selected=None;self.mode='browse';self.line_snapshot=None;self.drag_snapshot=None
        self.case_info.setText('局部二维结果；无地形。')
        self.set_mode('browse')

    def export_image(self):
        if not self.require_committed_edit():
            return
        path, _ = Q.QFileDialog.getSaveFileName(self, '导出三维视图', '', 'PNG (*.png)')
        if path:
            self.plotter.screenshot(path)
            self.say(f'已导出 {path}')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'views'):
            compact = self.width() < 1100
            self.sidebar_scroll.setFixedWidth(326)
            self.resize_data_panel()
            QtCore.QTimer.singleShot(0,self.refresh_sidebar_geometry)
            self.views.setOrientation(QtCore.Qt.Vertical if compact else QtCore.Qt.Horizontal)

    def closeEvent(self, event):
        if self.line_drag_active() or self.drag_endpoint is not None or self.mode == 'line':
            self.cancel_current_action()
        if self.process is not None or (hasattr(self,'survey_busy') and self.survey_busy()):
            self.error(ValueError('计算或测线批次尚未结束，请等待完成或取消后再关闭。'))
            event.ignore()
            return
        if self.dirty and not self.confirm_replace():
            event.ignore()
            return
        if hasattr(self,'clear_coordinate_hints'):
            self.clear_coordinate_hints()
        self.plotter.close()
        self.manager.remove(self)
        event.accept()


def main():
    app = Q.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(str(Path(__file__).with_name('GeoScope-GS.ico'))))
    # Explicitly initialize the inactive Windows bridge before native children
    # are created; leave an already active assistive client enabled.
    if not QtGui.QAccessible.isActive():QtGui.QAccessible.setActive(False)
    app.aboutToQuit.connect(lambda:QtGui.QAccessible.setActive(False))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    if not any((flag.endswith('-check') and flag not in ('--dipole-check','--auto-array-check')) or flag=='--capture' for flag in sys.argv[1:]):
        sys.modules.setdefault('app',sys.modules[__name__])
        from method_shell import MethodProject
        documents=[arg for arg in sys.argv[1:] if arg.lower().endswith(('.geoscope','.geoscope.json'))]
        if documents:
            try:project=MethodProject.load(documents[0])
            except Exception as error:
                Q.QMessageBox.warning(None,'项目未打开',str(error));project=MethodProject();project.new_host()
        else:project=MethodProject();project.new_host()
        if not project.modules:
            project.open_module('ert')
            project.dirty=False
            for host in project.hosts:host.refresh_title()
        project.show_all()
        # 重磁与跨模块验证入口仅在完整版提供。
        if '--dipole-check' in sys.argv:
            from check_dipole_desktop import run_checks
            QtCore.QTimer.singleShot(700,lambda:run_checks(project,app,ROOT))
        if '--auto-array-check' in sys.argv:
            from check_auto_array import run_checks
            QtCore.QTimer.singleShot(700,lambda:run_checks(project,app,ROOT))
        sys.exit(app.exec())
    if any(flag in sys.argv for flag in ('--quick-parallel-check','--performance-check')):
        sys.modules.setdefault('app',sys.modules[__name__])
    window = Window(demo=any(flag in sys.argv for flag in ['--capture','--interaction-check','--shortcut-check','--history-check','--window-check','--campus-check','--point-management-check','--observed-ert-check']))
    window.show()
    print('GeoScope window ready', flush=True)
    if '--performance-check' in sys.argv:
        from check_project_performance import run_checks
        QtCore.QTimer.singleShot(700,lambda:run_checks(window,app,ROOT))
    if '--capture' in sys.argv:
        def capture():
            window.grab().save(str(ROOT / 'desktop-preview.png'))
            window.plotter.screenshot(str(ROOT / 'scene-preview.png'))
            app.quit()
        QtCore.QTimer.singleShot(6000, capture)
    if '--interaction-check' in sys.argv:
        from check_interactions import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--shortcut-check' in sys.argv:
        from check_shortcuts import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--history-check' in sys.argv:
        from check_history import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--window-check' in sys.argv:
        from check_windows import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--campus-check' in sys.argv:
        from check_campus import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--navigation-check' in sys.argv:
        from check_navigation import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--point-management-check' in sys.argv:
        from check_point_management import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--observed-ert-check' in sys.argv:
        from check_observed_ert import run_checks
        QtCore.QTimer.singleShot(1400, lambda: run_checks(window, app, ROOT))
    if '--workflow-check' in sys.argv:
        from check_workflow import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--observation-import-check' in sys.argv:
        from check_observation_import import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--calibration-check' in sys.argv:
        from check_calibration import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--sidebar-layout-check' in sys.argv:
        from check_sidebar_layout import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--data-tabs-check' in sys.argv:
        from check_data_tabs import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--scalar-legend-check' in sys.argv:
        from check_scalar_legend import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--ert-quality-check' in sys.argv:
        from check_ert_quality import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--region-check' in sys.argv:
        from check_region import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--survey3d-check' in sys.argv:
        from check_survey3d import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--multiline-interaction-check' in sys.argv:
        from check_multiline_acceptance import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--drag-line-check' in sys.argv:
        from check_drag_line_acceptance import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--endpoint-edit-check' in sys.argv:
        from check_endpoint_edit_acceptance import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--quick-parallel-check' in sys.argv:
        from check_quick_parallel import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--coordinate-hints-check' in sys.argv:
        from check_coordinate_hints import run_checks
        QtCore.QTimer.singleShot(700, lambda: run_checks(window, app, ROOT))
    if '--survey3d-reopen-check' in sys.argv:
        from check_survey3d import run_reopen_checks
        QtCore.QTimer.singleShot(700, lambda: run_reopen_checks(window, app, ROOT))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
