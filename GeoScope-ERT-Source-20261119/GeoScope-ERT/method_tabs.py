"""Browser-style method tabs and detachable peer project windows."""
import json
import math
from pathlib import Path
from collections import OrderedDict
from PySide6 import QtCore,QtGui,QtWidgets as Q
from method_shell import METHODS

MIME='application/x-geoscope-method'
# 50% coverage is the optical ink edge.  Anti-aliased pixels below this are
# not part of the visible glyph, and keeping them would let faint one-sided
# fringes pull the box off centre.
_INK_ALPHA=128
_INK_CACHE_MAX=128
_INK_CACHE=OrderedDict()


def ink_baseline(font,text,center):
    """Fractional metrics baseline used only as a fallback.

    QFontMetricsF.tightBoundingRect() does not describe hinted raster ink at
    fractional DPR (the 20px '×' is the known case), so normal painting uses
    draw_ink_centered() below.  This helper keeps a safe fallback if the
    scratch image cannot be measured for any reason.
    """
    metrics=QtGui.QFontMetricsF(font);ink=metrics.tightBoundingRect(text)
    if ink.isEmpty():ink=metrics.boundingRect(text)
    center=QtCore.QPointF(center)
    return QtCore.QPointF(center.x()-ink.center().x(),center.y()-ink.center().y())


def _ink_bitmap(font,text,color,scale):
    """Return the text's real raster ink, alpha-cropped at ``scale``.

    The glyph is painted by QPainter into a transparent QImage at the
    painter's actual device scale; the alpha channel is scanned at the 50%
    ink contour to find the hinted bounds, which are then cropped.  Unlike
    tightBoundingRect(), this bbox is the ink the screen sees, so its centre
    is the optical centre rather than a font-metric approximation.
    Results are LRU-cached by font, glyph, colour and scale, so a tab repaint
    does not rescan glyphs every frame.
    """
    key=(font.toString(),font.family(),font.pixelSize(),text,color.rgba(),
         round(float(scale),4))
    cached=_INK_CACHE.get(key)
    if cached is not None:
        _INK_CACHE.move_to_end(key);return cached
    metrics=QtGui.QFontMetricsF(font);px=font.pixelSize()
    if px<=0:px=max(1,int(math.ceil(metrics.height())))
    for extra in (0,1):
        # Keep a generous transparent margin so no hinted overhang is cut off;
        # retry once with twice the margin if the ink still touches an edge.
        pad=max(4,px)<<extra
        width=metrics.horizontalAdvance(text)+2*pad
        height=metrics.height()+2*pad
        w=max(1,int(math.ceil(width*scale)));h=max(1,int(math.ceil(height*scale)))
        if w>4096 or h>4096:return None
        image=QtGui.QImage(w,h,QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter=QtGui.QPainter(image)
        try:
            painter.scale(scale,scale)
            painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing,True)
            painter.setFont(font);painter.setPen(color)
            painter.drawText(QtCore.QPointF(pad,pad+metrics.ascent()),text)
        finally:
            painter.end()
        alpha=image.convertToFormat(QtGui.QImage.Format.Format_Alpha8)
        stride=alpha.bytesPerLine();data=bytes(alpha.constBits())
        left=w;top=h;right=-1;bottom=-1
        for y in range(h):
            row=data[y*stride:y*stride+w]
            if not row or max(row)<_INK_ALPHA:continue
            if y<top:top=y
            bottom=y
            for x,value in enumerate(row):
                if value>=_INK_ALPHA:
                    if x<left:left=x
                    if x>right:right=x
        if right<left or bottom<top:return None
        if left>0 and top>0 and right<w-1 and bottom<h-1:
            ink=image.copy(left,top,right-left+1,bottom-top+1)
            # The crop is in device pixels, so tag it with the device scale:
            # drawImage() then advances logical size = pixels / scale.
            ink.setDevicePixelRatio(scale)
            _INK_CACHE[key]=ink
            if len(_INK_CACHE)>_INK_CACHE_MAX:_INK_CACHE.popitem(last=False)
            return ink
    return None


