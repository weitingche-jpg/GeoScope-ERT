"""Exercise stable tab geometry rather than testing individual style strings."""
import json
from PySide6 import QtCore,QtWidgets as Q


def run_checks(w,app,root):
    checks=[];failures=[]
    try:
        w.load_campus();w.set_workflow_step(1)
        tables=[w.table,w.observation_table,w.check_table,w.response_table]
        for size in [(1420,930),(1060,720)]:
            w.resize(*size);app.processEvents()
            scene=None;tab_bounds=None;table_bounds=None;bar_rects=None
            for index in [0,1,2,3,0,3,1,0]:
                w.data_tabs.setCurrentIndex(index);app.processEvents()
                bar=w.data_tabs.tabBar()
                rects=[bar.tabRect(i) for i in range(4)]
                text_rects=[]
                for i in range(4):
                    option=Q.QStyleOptionTab();bar.initStyleOption(option,i)
                    text_rects.append(bar.style().subElementRect(Q.QStyle.SE_TabBarTabText,option,bar))
                for first,second in zip(text_rects,text_rects[1:]):
                    assert second.left()-first.right()-1>=28,'页签文字之间缺少留白'
                current_scene=w.views.geometry()
                current_bounds=w.data_tabs.geometry()
                table=tables[index]
                current_table=(table.mapTo(w.data_tabs,QtCore.QPoint()).y(),table.height())
                if scene is None:
                    scene=QtCore.QRect(current_scene);tab_bounds=QtCore.QRect(current_bounds)
                    table_bounds=current_table;bar_rects=rects
                else:
                    assert current_scene==scene,('视图区随页签改变',index,current_scene,scene)
                    assert current_bounds==tab_bounds,('数据区随页签改变',index,current_bounds,tab_bounds)
                    assert current_table==table_bounds,('表格起始位置/高度不同',index,current_table,table_bounds)
                    assert rects==bar_rects,'页签选中时位置发生变化'
            checks.append(f'{size[0]}×{size[1]}：往返切换四页，视图/页签/表格几何保持稳定')
        w.data_tabs.setCurrentIndex(0);app.processEvents()
        w.data_tabs.grab().save(str(root/'data-tabs-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
    (root/'data-tabs-verification.json').write_text(json.dumps(dict(
        passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf8')
    w.dirty=False;w.close();app.exit(1 if failures else 0)
