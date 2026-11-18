"""One keyboard router owns application and viewport shortcuts."""
import copy
from PySide6 import QtCore, QtGui, QtWidgets as Q

HELP_ROWS = [
    ('窗口','Ctrl+N / Ctrl+W','新建独立窗口 / 关闭当前窗口'),
    ('全局','Ctrl+O','打开项目'),('全局','Ctrl+S','保存；首次选择路径'),
    ('全局','Ctrl+Shift+S','另存为'),('全局','Ctrl+I（字母 i）','打开数据导入窗口'),
    ('几何编辑','Ctrl+Z / Ctrl+Y','撤销 / 重做；一次拖动只记一步'),
    ('几何编辑','Delete','删除选中工作台测点 / 当前测线；可撤销'),
    ('全局','Ctrl+Shift+E','导出三维截图'),('全局','F1','快捷键帮助'),
    ('三维','V / P / L / E','浏览查询 / 布点 / 拖动绘线 / 端点编辑'),
    ('三维','F','聚焦选中的测点或地下单元'),('三维','Home','完整显示场景'),
    ('三维','1 / 2 / 3','三维斜视 / 俯视 / 正对剖面'),
    ('二维','Home','恢复二维剖面视图'),('二维','Esc','退出平移或框选缩放'),
    ('工作台','Esc','取消未完成绘线拖动；恢复原布局；否则退出工具/清除选区'),
    ('全局','Ctrl+Enter','运行当前窗口反演；需通过数据检查，合成演示须明确选择'),
    ('鼠标','单击 / 左键拖动','查询 / 浏览时旋转；绘线时从起点拖至终点；端点编辑时移动端点'),
    ('鼠标','中键拖动 / 滚轮','平移三维视图 / 缩放'),
]