def draw_ink_centered(painter,font,text,color,center):
    """Draw ``text`` with its measured ink bbox centred on ``center``.

    The cropped bitmap is device-resolution (DPR = scale).  The target is
    computed in physical pixels with the painter's own deviceTransform, the
    physical top-left is snapped to the pixel grid, and only that point is
    mapped back to the current logical space.  The painter's world transform,
    clipping and the child-widget render origin are left untouched, so buttons
    inside redirected child widgets keep landing on screen.
    """
    if not text:return False
    device=painter.device()
    scale=float(device.devicePixelRatioF()) if device is not None else 1.0
    if scale<=0:scale=1.0
    transform=painter.combinedTransform()
    determinant=abs(transform.determinant())
    if determinant>0:scale=math.sqrt(determinant)
    ink=_ink_bitmap(font,text,color,scale)
    if ink is None:return False
    device_transform=painter.deviceTransform()
    inverse,invertible=device_transform.inverted()
    if not invertible:return False
    target=device_transform.map(QtCore.QPointF(center))
    left=math.floor(target.x()-ink.width()/2.0+0.5)
    top=math.floor(target.y()-ink.height()/2.0+0.5)
    origin=inverse.map(QtCore.QPointF(left,top))
    painter.save()
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform,False)
        painter.drawImage(origin,ink)
    finally:
        painter.restore()
    return True


def optical_center(widget):
    """Return the glyph target centre in widget-local logical coordinates."""
    target=QtCore.QRectF(widget.rect()).center()
    parent=widget.parentWidget()
    # Side tab buttons can be laid out from a sizeHint slightly different from
    # their fixed 24px square. Use the 36px bar centre for those, so text,
    # close and plus glyphs share one vertical centre.
    if isinstance(parent,Q.QTabBar):
        bar_center=QtCore.QRectF(parent.rect()).center().y()
        target.setY(widget.mapFrom(parent,QtCore.QPointF(0,bar_center)).y())
    return target


class OpticalToolButton(Q.QToolButton):
    """Tool button that optically centres its glyph instead of its text box.

    Colours and pixel sizes mirror the #methodTabClose/#methodAddButton QSS
    rules; the QSS background, hover and pressed painting is left untouched.
    """
    def __init__(self,parent=None,glyph_px=16,color='#a9b5c3',hover_color='#e2eaf3',weight=QtGui.QFont.Weight.Normal):
        super().__init__(parent)
        self._glyph_px=glyph_px;self._glyph_color=color;self._glyph_hover=hover_color
        self._glyph_weight=weight

    def paintEvent(self,event):
        option=Q.QStyleOptionToolButton();self.initStyleOption(option)
        text=option.text;hovered=bool(option.state&Q.QStyle.State_MouseOver)
        painter=Q.QStylePainter(self)
        option.text=''
        painter.drawComplexControl(Q.QStyle.CC_ToolButton,option)
        if not text:return
        font=QtGui.QFont(self.font());font.setPixelSize(self._glyph_px)
        font.setWeight(self._glyph_weight)
        color=QtGui.QColor(self._glyph_hover if hovered else self._glyph_color)
        center=optical_center(self)
        painter.setFont(font);painter.setPen(color)
        if not draw_ink_centered(painter,font,text,color,center):
            painter.drawText(ink_baseline(font,text,center),text)


