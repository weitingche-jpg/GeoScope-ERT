"""Source/frozen UI acceptance of default automatic ERT array recognition."""
import copy
import json
import traceback
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtCore, QtWidgets as Q


def run_checks(project,app,root):
    checks=[];failures=[];dialogs=[]
    folder=root/'runs/auto-array-check';folder.mkdir(parents=True,exist_ok=True)
    try:
        from observation_import_dialog import ObservationImportDialog
        from ert_protocol import read_r2_protocol
        widget=project.open_module('ert',project.hosts[0])
        fixtures={
            'wenner':'A,B,M,N,rhoa\n1,4,2,3,100\n2,5,3,4,110\n',
            'dipole-dipole':'A,B,M,N,rhoa\n1,2,3,4,100\n1,2,4,5,110\n',
            'mixed':'A,B,M,N,rhoa\n1,4,2,3,100\n1,2,3,4,110\n',
            'invalid':'A,B,M,N,rhoa\n1,1,2,3,100\n',
        }
        for name,text in fixtures.items():
            path=folder/(name+'.csv');path.write_text(text,encoding='utf-8')
            dialog=ObservationImportDialog(widget,path);dialogs.append(dialog)
            assert dialog.array.currentData()=='auto'
            dialog.spacing.setValue(2.);dialog.confirm.setChecked(True)
            if name in ('wenner','dipole-dipole'):
                assert dialog.ok.isEnabled(),dialog.summary.text()
                dialog.show();app.processEvents()
                dialog.grab().save(str(root/f'auto-array-{name}-preview.png'))
                dialog.accept();assert dialog.packet['array']==name
                assert dialog.packet.get('array_detection')
                checks.append('Default automatic recognition and packet provenance: '+name)
            elif name=='mixed':
                assert not dialog.ok.isEnabled()
                assert dialog.preview.rowCount()==2
                dialog.show();app.processEvents()
                dialog.grab().save(str(root/'auto-array-mixed-preview.png'))
                dialog.preview.item(1,0).setCheckState(QtCore.Qt.Unchecked)
                assert dialog.ok.isEnabled(),dialog.summary.text()
                dialog.accept();assert dialog.packet['array']=='wenner'
                assert len(dialog.packet['rows'])==1
                checks.append('Mixed array blocked; explicit exclusion recomputes recognition')
            else:
                assert not dialog.ok.isEnabled()
                dialog.reject();checks.append('Unsupported geometry blocked without guessing')
            app.processEvents();dialog.close()
        # R2 must also identify Wenner, not assume all six-column protocols are DD.
        (folder/'electrodes.dat').write_text('0 0 0\n2 0 0\n4 0 0\n6 0 0\n',encoding='utf-8')
        (folder/'protocol.dat').write_text('1\n1 2 3 1 4 10\n',encoding='utf-8')
        packet=read_r2_protocol(folder/'protocol.dat',folder/'electrodes.dat')
        assert packet['array']=='wenner'
        widget.open_observation_packet(packet)
        before=copy.deepcopy(widget.ert_packet);messages=[]
        original_say=widget.say;widget.say=lambda text:messages.append(text)
        with patch.object(Q.QFileDialog,'getOpenFileName',side_effect=[
                (str(folder/'protocol.dat'),''),('','')]):
            widget.import_observations()
        widget.say=original_say
        assert widget.ert_packet==before
        assert any('electrodes.dat' in message for message in messages),messages
        checks.append('Missing R2 electrode file cancels atomically and explains required file')
        source=root/'data/borth'
        if not source.is_dir():source=root/'output/literature_ert_GJI2021/author_original/2D_Borth'
        borth=read_r2_protocol(source/'protocol.dat',source/'electrodes.dat')
        assert borth['array']=='dipole-dipole' and len(borth['rows'])==397
        assert borth['electrodes']==48 and borth['spacing']==2.
        checks.append('Published Borth geometry still recognized as DD: 397 observations, 48 electrodes, 2m')
    except Exception:
        failures.append(traceback.format_exc())
    finally:
        for dialog in dialogs:dialog.close()
        app.processEvents()
        (root/'auto-array-verification.json').write_text(json.dumps(
            dict(passed=not failures,checks=checks,failures=failures),ensure_ascii=False,indent=2),encoding='utf-8')
        project.shutdown();app.exit(1 if failures else 0)
