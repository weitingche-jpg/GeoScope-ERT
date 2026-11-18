"""Native mouse checks read text from rendered VTK label actors."""
import copy
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Q,QtTest
from core import write_json
from region_project import workspace_document


def run_checks(w,app,root):
    checks=[];failures=[]
    def settle():
        QtTest.QTest.qWait(140);app.processEvents();w.plotter.render();app.processEvents()
    def labels():
        result=[]
        for actor in list(w.plotter.actors.values()):
            try:
                if not actor.GetVisibility():continue
                mapper=actor.GetMapper()
                if mapper.GetClassName()!='vtkLabelPlacementMapper':continue
                data=mapper.GetInputDataObject(0,0);values=data.GetLabels()
                if values is not None:result.extend(str(values.GetValue(i)) for i in range(values.GetNumberOfValues()))
            except (AttributeError,TypeError,RuntimeError):pass
        return result
    def coords():return [t for t in labels() if all(k in t for k in ('X','Y','Z'))]
    def screen(xyz):
        xy,depth=w._screen(np.asarray(xyz)-w.terrain.origin);assert 0<=depth<=1
        return QtCore.QPoint(int(round(xy[0])),int(round(xy[1])))
    def hover(pos):
        event=QtGui.QMouseEvent(QtCore.QEvent.MouseMove,QtCore.QPointF(pos),
            QtCore.QPointF(w.plotter.interactor.mapToGlobal(pos)),QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,QtCore.Qt.NoModifier)
        Q.QApplication.sendEvent(w.plotter.interactor,event);settle()
    def key(code):QtTest.QTest.keyClick(w.plotter.interactor,code);settle()
    try:
        w.load_campus();cx=float(np.mean(w.terrain.x));cy=float(np.mean(w.terrain.y))
        w.points=[dict(id=f'P10{i+1}',x=cx+(i-1)*15,y=cy,z=0.,elevation_source='验收规划点') for i in range(3)]
        w.line=[[cx-15,cy],[cx+15,cy]]
        w.line_endpoint_overrides={'1':dict(x=cx+15,y=cy,z=5.,height_mode='manual')}
        w.points_follow_line=False;w.redraw()
        w.plotter.view_xy();w.plotter.reset_camera(bounds=(-45,45,-30,30,-3,8));settle()
        assert not coords() and not any('P10' in t for t in labels()),'默认仍有常驻点/端点标签'
        assert w.plotter.interactor.hasMouseTracking(),'未开启无按键鼠标跟踪'
        before=w.document_key();history=w.history.undo_count;selection=w.selected
        middle=screen([cx,cy,2.]);hover(middle)
        assert len(coords())==1 and 'P102' in coords()[0],dict(texts=coords(),shown=getattr(w,'_coord_hint_shown',None),hover=getattr(w,'_coord_hint_hover',None),target=w._coordinate_target_at(QtCore.QPointF(middle)),actors=list(w.plotter.actors))
        assert '0.00' in coords()[0] and w.selected==selection
        assert w.document_key()==before and w.history.undo_count==history
        w.grab().save(str(root/'coordinate-hover-preview.png'))
        hover(QtCore.QPoint(8,8));assert not coords(),'移开后悬停坐标未隐藏'
        checks.append('默认隐藏密集标签；Qt鼠标事件悬停仅显示一个真实XYZ，移开消失，不改数据或历史')
        QtTest.QTest.mouseClick(w.plotter.interactor,QtCore.Qt.LeftButton,pos=middle);settle()
        assert w.selected==('point',1) and 'P102' in coords()[0]
        Q.QApplication.sendEvent(w.plotter.interactor,QtCore.QEvent(QtCore.QEvent.Leave));settle()
        assert len(coords())==1 and 'P102' in coords()[0]
        key(QtCore.Qt.Key_Escape);assert not coords()
        checks.append('点击选中后坐标保留，鼠标离开不丢选择，Esc清除')
        end=screen([cx+15,cy,7.]);hover(end)
        assert len(coords())==1 and '5.00' in coords()[0],coords()
        hover(QtCore.QPoint(8,8));assert not coords()
        checks.append('手动高程端点悬停显示真实Z而非渲染偏移')
        base=workspace_document(w);other=copy.deepcopy(base)
        for point in other['points']:point['y']+=10
        other['line']=[[x,y+10] for x,y in other['line']];other['line_endpoint_overrides']={}
        w.survey_state.update(lines=[dict(id='L01',name='L01',visible=True,selected=True,workspace=base),
                                   dict(id='L02',name='L02',visible=True,selected=True,workspace=other)],active_id='L01')
        w.redraw();settle();before=w.document_key();history=w.history.undo_count
        hover(screen([cx,cy+10,0.]))
        assert len(coords())==1 and 'L02' in coords()[0] and 'P102' in coords()[0],coords()
        assert w.survey_state['active_id']=='L01' and w.document_key()==before and w.history.undo_count==history
        w.survey_state['lines'][1]['visible']=False;w.redraw();settle()
        hover(screen([cx,cy+10,0.]));assert not coords(),'隐藏线仍可悬停显示'
        hover(screen([cx,cy,0.]));assert len(coords())==1 and 'L01:P102' in coords()[0]
        checks.append('跨测线悬停显示正确编号但不切线，隐藏线排除')
        w.select_point(1);settle();assert len(coords())==1
        w.redraw();settle();assert not coords(),'重绘残留旧坐标'
        checks.append('选点表与地图共用一个提示，重绘清理过期坐标')
        w.grab().save(str(root/'coordinate-clean-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    write_json(root/'coordinate-hints-verification.json',dict(passed=not failures,checks=checks,failures=failures))
    w.clear_coordinate_hints()
    for window in list(w.manager.windows):window.dirty=False;window.close()
    app.exit(1 if failures else 0)