class MethodTabBar(Q.QTabBar):
    def __init__(self,host):
        super().__init__(host)
        self.host=host;self.setAcceptDrops(True);self.setMovable(True);self.press=None
        self.pressed_widget=None;self._drag_cancelled=False
        # Browser-style single row: no wide native close area and no base
        # line under the tabs. Tab geometry is fixed by the object-name QSS
        # rule below; do not override tabSizeHint in Python, because Qt calls
        # it during layout/show and the virtual override can crash.
        self.setElideMode(QtCore.Qt.ElideRight);self.setExpanding(False)
        self.setDrawBase(False);self.setFixedHeight(36)

    def tab_font(self):
        font=QtGui.QFont(self.font());font.setPixelSize(16)
        font.setWeight(QtGui.QFont.Weight.Medium);return font

    def paintEvent(self,event):
        # Let QSS paint the tab shapes without their text, then draw each
        # label from its real ink bounds. Vertical centring therefore follows
        # the glyphs rather than the 23px label box, while horizontal centring,
        # the 36px geometry and the base-less/hover/selected painting stay.
        selected=self.currentIndex()
        order=[i for i in range(self.count()) if i!=selected]
        if 0<=selected<self.count():order.append(selected)
        painter=Q.QStylePainter(self);option=Q.QStyleOptionTab()
        font=self.tab_font();metrics=QtGui.QFontMetrics(font)
        for index in order:
            self.initStyleOption(option,index)
            text=option.text
            label=self.style().subElementRect(Q.QStyle.SE_TabBarTabText,option,self)
            option.text=''
            painter.drawControl(Q.QStyle.CE_TabBarTab,option)
            if not text:continue
            text=metrics.elidedText(text,self.elideMode(),label.width())
            color=QtGui.QColor('#e6eef7' if index==selected else '#aeb9c7')
            painter.setFont(font);painter.setPen(color)
            # Use one fractional target for every glyph class: the tab centre
            # horizontally, the 36px bar centre vertically.
            target=QtCore.QPointF(QtCore.QRectF(self.tabRect(index)).center().x(),
                                  QtCore.QRectF(self.rect()).center().y())
            if not draw_ink_centered(painter,font,text,color,target):
                painter.drawText(ink_baseline(font,text,target),text)

    def resizeEvent(self,event):
        super().resizeEvent(event);QtCore.QTimer.singleShot(0,self.host.position_plus)

    def tabInserted(self,index):
        super().tabInserted(index);QtCore.QTimer.singleShot(0,self.host.position_plus)

    def tabRemoved(self,index):
        super().tabRemoved(index);QtCore.QTimer.singleShot(0,self.host.position_plus)

    def mousePressEvent(self,event):
        self.press=event.position().toPoint()
        index=self.tabAt(self.press)
        self.pressed_widget=self.host.tabs.widget(index) if index>=0 and event.button()==QtCore.Qt.LeftButton else None
        super().mousePressEvent(event)

    def eventFilter(self,watched,event):
        if event.type()==QtCore.QEvent.KeyPress and event.key()==QtCore.Qt.Key_Escape:
            self._drag_cancelled=True
        return False

    def mouseMoveEvent(self,event):
        if self.press is not None and event.buttons()&QtCore.Qt.LeftButton:
            point=event.position().toPoint()
            if (point-self.press).manhattanLength()>Q.QApplication.startDragDistance() and not self.rect().contains(point):
                widget=self.pressed_widget;self.press=None;self.pressed_widget=None
                index=self.host.tabs.indexOf(widget) if widget is not None else -1
                if index<0:return
                method=widget.method_key
                mime=QtCore.QMimeData();mime.setData(MIME,json.dumps(dict(project=self.host.project.id,method=method)).encode())
                drag=QtGui.QDrag(self);drag.setMimeData(mime)
                drag.setPixmap(self.grab(self.tabRect(index)))
                self._drag_cancelled=False
                app=Q.QApplication.instance();app.installEventFilter(self)
                try:result=drag.exec(QtCore.Qt.MoveAction)
                finally:app.removeEventFilter(self)
                # Native drag cancellation may consume Escape before Qt receives
                # it. A still-held drag button also means no desktop drop occurred.
                cancelled=self._drag_cancelled or bool(Q.QApplication.mouseButtons()&QtCore.Qt.LeftButton)
                if result==QtCore.Qt.IgnoreAction and not cancelled and self.host.project.host_for(method) is self.host:
                    position=QtGui.QCursor.pos()
                    # Rejected drop on another project stays in its original project.
                    from method_shell import PROJECTS
                    over_other=any(h.isVisible() and h.frameGeometry().contains(position) and p is not self.host.project
                                   for p in list(PROJECTS.values()) for h in p.hosts)
                    if not over_other:self.host.project.detach_module(method,position-QtCore.QPoint(100,20))
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self.press=None;self.pressed_widget=None;super().mouseReleaseEvent(event)

    def payload(self,mime):
        try:
            value=json.loads(bytes(mime.data(MIME)))
            if value['project']==self.host.project.id and self.host.project.host_for(value['method']):return value
        except (ValueError,KeyError,TypeError):pass
        return None

    def dragEnterEvent(self,event):
        if self.payload(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):self.dragEnterEvent(event)

    def dropEvent(self,event):
        value=self.payload(event.mimeData())
        if value is None:event.ignore();return
        self.host.project.move_module(value['method'],self.host,self.tabAt(event.position().toPoint()))
        event.setDropAction(QtCore.Qt.MoveAction);event.accept()


