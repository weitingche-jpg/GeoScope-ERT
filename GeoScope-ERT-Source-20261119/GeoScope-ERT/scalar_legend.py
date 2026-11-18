"""Keep a VTK legend title separate from its horizontal tick labels."""
from PySide6 import QtCore


def place_legend(plotter,bar,title,scale=1.):
    """Lay out in rendered pixels, so HiDPI and viewport changes stay safe."""
    if getattr(plotter,'_closed',False):return None
    _,height=plotter.render_window.GetSize()
    if height<=0:return None
    bar.SetHeight(min(.40,max(.08,48.*scale/height)))
    bar.SetPosition(.14,max(.03,12.*scale/height))
    plotter.render()
    rect=[0,0,0,0];bar.GetScalarBarRect(rect,plotter.renderer)
    if rect[2]<=0 or rect[3]<=0:return None
    bounds=[0.,0.,0.,0.];title.GetBoundingBox(plotter.renderer,bounds)
    width=bounds[1]-bounds[0]
    gap=max(8.,6.*scale)
    title.SetPosition(rect[0]+rect[2]/2-width/2,rect[1]+rect[3]+gap)
    title.SetVisibility(True)
    plotter.render()
    return rect


class ScalarLegendController(QtCore.QObject):
    def __init__(self,plotter,parent=None):
        super().__init__(parent)
        self.plotter=plotter;self.bar=None;self.title=None;self.pending=False
        plotter.interactor.installEventFilter(self)

    def clear(self):
        self.bar=None;self.title=None

    def attach(self,bar):
        self.bar=bar
        bar.SetTitle('');bar.SetTextPositionToPrecedeScalarBar();bar.SetTextPad(6)
        self.title=self.plotter.add_text('Resistivity (ohm m)',position=(0,0),font_size=9,
            color='#dce8f7',name='resistivity-legend-title',render=False)
        self.title.GetTextProperty().SetJustificationToLeft()
        self.title.SetVisibility(False)
        self.schedule()

    def schedule(self):
        if not self.pending:
            self.pending=True;QtCore.QTimer.singleShot(0,self.update_layout)

    def update_layout(self):
        self.pending=False
        if self.bar is None or self.title is None:return
        scale=self.plotter.interactor.devicePixelRatioF()
        place_legend(self.plotter,self.bar,self.title,scale)

    def eventFilter(self,watched,event):
        if event.type() in (QtCore.QEvent.Resize,QtCore.QEvent.Show):self.schedule()
        return False
