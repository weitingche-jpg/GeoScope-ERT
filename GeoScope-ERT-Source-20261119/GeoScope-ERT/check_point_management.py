"""Qt integration checks for Office preview, real table edits, and persistence."""
import copy
import json
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore,QtTest,QtWidgets as Q
from point_samples import create_samples
from point_import_dialog import PointImportDialog


def run_checks(w,app,root):
    checks=[];failures=[]
    def key(code,mods=QtCore.Qt.ControlModifier):
        w.plotter.interactor.setFocus();QtTest.QTest.keyClick(w.plotter.interactor,code,mods);app.processEvents()
    try:
        w.load_campus();samples=create_samples(root/'data'/'point-samples')
        for suffix in ('csv','xlsx','docx'):
            dialog=PointImportDialog(w,samples/f'校园测点.{suffix}')
            dialog.show();app.processEvents()
            assert dialog.header.value()==2 and len(dialog.records)==2 and not dialog.ok.isEnabled()
            dialog.confirm_coordinates.setChecked(True)
            assert dialog.ok.isEnabled()
            assert not w.points,'预览阶段修改了项目'
            dialog.mode.setCurrentIndex(2);dialog.accept();w.apply_point_import(dialog)
            assert len(w.points)==2 and w.points[0]['id']=='T01' and w.line==[]
            assert w.points[0]['source_row']==3
            key(QtCore.Qt.Key_Z);assert w.points==[]
            dialog.deleteLater()
        checks.append('CSV/Excel/Word 实际提取、识别标题行、确认坐标、导入预览、来源行与一步撤销')
        dialog=PointImportDialog(w,samples/'校园测点.xlsx')
        dialog.confirm_coordinates.setChecked(True);dialog.accept();w.apply_point_import(dialog)
        duplicate=PointImportDialog(w,samples/'校园测点.csv')
        duplicate.confirm_coordinates.setChecked(True)
        assert not duplicate.ok.isEnabled()
        duplicate.mode.setCurrentIndex(1);assert duplicate.ok.isEnabled()
        duplicate.reject();assert len(w.points)==2
        duplicate.deleteLater()
        checks.append('重复编号禁止追加、更新模式允许，取消不改变布局')
        bad=samples/'错误行.csv';bad.write_text('id,x,y,z\nBAD,99999,200,0\nGOOD,300,200,\n',encoding='utf-8')
        review=PointImportDialog(w,bad);review.confirm_coordinates.setChecked(True)
        assert not review.ok.isEnabled()
        review.preview.item(0,0).setCheckState(QtCore.Qt.Unchecked)
        assert review.ok.isEnabled();review.accept();w.apply_point_import(review)
        assert len(w.points)==3 and w.points[-1]['id']=='GOOD'
        checks.append('越界错误阻止导入；明确排除错误行后导入有效行')
        noheader=samples/'无表头.csv';noheader.write_text('H01,400,300\nH02,450,300\n',encoding='utf-8')
        manual=PointImportDialog(w,noheader)
        assert manual.header.value()==0
        for field,index in [('id',1),('x',2),('y',3)]:manual.columns[field].setCurrentIndex(index)
        manual.confirm_coordinates.setChecked(True);assert manual.ok.isEnabled()
        assert len(manual.records)==2 and manual.records[0]['point']['id']=='H01'
        manual.reject()
        checks.append('无表头文件人工映射，首个测点完整保留')
        precise=copy.deepcopy(w.points[0]);precise['x']=260.123456789
        w.commit_point(precise,0)
        assert w.table.item(0,1).text()=='260.123456789'
        before=copy.deepcopy(w.points);camera=w.plotter.camera_position
        # Native Qt editor, actual keyboard replacement and Enter commit.
        item=w.table.item(0,1);w.table.setCurrentItem(item);w.table.editItem(item);app.processEvents()
        editor=next((e for e in w.table.findChildren(Q.QLineEdit) if e.isVisible()),None)
        assert isinstance(editor,Q.QLineEdit),'原生表格编辑器未创建'
        QtTest.QTest.keyClick(editor,QtCore.Qt.Key_A,QtCore.Qt.ControlModifier)
        QtTest.QTest.keyClicks(editor,'260');QtTest.QTest.keyClick(editor,QtCore.Qt.Key_Return);app.processEvents()
        assert w.points[0]['x']==260 and w.selected==('point',0)
        selection=w.plotter.renderer.actors['selection'].mapper.dataset.points
        np.testing.assert_allclose(selection[0]+w.terrain.origin,[260,100,2],atol=1e-3)
        np.testing.assert_allclose(np.array(list(w.plotter.camera_position)),np.array(list(camera)))
        key(QtCore.Qt.Key_Z);assert w.points==before
        key(QtCore.Qt.Key_Y);assert w.points[0]['x']==260
        checks.append('真实单元格编辑及Enter提交、地图高亮位置移动、视角保持、撤销重做')
        old=copy.deepcopy(w.points)
        with patch.object(Q.QMessageBox,'warning') as warning:
            w.table.item(0,1).setText('nan')
            assert warning.called and w.points==old
            w.table.item(0,0).setText('T02')
            assert w.points==old
        checks.append('非有限坐标与重复编号被拒绝并恢复原数据')
        def fill_new():
            modal=Q.QApplication.activeModalWidget()
            edits=modal.findChildren(Q.QLineEdit)
            for editor,value in zip(edits,['NEW','450','300','0']):editor.setText(value)
            modal.findChild(Q.QDialogButtonBox).button(Q.QDialogButtonBox.Ok).click()
        QtCore.QTimer.singleShot(100,fill_new);w.edit_point_dialog()
        assert w.points[-1]['id']=='NEW'
        checks.append('新增测点对话框输入坐标，贴地创建并选中')
        saved=root/'.cache'/'point-management-save.json';w.save_project_to(saved)
        expected=copy.deepcopy(w.points);w.load_project_from(saved);assert w.points==expected
        checks.append('保存重开保留编辑后坐标、原始来源与校园图层')
        dialog=PointImportDialog(w,samples/'校园测点.xlsx');dialog.show();app.processEvents()
        dialog.grab().save(str(root/'point-import-preview.png'));dialog.reject()
        w.grab().save(str(root/'point-management-preview.png'))
    except Exception:
        import traceback
        failures.append(traceback.format_exc());print(failures[-1],flush=True)
    (root/'point-management-verification.json').write_text(json.dumps(dict(passed=not failures,
        checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    w.dirty=False;w.close();app.exit(1 if failures else 0)
