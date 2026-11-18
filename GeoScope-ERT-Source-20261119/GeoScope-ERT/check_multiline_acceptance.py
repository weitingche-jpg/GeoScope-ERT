"""Independent native acceptance of manual, same-scene survey imports."""
import copy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from core import write_json
from point_import_dialog import PointImportDialog
from survey_project import survey_signature


def run_checks(w, app, root):
    checks=[];failures=[]
    w.error=lambda error:print('UI ERROR: '+str(error),flush=True)
    def settle():
        for _ in range(3):app.processEvents()
    def button(words):
        candidates=[b for b in w.findChildren(Q.QPushButton) if all(s in b.text() for s in words)]
        assert candidates,words
        return candidates[0]
    def accept_points(dialog):
        dialog.mode.setCurrentIndex(2)
        dialog.confirm_coordinates.setChecked(True)
        dialog.connect_line.setChecked(True)
        dialog.accept()
        assert len(dialog.imported_points)==4
        return Q.QDialog.Accepted
    def import_new(path,accept=True):
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(path),'')), \
             patch.object(PointImportDialog,'exec',accept_points if accept else lambda dialog:Q.QDialog.Rejected), \
             patch.object(Q.QMessageBox,'question',return_value=Q.QMessageBox.Yes):
            button(['新增','导入']).click()
        settle()
    try:
        print('CHECK: load terrain',flush=True)
        w.load_campus();folder=root/'.cache'/'multiline-manual-inputs';folder.mkdir(parents=True,exist_ok=True)
        paths=[]
        cx=float(np.mean(w.terrain.x));cy=float(np.mean(w.terrain.y))
        for j in range(3):
            path=folder/f'L{j+1:02}.csv'
            path.write_text('id,x,y,z\n'+''.join(f'E{i+1:02},{cx-3+i*2},{cy+j*5},0\n' for i in range(4)),encoding='utf-8')
            paths.append(path)
        dialog=PointImportDialog(w,paths[0]);accept_points(dialog);w.apply_point_import(dialog);settle()
        print('CHECK: initial line imported',flush=True)
        original=copy.deepcopy(w.points)
        import_new(paths[1]);assert len(w.survey_state['lines'])==2
        print('CHECK: second line imported',flush=True)
        assert w.survey_state['lines'][0]['workspace']['points']==original,'进入多线时丢失原单线'
        import_new(paths[2]);assert len(w.survey_state['lines'])==3
        assert {sid for _,sid in w.survey_line_actors}=={l['id'] for l in w.survey_state['lines']}
        assert all(len(l['workspace']['line'])==4 for l in w.survey_state['lines'])
        checks.append('实际CSV逐次导入三条多顶点测线，保全原单线，全部同图可拾取')
        w.undo_edit();settle();assert len(w.survey_state['lines'])==2
        w.redo_edit();settle();assert len(w.survey_state['lines'])==3
        checks.append('新增测线和导入是一次撤销操作，重做恢复整条测线')
        before=copy.deepcopy(w.survey_state);import_new(paths[0],False)
        assert w.survey_state==before,'取消新增导入改变项目'
        w.activate_survey_line(w.survey_state['lines'][0]['id']);settle()
        camera=np.asarray(list(w.plotter.camera_position),float).copy()
        w.activate_survey_line(w.survey_state['lines'][2]['id']);settle()
        np.testing.assert_allclose(np.asarray(list(w.plotter.camera_position),float),camera)
        checks.append('取消导入不留空测线；切换测线保留相机及另外两线数据')
        w.focus_survey_view();settle()
        first=w.survey_state['lines'][0]
        xy=np.mean(np.asarray(first['workspace']['line'])[1:3],axis=0)
        world=np.r_[xy,w.terrain.height([xy])[0]+.6]-w.terrain.origin
        screen,depth=w._screen(world)
        assert 0<=depth<=1
        w.query_at(QtCore.QPointF(float(screen[0]),float(screen[1])));settle()
        assert w.survey_state['active_id']==first['id'],'实际地图点击没有切换到目标测线'
        checks.append('真实VTK地图拾取切换到其他测线，其他线仍保留')
        table=w.survey_table;header=[table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
        column=header.index('显示');state=copy.deepcopy(w.survey_state);sig=survey_signature(state)
        table.item(0,column).setCheckState(QtCore.Qt.Unchecked);settle()
        assert w.survey_state['lines'][0]['selected']==state['lines'][0]['selected']
        assert survey_signature(w.survey_state)==sig
        assert w.survey_state['lines'][0]['id'] not in {sid for _,sid in w.survey_line_actors}
        table.item(0,column).setCheckState(QtCore.Qt.Checked);settle()
        checks.append('显示开关独立于参与反演与数值签名，隐藏后恢复三线')
        target=w.survey_state['active_id'];other=[copy.deepcopy(l) for l in w.survey_state['lines'] if l['id']!=target]
        dialog=PointImportDialog(w,paths[2]);accept_points(dialog)
        dialog.imported_points[1]['x']+=.2
        w.apply_point_import(dialog);settle()
        assert [l for l in w.survey_state['lines'] if l['id']!=target]==other
        w.undo_edit();settle()
        assert [l for l in w.survey_state['lines'] if l['id']!=target]==other
        checks.append('更新当前线及撤销不覆盖其他线')
        before=copy.deepcopy(w.survey_state)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=(str(paths[0]),'')), \
             patch.object(PointImportDialog,'exec',accept_points), \
             patch.object(Q.QMessageBox,'question',return_value=Q.QMessageBox.No) as ask:
            w.import_points()
        settle();assert w.survey_state==before
        assert w.active_survey_line()['name'] in ask.call_args.args[2]
        checks.append('替换确认明确目标线；拒绝更新不改变数据')
        project=root/'.cache'/'multiline-manual.geoscope.json'
        w.save_project_to(project);saved=copy.deepcopy(w.survey_state)
        w.load_project_from(project);settle();assert w.survey_state==saved
        w.survey_dock.show();w.focus_survey_view();settle()
        w.grab().save(str(root/'multiline-interaction-preview.png'))
        checks.append('保存重开保留三条测线、各自测点及显示设置')
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    write_json(root/'multiline-interaction-verification.json',dict(passed=not failures,checks=checks,failures=failures))
    for window in list(w.manager.windows):window.dirty=False;window.close()
    app.exit(1 if failures else 0)