class MethodHost(Q.QMainWindow):
    def __init__(self,project):
        super().__init__();self.project=project;self._empty_close=False;self._will_close=False
        self.resize(1420,930);self.setMinimumSize(620,480)
        root=Q.QWidget();layout=Q.QVBoxLayout(root);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        self.tabs=Q.QTabWidget();self.tabs.setObjectName('methodTabs')
        self.tabs.setTabBar(MethodTabBar(self));self.tabs.setTabsClosable(False)
        self.tabs.setDocumentMode(True)
        # documentMode can leave the native QTabBar base line enabled, which
        # draws a pale strip above/behind the tabs. Clear it after the mode
        # switch, not only in MethodTabBar.__init__().
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.current_changed)
        self.tabs.tabBar().tabMoved.connect(self.tab_moved)
        bar=self.tabs.tabBar();bar.setObjectName('methodTabBar')
        # The old generic tab rule is kept for the gravity workbench data tabs;
        # the object-name rule below overrides it only for the method bar and
        # fixes each tab at 140x36; the bar height is fixed to match.
        self.tabs.setStyleSheet(
            'QTabWidget#methodTabs {border:0;}'
            'QTabWidget#methodTabs::pane {border:0;background:#10151b;}'
            'QTabBar#methodTabBar {border:0;background:#10151b;}'
            'QTabBar::tab {min-width:125px;padding:10px 18px;background:#171e28;'
            'border:1px solid #303947;margin-right:5px;}'
            'QTabBar::tab:selected {background:#1d3855;border-color:#58a6f1;}'
            'QTabBar#methodTabBar::tab {min-width:140px;max-width:140px;min-height:23px;'
            'max-height:23px;padding:6px 10px;margin:0 3px 0 0;background:#191f26;color:#aeb9c7;'
            'font-size:16px;font-weight:500;'
            'border:1px solid #2a323d;border-bottom:none;border-top-left-radius:5px;'
            'border-top-right-radius:5px;}'
            'QTabBar#methodTabBar::tab:hover {background:#222a33;border-color:#38434f;}'
            'QTabBar#methodTabBar::tab:selected {background:#22303d;color:#e6eef7;border-color:#4b6b8a;}'
            'QWidget#methodTabLeftSpacer {border:0;background:transparent;}'
            'QToolButton#methodTabClose {border:0;border-radius:3px;background:transparent;'
            'color:#8f9baa;font-size:20px;font-weight:500;padding:0;}'
            'QToolButton#methodTabClose:hover {background:#2e3844;color:#e2eaf3;}'
            'QToolButton#methodTabClose:pressed {background:#28313c;}'
            'QToolButton#methodAddButton {border:0;border-radius:4px;background:transparent;'
            'color:#a9b5c3;font-size:16px;padding:0;}'
            'QToolButton#methodAddButton:hover {background:#232b34;color:#e2eaf3;}'
            'QToolButton#methodAddButton:pressed {background:#1b222a;}'
        )
        plus=OpticalToolButton(glyph_px=16,color='#a9b5c3',hover_color='#e2eaf3')
        plus.setObjectName('methodAddButton');plus.setText('+');plus.setToolTip('添加或重新打开方法模块')
        plus.setFixedSize(24,24);plus.setCursor(QtCore.Qt.PointingHandCursor)
        plus.setFocusPolicy(QtCore.Qt.NoFocus);plus.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        plus.clicked.connect(self.plus_menu)
        # A browser-like plus follows the current tabs, rather than sitting on
        # the far corner of the page or covering the first workflow row. Parent
        # it to the tab widget: the tab bar may only span its tabs, which would
        # clip the button or push it back over the last tab.
        plus.setParent(self.tabs);self.plus_button=plus
        layout.addWidget(self.tabs)
        self.home=Q.QWidget();box=Q.QVBoxLayout(self.home);box.addStretch()
        title=Q.QLabel('GeoScope',objectName='brand');title.setAlignment(QtCore.Qt.AlignCenter);box.addWidget(title)
        hint=Q.QLabel('空白工作台\n通过右上角“＋”添加电法模块。\n可以先导入数据，稍后再加载共享地表。')
        hint.setAlignment(QtCore.Qt.AlignCenter);box.addWidget(hint)
        add=Q.QPushButton('＋ 添加方法模块');add.clicked.connect(self.plus_menu);box.addWidget(add,alignment=QtCore.Qt.AlignCenter)
        terrain=Q.QPushButton('导入项目地表');terrain.clicked.connect(self.import_terrain);box.addWidget(terrain,alignment=QtCore.Qt.AlignCenter)
        box.addStretch();layout.addWidget(self.home)
        self.setCentralWidget(root)
        menu=self.menuBar().addMenu('项目')
        menu.addAction('新建空白项目',self.new_project,'Ctrl+N')
        menu.addAction('打开项目包 / 旧项目',self.open_project,'Ctrl+O')
        menu.addAction('保存完整项目包',self.save_project,'Ctrl+S')
        menu.addAction('另存项目包',self.save_as,'Ctrl+Shift+S')
        menu.addSeparator();menu.addAction('导入 / 更换共享地表',self.import_terrain)
        menu.addAction('加载虚拟校园地表',self.load_campus)
        tools=self.menuBar().addMenu('模块')
        for method,label in METHODS.items():tools.addAction('打开'+label,lambda checked=False,m=method:self.project.open_module(m,self))
        tools.addAction('将当前标签拖出为独立窗口',self.detach_current)
        windows=self.menuBar().addMenu('窗口')
        windows.addAction('同项目窗口左右平铺',project.tile)
        windows.addAction('关闭当前标签',lambda:self.close_tab(self.tabs.currentIndex()),'Ctrl+W')
        self.statusBar().showMessage('同一项目共享地表；各模块的测点、视角与计算独立。')
        self.current_changed();self.refresh_title()

    def method_keys(self):return [self.tabs.widget(i).method_key for i in range(self.tabs.count())]
    def current_method(self):return getattr(self.tabs.currentWidget(),'method_key',None)

    def add_workbench(self,method,widget,index=None):
        widget.method_key=method
        if index is None or index<0:index=self.tabs.count()
        from qt_lifecycle import native_widget_transition
        with native_widget_transition():
            self.tabs.insertTab(index,widget,METHODS[method])
        index=self.tabs.indexOf(widget)
        if index>=0:
            spacer=self.make_tab_left_spacer()
            self.tabs.tabBar().setTabButton(index,Q.QTabBar.LeftSide,spacer)
            spacer.show()
            close=self.make_close_button(widget,method)
            self.tabs.tabBar().setTabButton(index,Q.QTabBar.RightSide,close)
            close.show()
        self.tabs.setCurrentWidget(widget)
        self.current_changed()

    def make_tab_left_spacer(self):
        # Mirror the close button so the label stays on the tab's geometric
        # centre instead of being pushed left by the right-side close button.
        spacer=Q.QWidget(self.tabs.tabBar());spacer.setObjectName('methodTabLeftSpacer')
        spacer.setFixedSize(24,24);spacer.setFocusPolicy(QtCore.Qt.NoFocus)
        spacer.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents,True)
        return spacer

    def make_close_button(self,widget,method):
        # Parent to the tab bar up front: setTabButton() reparents the button,
        # and an unparented button can be orphaned during tab teardown/reorder.
        button=OpticalToolButton(self.tabs.tabBar(),glyph_px=20,color='#8f9baa',hover_color='#e2eaf3',
                                 weight=QtGui.QFont.Weight.Medium)
        button.setObjectName('methodTabClose')
        button.setText('×');button.setToolTip('关闭'+METHODS[method])
        button.setAccessibleName('关闭'+METHODS[method]);button.setFixedSize(24,24)
        button.setCursor(QtCore.Qt.PointingHandCursor);button.setFocusPolicy(QtCore.Qt.NoFocus)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        button.clicked.connect(lambda checked=False,w=widget:self.close_tab_widget(w))
        return button

    def close_tab_widget(self,widget):
        # The button travels with its tab during reorder, so resolve the
        # current index by widget identity instead of trusting a saved index.
        index=self.tabs.indexOf(widget)
        if index>=0:self.close_tab(index)

    def tab_moved(self,*args):
        self.project.mark_dirty();self.position_plus()
        QtCore.QTimer.singleShot(0,self.position_plus)

    def remove_workbench(self,method):
        widget=self.project.modules[method];index=self.tabs.indexOf(widget)
        if index>=0:
            from qt_lifecycle import native_widget_transition
            with native_widget_transition():self.tabs.removeTab(index)
        self.current_changed()

    def current_changed(self,*args):
        if not hasattr(self,'home'):return
        empty=self.tabs.count()==0;self.home.setVisible(empty)
        # Keep the empty tab bar and plus button visible on the home page.
        self.tabs.setMaximumHeight(40 if empty else 16777215)
        self.position_plus()
        if hasattr(self,'_method_menu_actions'):
            from shiboken6 import isValid
            for action in self._method_menu_actions:
                if isValid(action):self.menuBar().removeAction(action)
        self._method_menu_actions=[]
        widget=self.tabs.currentWidget()
        if widget is not None and hasattr(widget,'menuBar'):
            for action in widget.menuBar().actions():
                if action.text() not in ('项目','窗口'):
                    self.menuBar().addAction(action);self._method_menu_actions.append(action)
        self.refresh_title()

    def position_plus(self):
        if not hasattr(self,'plus_button'):return
        bar=self.tabs.tabBar();count=bar.count();plus=self.plus_button
        if count:x=bar.tabRect(count-1).right()+5
        else:x=8
        # The bar is fixed at 36px; use its centre instead of a tabRect height
        # that may still be the previous/pre-layout value on first show.
        y=max(0,(bar.height()-plus.height())//2)
        # Map tab-bar coordinates into the tab widget. Clamping against the
        # widget (not the possibly tab-only-wide bar) keeps the button to the
        # right of the last tab instead of covering it or being clipped.
        point=bar.mapTo(self.tabs,QtCore.QPoint(x,y))
        limit=max(0,self.tabs.width()-plus.width()-4)
        plus.move(max(0,min(point.x(),limit)),max(0,point.y()))
        plus.raise_();plus.show()

    def showEvent(self,event):
        super().showEvent(event)
        # Final tab rects exist only after the first style/layout pass.
        QtCore.QTimer.singleShot(0,self.position_plus)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0,self.position_plus)

    def refresh_title(self):
        name=self.project.path.stem if self.project.path else '未命名项目'
        self.setWindowTitle(f'GeoScope · {name}'+(' *' if self.project.dirty else ''))
        terrain=self.project.scene.get('label') if self.project.scene else self.project.terrain.source if self.project.terrain else '尚未加载地表'
        if terrain:self.statusBar().showMessage('项目地表：'+str(terrain)+'  |  各模块视角独立')

    def plus_menu(self):
        menu=Q.QMenu(self)
        for method,label in METHODS.items():
            host=self.project.host_for(method)
            suffix='（已打开，定位）' if host else '（已保存，重新打开）' if method in self.project.modules else ''
            menu.addAction(label+suffix,lambda checked=False,m=method:self.run_guarded(lambda:self.project.open_module(m,self)))
        menu.exec(self.plus_button.mapToGlobal(QtCore.QPoint(0,self.plus_button.height())))

    def run_guarded(self,callback):
        try:return callback()
        except Exception as error:Q.QMessageBox.warning(self,'请检查',str(error));return False

    def new_project(self):
        from method_shell import MethodProject
        project=MethodProject();host=project.new_host()
        project.open_module('ert')
        project.dirty=False
        host.refresh_title()
        host.show();return host

    def open_project(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'打开完整项目包或旧项目','','GeoScope (*.geoscope *.json)')
        if path:
            from method_shell import MethodProject
            project=self.run_guarded(lambda:MethodProject.load(path))
            if project:project.show_all()

    def save_project(self):return self.run_guarded(lambda:self.project.save(parent=self))

    def save_as(self):
        path,_=Q.QFileDialog.getSaveFileName(self,'另存完整项目包','','GeoScope (*.geoscope)')
        if path:return self.run_guarded(lambda:self.project.save(path,parent=self))

    def import_terrain(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'导入项目共享地表','','DEM / GeoTIFF (*.tif *.tiff *.dem)')
        if path:return self.run_guarded(lambda:self.project.set_terrain(path))

    def load_campus(self):
        from method_shell import ROOT
        path=ROOT/'data/campus/shah-campus.tif'
        def load():
            if not path.exists() or not Path(str(path)+'.scene.json').exists():
                raise FileNotFoundError('校园数据缺失：需要 shah-campus.tif 及相邻 .scene.json 文件。')
            return self.project.set_terrain(path)
        return self.run_guarded(load)

    def detach_current(self):
        method=self.current_method()
        if method:self.project.detach_module(method,self.pos()+QtCore.QPoint(50,50))

    def close_tab(self,index):
        if index<0:return
        method=self.tabs.widget(index).method_key
        self.run_guarded(lambda:self.project.close_modules([method],parent=self))

    def copy_points(self):
        target=self.tabs.currentWidget()
        if target is None:return
        sources=[k for k,w in self.project.modules.items() if w is not target and getattr(w,'points',None)]
        if not sources:Q.QMessageBox.information(self,'测点复制','其他模块尚无可复制测点。');return
        method,ok=Q.QInputDialog.getItem(self,'选择来源模块','来源',[METHODS[k] for k in sources],0,False)
        if not ok:return
        source=sources[[METHODS[k] for k in sources].index(method)]
        mode,ok=Q.QInputDialog.getItem(self,'测点复制','已有同编号点将更新；关联更新仍需手动确认。',['复制为独立测点','关联来源并确认更新'],0,False)
        if not ok:return
        def apply():
            import copy
            if self.project.is_busy(target):raise ValueError('计算中不能更改测点。')
            points=copy.deepcopy(self.project.modules[source].points)
            current={p['id']:copy.deepcopy(p) for p in getattr(target,'points',[])}
            for point in points:
                if mode.startswith('关联'):point['method_link']={'project':self.project.id,'module':source,'point':point['id']}
                current[point['id']]=point
            if hasattr(target,'set_points'):target.set_points(list(current.values()),crs=self.project.terrain.crs if self.project.terrain else None)
            else:
                from shortcuts import edit_snapshot
                before=edit_snapshot(target);target.points=list(current.values());target.points_follow_line=False
                target.record_edit(before,'从'+METHODS[source]+'复制测点');target.redraw()
            self.project.mark_dirty()
        self.run_guarded(apply)

    def fit_compact(self,width):
        for i in range(self.tabs.count()):
            widget=self.tabs.widget(i)
            if hasattr(widget,'sidebar_scroll') and width<1100:widget.sidebar_scroll.setFixedWidth(245)

    def ensure_on_screen(self):
        screens=Q.QApplication.screens()
        if not any(self.frameGeometry().intersects(s.availableGeometry()) for s in screens):
            self.move(screens[0].availableGeometry().topLeft()+QtCore.QPoint(30,30))

    def close_empty(self):
        self._empty_close=True
        if self in self.project.hosts:self.project.hosts.remove(self)
        self.close()

    def closeEvent(self,event):
        if self._empty_close or self.project._shutting_down:event.accept();return
        self._will_close=True
        methods=self.method_keys()
        try:
            if methods and not self.project.close_modules(methods,parent=self):
                self._will_close=False;event.ignore();return
            if not methods and self.project.dirty:
                answer=Q.QMessageBox.question(self,'关闭项目窗口','项目有未保存修改，保存完整项目包？',Q.QMessageBox.Save|Q.QMessageBox.Discard|Q.QMessageBox.Cancel)
                if answer==Q.QMessageBox.Cancel:self._will_close=False;event.ignore();return
                if answer==Q.QMessageBox.Save and not self.project.save(parent=self):
                    self._will_close=False;event.ignore();return
            if self in self.project.hosts:self.project.hosts.remove(self)
            event.accept()
        except Exception as error:
            self._will_close=False
            Q.QMessageBox.warning(self,'关闭未完成',str(error));event.ignore()
