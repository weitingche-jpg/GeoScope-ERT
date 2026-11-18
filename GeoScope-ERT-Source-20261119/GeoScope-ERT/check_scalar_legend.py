"""Check the legend in the real Qt scene and after resizing."""
import json
from PySide6 import QtWidgets as Q


def run_checks(w,app,root):
    checks=[];failures=[]
    try:
        w.demo();app.processEvents()
        for width,height in [(1420,930),(1060,720)]:
            w.resize(width,height);app.processEvents();w.scalar_legend.update_layout()
            controller=w.scalar_legend;bar=controller.bar;title=controller.title
            assert bar is not None and title is not None and title.GetVisibility()
            rect=[0,0,0,0];bar.GetScalarBarRect(rect,w.plotter.renderer)
            assert rect[2]>0 and rect[3]>0
            assert title.GetPosition()[1]>=rect[1]+rect[3]+8
            assert bar.GetTextPosition()==0 and bar.GetTitle()==''
            checks.append(f'{width}×{height}：标题在色条上方、刻度在下方且标题居中')
        w.resize(1420,930);app.processEvents();w.scalar_legend.update_layout()
        w.plotter.screenshot(str(root/'scalar-legend-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc())
    (root/'scalar-legend-verification.json').write_text(json.dumps(dict(
        passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf8')
    w.dirty=False;w.close();app.exit(1 if failures else 0)
