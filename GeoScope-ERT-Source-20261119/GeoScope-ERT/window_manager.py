"""Independent document windows sharing only their window registry."""
from PySide6 import QtCore, QtWidgets as Q


class WindowManager:
    def __init__(self, factory):
        self.factory = factory
        self.windows = []
        self.counter = 0

    def register(self, window):
        self.counter += 1
        window.window_number = self.counter
        self.windows.append(window)
        menu = window.menuBar().addMenu('窗口')
        window.window_menu = menu
        menu.addAction('新建窗口\tCtrl+N', self.new_window)
        menu.addAction('在新窗口打开地形', lambda: self.open_terrain(window))
        menu.addAction('左右平铺', self.tile)
        menu.addAction('切换到下一个窗口', lambda: self.next_window(window))
        menu.addSeparator()
        menu.addAction('关闭当前窗口\tCtrl+W', window.close)
        window.setWindowTitle(f'GeoScope · 窗口 {window.window_number:02} · 空白工作台')

    def new_window(self):
        window = self.factory(manager=self, demo=False)
        window.show()
        window.raise_()
        return window

    def open_terrain(self, owner):
        path, _ = Q.QFileDialog.getOpenFileName(owner, '在新窗口打开地形', '', '地形 (*.tif *.tiff *.dem)')
        if not path:
            return
        window = self.new_window()
        try:
            window.load_terrain(path)
        except Exception as error:
            window.close()
            owner.error(error)

    def tile(self):
        windows = [w for w in self.windows if w.isVisible()]
        if not windows:
            return
        active = Q.QApplication.activeWindow()
        screen = active.screen() if active else windows[0].screen()
        area = screen.availableGeometry()
        width = area.width() // len(windows)
        if width < max(w.minimumSizeHint().width() for w in windows):
            for window in windows:
                window.say('当前屏幕空间不足以左右平铺所有窗口；请减少窗口数量或移到其他屏幕。')
            return
        for index, window in enumerate(windows):
            window.showNormal()
            window.setGeometry(area.x()+index*width, area.y(),
                width if index<len(windows)-1 else area.width()-index*width, area.height())

    def next_window(self, current):
        if len(self.windows)>1:
            window = self.windows[(self.windows.index(current)+1) % len(self.windows)]
            window.showNormal()
            window.raise_()
            window.activateWindow()

    def remove(self, window):
        if window in self.windows:
            self.windows.remove(window)
        Q.QApplication.instance().removeEventFilter(window.shortcut_controller)