class ShortcutController(QtCore.QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.help_dialog = None
        self.import_dialog = Q.QDialog(window)
        self.import_dialog.setWindowTitle('导入数据 · Ctrl+I（字母 i）')
        self.import_dialog.setModal(True)
        self.import_dialog.resize(480,330)
        import_layout = Q.QVBoxLayout(self.import_dialog)
        import_layout.addWidget(Q.QLabel('选择要导入的数据类型',objectName='heading'))
        self.import_buttons = {}
        for index,(kind,title,callback) in enumerate([
                ('terrain','真实地形\nGeoTIFF / DEM · 投影坐标单位为米',window.import_terrain),
                ('points','智能导入测点\nExcel / Word / CSV · 识别、预览、检查',window.import_points),
                ('case','已有反演案例\n包含 model.npz 与 metadata.json 的文件夹',window.open_case)]):
            button = Q.QPushButton(title)
            button.setMinimumHeight(64)
            button.setDefault(index==0)
            button.clicked.connect(lambda _checked=False,cb=callback:self.choose_import(cb))
            self.import_buttons[kind] = button
            import_layout.addWidget(button)
        self.global_actions = {
            (QtCore.Qt.Key_N,QtCore.Qt.ControlModifier):lambda:window.manager.new_window(),
            (QtCore.Qt.Key_W,QtCore.Qt.ControlModifier):window.close,
            (QtCore.Qt.Key_O,QtCore.Qt.ControlModifier):window.open_project,
            (QtCore.Qt.Key_S,QtCore.Qt.ControlModifier):window.save_project,
            (QtCore.Qt.Key_S,QtCore.Qt.ControlModifier|QtCore.Qt.ShiftModifier):window.save_project_as,
            (QtCore.Qt.Key_I,QtCore.Qt.ControlModifier):self.show_import,
            (QtCore.Qt.Key_E,QtCore.Qt.ControlModifier|QtCore.Qt.ShiftModifier):window.export_image,
            (QtCore.Qt.Key_F1,QtCore.Qt.NoModifier):self.show_help,
            (QtCore.Qt.Key_Return,QtCore.Qt.ControlModifier):self.start_inversion,
            (QtCore.Qt.Key_Enter,QtCore.Qt.ControlModifier):self.start_inversion,
            (QtCore.Qt.Key_Z,QtCore.Qt.ControlModifier):window.undo_edit,
            (QtCore.Qt.Key_Y,QtCore.Qt.ControlModifier):window.redo_edit,
            (QtCore.Qt.Key_Delete,QtCore.Qt.NoModifier):window.delete_selected,
        }
        Q.QApplication.instance().installEventFilter(self)
        for button,text in [(window.point_btn,'放置测点（P）'),(window.line_btn,'拖动绘线（L）；从起点拖至终点，Esc 取消'),
                            (window.edit_btn,'编辑测线端点（E）；拖动中 Esc 恢复原布局'),
                            (window.run_btn,'运行合成反演（Ctrl+Enter）')]:
            button.setToolTip(text)
        help_menu=window.menuBar().addMenu('帮助')
        self.help_menu=help_menu
        help_menu.addAction('快捷键与鼠标操作\tF1',self.show_help)
        data_menu=window.menuBar().addMenu('数据')
        self.data_menu=data_menu
        data_menu.addAction('导入数据\tCtrl+I',self.show_import)

    def _within(self, widget, parent):
        return isinstance(widget,Q.QWidget) and (widget is parent or parent.isAncestorOf(widget))

    def _text_input(self, widget):
        while isinstance(widget,Q.QWidget):
            if isinstance(widget,(Q.QLineEdit,Q.QTextEdit,Q.QPlainTextEdit,Q.QAbstractSpinBox)):
                return True
            if isinstance(widget,Q.QComboBox) and widget.isEditable():
                return True
            widget=widget.parentWidget()
        return False

    def _view(self, watched):
        w=self.window
        for widget in (watched,Q.QApplication.widgetAt(QtGui.QCursor.pos())):
            if self._within(widget,w.plotter.interactor):
                return '3d'
            if self._within(widget,w.section_view.canvas):
                return '2d'
        return None

    def eventFilter(self, watched,event):
        if event.type() not in (QtCore.QEvent.KeyPress,QtCore.QEvent.ShortcutOverride):
            return False
        w=self.window
        if not self._within(watched,w) or watched.window() is not w.window() or not w.isVisible():
            return False
        if Q.QApplication.activeModalWidget() is not None or Q.QApplication.activePopupWidget() is not None:
            return False
        mods=event.modifiers() & (QtCore.Qt.ControlModifier|QtCore.Qt.ShiftModifier|
                                 QtCore.Qt.AltModifier|QtCore.Qt.MetaModifier)
        if (event.key() in (QtCore.Qt.Key_Z,QtCore.Qt.Key_Y,QtCore.Qt.Key_Delete) and
            (self._text_input(watched) or self._text_input(Q.QApplication.focusWidget()))):
            return False
        callback=self.global_actions.get((event.key(),mods))
        consume=callback is not None
        if not consume:
            if self._text_input(watched) or self._text_input(Q.QApplication.focusWidget()):
                return False
            view=self._view(watched)
            if view is None:
                return False
            if mods==QtCore.Qt.NoModifier:
                if event.key()==QtCore.Qt.Key_Escape:
                    callback=w.cancel_current_action
                    if view=='2d' and w.section_view.toolbar.mode:
                        callback=w.section_view.stop_navigation
                elif event.key()==QtCore.Qt.Key_Home:
                    callback=w.section_view.reset_view if view=='2d' else self.frame_all
                elif view=='3d':
                    callback={QtCore.Qt.Key_V:lambda:self.change_mode('browse'),
                        QtCore.Qt.Key_P:lambda:self.change_mode('point'),
                        QtCore.Qt.Key_L:lambda:self.change_mode('line'),
                        QtCore.Qt.Key_E:lambda:self.change_mode('edit'),
                        QtCore.Qt.Key_F:w.focus_selected,
                        QtCore.Qt.Key_1:w.view_iso,QtCore.Qt.Key_2:w.view_top,
                        QtCore.Qt.Key_3:w.view_section}.get(event.key())
            # Prevent the embedded renderers' own quit/save/display key bindings.
            consume=callback is not None or QtCore.Qt.Key_A<=event.key()<=QtCore.Qt.Key_Z or QtCore.Qt.Key_0<=event.key()<=QtCore.Qt.Key_9
        if not consume:
            return False
        event.accept()
        if event.type()==QtCore.QEvent.KeyPress and callback is not None and not event.isAutoRepeat():
            pending_edit=w.drag_endpoint is not None or w.mode=='line'
            if pending_edit and (event.key(),mods) in self.global_actions and not (event.key()==QtCore.Qt.Key_Z and mods==QtCore.Qt.ControlModifier):
                w.say('请先完成编辑，或按 Esc 取消，再执行保存、打开、帮助或计算。')
                return True
            callback()
        return True

    def change_mode(self,mode):
        if self.window.process is not None and mode!='browse':
            self.window.say('计算进行中，暂不切换编辑工具。')
            return
        if mode!='browse' and hasattr(self.window,'set_workflow_step'):self.window.set_workflow_step(1)
        self.window.set_mode(mode)

    def frame_all(self):
        if self.window.terrain is not None:
            self.window.plotter.reset_camera()
            self.window.plotter.render()

    def start_inversion(self):
        w=self.window
        if w.process is not None:
            w.say('反演已经在运行，不重复提交。')
            return
        if w.drag_endpoint is not None or w.mode=='line':
            w.say('请先完成或取消当前拖动/绘线，再开始计算。')
            return
        w.run_inversion()

    def show_import(self):
        if not self.window.require_committed_edit():
            return
        self.import_dialog.show()
        self.import_dialog.raise_()
        self.import_buttons['terrain'].setFocus()

    def choose_import(self,callback):
        self.import_dialog.close()
        QtCore.QTimer.singleShot(0,callback)

    def show_help(self):
        if self.help_dialog is None:
            dialog=Q.QDialog(self.window)
            dialog.setWindowTitle('GeoScope · 快捷键与鼠标操作')
            dialog.resize(760,650)
            layout=Q.QVBoxLayout(dialog)
            intro=Q.QLabel('在目标视图区单击后使用视图快捷键；文字输入优先。Esc 不会终止正在运行的反演。')
            intro.setWordWrap(True)
            layout.addWidget(intro)
            table=Q.QTableWidget(len(HELP_ROWS),3)
            table.setHorizontalHeaderLabels(['作用区域','按键','操作'])
            table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
            for row,values in enumerate(HELP_ROWS):
                for col,value in enumerate(values):
                    table.setItem(row,col,Q.QTableWidgetItem(value))
            table.horizontalHeader().setSectionResizeMode(0,Q.QHeaderView.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1,Q.QHeaderView.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(2,Q.QHeaderView.Stretch)
            table.resizeRowsToContents()
            layout.addWidget(table)
            buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Close)
            buttons.button(Q.QDialogButtonBox.Close).setText('关闭')
            buttons.rejected.connect(dialog.close)
            layout.addWidget(buttons)
            self.help_dialog=dialog
        self.help_dialog.show()
        self.help_dialog.raise_()


def edit_snapshot(window):
    return dict(line=copy.deepcopy(window.line),points=copy.deepcopy(window.points),
                points_follow_line=window.points_follow_line,
                line_endpoint_overrides=copy.deepcopy(getattr(window,'line_endpoint_overrides',{}) or {}),
                dirty=window.dirty,
                ert_packet=copy.deepcopy(getattr(window,'ert_packet',None)),
                simulation_enabled=getattr(window,'simulation_enabled',False),
                region_state=copy.deepcopy(getattr(window,'region_state',None)),
                survey_state=copy.deepcopy(getattr(window,'survey_state',None)))


def restore_edit(window,snapshot):
    old_survey=getattr(window,'survey_state',None)
    old_active=old_survey.get('active_id') if isinstance(old_survey,dict) else None
    window.line=copy.deepcopy(snapshot['line'])
    window.points=copy.deepcopy(snapshot['points'])
    window.points_follow_line=snapshot['points_follow_line']
    overrides=snapshot.get('line_endpoint_overrides')
    window.line_endpoint_overrides=copy.deepcopy(overrides if isinstance(overrides,dict) else {})
    window.dirty=snapshot['dirty']
    if 'ert_packet' in snapshot:window.ert_packet=copy.deepcopy(snapshot['ert_packet'])
    if 'simulation_enabled' in snapshot:window.simulation_enabled=snapshot['simulation_enabled']
    if snapshot.get('region_state') is not None:window.region_state=copy.deepcopy(snapshot['region_state'])
    if 'survey_state' in snapshot and snapshot.get('survey_state') is not None:
        window.survey_state=copy.deepcopy(snapshot['survey_state'])
        new_active=window.survey_state.get('active_id')
        if new_active and new_active!=old_active and hasattr(window,'activate_survey_line'):
            try:window.activate_survey_line(new_active,sync=False,clear_history=False)
            except Exception as error:
                if hasattr(window,'say'):window.say('恢复测线结果失败：'+str(error))

