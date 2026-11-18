"""Native desktop proof: real single-line archive + explicit multi-line simulation."""
import copy
import json
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from core import write_json
from observed_ert import read_observations
from calibration import make_proposal
from region_project import capture_current,section_from_project,empty_region
from region_model import build_region,region_signature
from region_dialog import RegionDialog


def run_checks(w,app,root):
    checks=[];failures=[];timer=QtCore.QTimer(w);clock=QtCore.QElapsedTimer()
    def settle(window):
        app.processEvents();app.processEvents();window.scalar_legend.update_layout();app.processEvents()
    def finish():
        timer.stop();write_json(root/'region-verification.json',dict(passed=not failures,checks=checks,failures=failures))
        for window in list(w.manager.windows):window.dirty=False;window.close()
        app.exit(1 if failures else 0)
    def fail():
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True);finish()
    try:
        assert w.terrain is None and not w.region_state['sections']
        w.load_campus();source=root/'data'/'field-experiments'/'26102812708.csv'
        if not source.exists():source=root/'电法实验数据'/'解压数据'/'26102812708'/'26102812708.csv'
        w.open_observation_packet(read_observations(source))
        w.apply_calibration_proposal(make_proposal(w.ert_packet,[240.,100.,0.],90.,w.terrain))
        w.run_inversion();assert w.process is not None,w.log.toPlainText();clock.start()
        def after_worker():
            if w.process is not None:
                if clock.elapsed()>90000:
                    timer.stop();w.process.kill();w.process.waitForFinished(3000)
                    failures.append('真实单线反演超时');finish()
                return
            timer.stop()
            try:
                assert w.model is not None and not w.result_stale,w.log.toPlainText()
                snapshot=capture_current(w,'实测 R01');assert snapshot['kind']=='imported_observations'
                np.testing.assert_allclose(np.linalg.norm(np.subtract(*np.asarray(snapshot['endpoints'])[::-1])),46.)
                state=empty_region();state['sections']=[snapshot]
                from region_model import default_settings
                state['settings']=default_settings([snapshot]);w.apply_region_state(state)
                try:build_region([snapshot],state['settings']);raise AssertionError('单线不应造体')
                except ValueError:pass
                saved=root/'.cache'/'region-real-line.geoscope.json';w.save_project_to(saved)
                imported=section_from_project(saved,w.terrain)
                np.testing.assert_allclose(imported['nodes'],snapshot['nodes'])
                assert len(imported['rho'])==len(snapshot['rho'])
                checks.append('第一组24电极2米、46米真实反演剖面按原尺寸收录，项目导入几何一致；单线拒绝造体')
                demo=w.load_region_demo();settle(demo)
                assert not demo.section_view.isVisible() and demo.plotter.interactor.width()>=demo.views.width()*.9
                assert len(demo.region_state['sections'])==3 and len(demo.region_actors)>=5
                result=demo.region_cache;assert set(np.unique(result['classification']))=={0,1,2}
                assert np.isnan(result['rho'][result['classification']==0]).all()
                assert all(s['kind']=='synthetic' for s in demo.region_state['sections'])
                # An uncollected current section must use the same scale as the collection.
                demo.model=demo.region_meshes[0].copy();demo.model.cell_data['resistivity']*=10
                demo.redraw()
                expected=max(np.asarray(demo.model.cell_data['resistivity']).max(),max(max(s['rho']) for s in demo.region_state['sections']))
                assert np.isclose(demo.model_actor.mapper.scalar_range[1],expected)
                for actor,mesh in demo.region_pick_entries:
                    if 'resistivity' in mesh.cell_data:assert np.isclose(actor.mapper.scalar_range[1],expected)
                demo.model=None;demo.redraw();demo.focus_region_view();settle(demo)
                screen,_=demo._screen(np.array([244.,110.,-6.])-demo.terrain.origin)
                demo.query_at(QtCore.QPointF(float(screen[0]),float(screen[1])))
                assert '区域查询' in demo.inspector.text(),demo.inspector.text()
                demo.grab().save(str(root/'region-workbench-preview.png'));demo.plotter.screenshot(str(root/'region-volume-preview.png'))
                checks.append('三条解析模拟剖面同时显示，区域体包含剖面邻近/线间插值/缺数据三类，缺数据值为NaN')
                dialog=RegionDialog(demo);dialog.show();app.processEvents()
                dialog.grab().save(str(root/'region-management-preview.png'))
                for button in dialog.findChildren(Q.QPushButton):
                    assert button.width()>=button.minimumSizeHint().width()-2,(button.text(),button.width(),button.minimumSizeHint().width())
                original=copy.deepcopy(demo.region_state);dialog.table.selectRow(1);dialog.remove_selected();dialog.reject()
                assert demo.region_state==original
                checks.append('区域窗口按钮完整可读；待应用移除后取消不改变项目')
                changed=copy.deepcopy(original);changed['settings']['max_gap']=5.
                demo.apply_region_state(changed);assert '参数已改变' in demo.region_status.text()
                assert len(demo.region_actors)==3
                demo.undo_edit();assert demo.region_state==original and len(demo.region_actors)>=5
                demo.redo_edit();assert len(demo.region_actors)==3
                demo.undo_edit();assert demo.region_cache['signature']==region_signature(demo.region_state['sections'],demo.region_state['settings'])
                checks.append('参数改变隐藏旧体；区域修改可整次撤销/重做，恢复原输入重新显示')
                categorical=copy.deepcopy(demo.region_state);categorical['display']['mode']='classification'
                demo.apply_region_state(categorical);settle(demo);demo.plotter.screenshot(str(root/'region-range-preview.png'))
                categorical['display']['mode']='slice';categorical['display']['slice_z']=-6
                demo.apply_region_state(categorical);settle(demo);demo.plotter.screenshot(str(root/'region-slice-preview.png'))
                exported=root/'.cache'/'region-model.npz'
                with patch.object(Q.QFileDialog,'getSaveFileName',return_value=(str(exported),'')):demo.export_region_model()
                with np.load(exported,allow_pickle=False) as arrays:
                    np.testing.assert_array_equal(arrays['classification'],demo.region_cache['classification'])
                    assert json.loads(str(arrays['metadata_json']))['classification']['0'].startswith('missing')
                categorical['display']['mode']='resistivity';demo.apply_region_state(categorical)
                saved=root/'.cache'/'region-demo.geoscope.json';demo.save_project_to(saved)
                before=copy.deepcopy(demo.region_state);demo.load_project_from(saved)
                assert demo.region_state==before and demo.region_cache is not None
                bad=copy.deepcopy(json.loads(saved.read_text(encoding='utf8')));bad['region']['sections'][0]['crs']='wrong'
                badfile=root/'.cache'/'region-bad.geoscope.json';write_json(badfile,bad)
                try:demo.load_project_from(badfile);raise AssertionError('错误CRS不应打开')
                except ValueError:pass
                assert demo.region_state==before
                checks.append('数据范围/水平切片/NPZ导出可用；保存重开重建一致；错误坐标项目回滚原区域')
                child=demo.new_region_workbench();assert child.terrain.source==demo.terrain.source and not child.points and not child.region_state['sections']
                assert demo.region_state==before and len(w.region_state['sections'])==1
                child.dirty=False;child.close()
                checks.append('新测线在独立窗口建立，原区域和真实测线保留，模拟示例不覆盖实测项目')
                demo.focus_region_view();settle(demo);demo.grab().save(str(root/'region-workbench-preview.png'))
                finish()
            except Exception:fail()
        timer.timeout.connect(after_worker);timer.start(150)
    except Exception:fail()
