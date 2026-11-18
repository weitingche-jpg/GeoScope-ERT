"""Check sidebar geometry under long results and a short viewport."""
import json
from PySide6 import QtCore, QtWidgets as Q


def run_checks(w,app,root):
    checks=[];failures=[]
    try:
        w.load_campus();w.set_workflow_step(2)
        w.case_info.setText('温纳观测 · 84 条\n516 个地下单元 · RRMS 14.57%\n计算时误差假设 3% · λ 20\n平地假设，定位与拟合质量分别检查\n请核对观测及地形条件后解释地下结果。')
        for width,height in [(1420,930),(1060,660)]:
            w.resize(width,height);app.processEvents()
            if hasattr(w,'refresh_sidebar_geometry'):w.refresh_sidebar_geometry()
            app.processEvents()
            page=w.step_stack.currentWidget()
            buttons=[b for b in page.findChildren(Q.QPushButton) if b.isVisible()]
            buttons.sort(key=lambda b:b.mapTo(page,QtCore.QPoint()).y())
            for button in buttons:
                assert button.height()>=40,(button.text(),button.height())
            for first,second in zip(buttons,buttons[1:]):
                bottom=first.mapTo(page,QtCore.QPoint()).y()+first.height()
                assert second.mapTo(page,QtCore.QPoint()).y()-bottom>=12,(first.text(),second.text())
            assert w.case_info.height()>=w.case_info.heightForWidth(w.case_info.width())
            assert page.height()>=max(b.mapTo(page,QtCore.QPoint()).y()+b.height() for b in buttons)
            if height==660:
                assert w.sidebar_scroll.verticalScrollBar().maximum()>0
                w.sidebar_scroll.ensureWidgetVisible(buttons[-1],0,12)
                app.processEvents()
                w.grab().save(str(root/'sidebar-layout-preview.png'))
            checks.append(f'{width}×{height}：按钮高度、间距、换行说明和滚动可达性通过')
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
    (root/'sidebar-layout-verification.json').write_text(json.dumps(dict(
        passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf8')
    w.dirty=False;w.close();app.exit(1 if failures else 0)
