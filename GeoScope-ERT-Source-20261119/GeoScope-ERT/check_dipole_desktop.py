"""Exercise actual dialogs, solver process, diagnostics and portable DD project."""
import copy
import json
import traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6 import QtCore, QtWidgets as Q


def run_checks(project, app, root):
    checks, failures, restored = [], [], []
    timer = QtCore.QTimer(project)
    host = project.hosts[0]
    elapsed = QtCore.QElapsedTimer()
    widget = None

    def finish():
        timer.stop()
        report = dict(passed=not failures, checks=checks, failures=failures)
        (root/'dipole-desktop-verification.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        for item in restored:
            item.shutdown()
        project.shutdown()
        app.exit(1 if failures else 0)

    try:
        source = root/'data/borth'
        if not source.exists():
            source = root/'output/literature_ert_GJI2021/author_original/2D_Borth'
        widget = project.open_module('ert', host)
        errors = []
        widget.error = lambda error: errors.append(str(error))
        from observation_workflow import ObservationDialog

        def accept_import():
            try:
                dialog = app.activeModalWidget()
                assert isinstance(dialog, ObservationDialog), type(dialog)
                assert dialog.packet['array'] == 'dipole-dipole'
                assert '偶极' in dialog.windowTitle()
                dialog.error.setValue(5)
                dialog.confirm.setChecked(True)
                dialog.grab().save(str(root/'dipole-import-preview.png'))
                dialog.accept()
            except Exception:
                failures.append(traceback.format_exc())
                if app.activeModalWidget():
                    app.activeModalWidget().reject()

        QtCore.QTimer.singleShot(200, accept_import)
        with patch.object(Q.QFileDialog, 'getOpenFileName', side_effect=[
                (str(source/'protocol.dat'), ''), (str(source/'electrodes.dat'), '')]):
            widget.import_observations()
        assert not errors and not failures, errors or failures
        assert len(widget.ert_packet['rows']) == 397
        assert widget.ert_packet['relative_error'] == .05
        checks.append('Actual two-file R2 import, DD preview, 397 rows, explicit 5% error confirmation')
        widget.run_inversion()
        assert widget.process is not None, errors
        elapsed.start()

        def poll():
            try:
                if widget.process is not None:
                    if elapsed.elapsed() > 180000:
                        raise RuntimeError('Borth GUI inversion exceeded 180 seconds')
                    return
                timer.stop()
                assert not errors, errors
                assert widget.case_arrays is not None
                np.testing.assert_array_equal(widget.case_arrays['source_row_indices'], np.arange(397))
                assert '偶极' in widget.case_info.text(), widget.case_info.text()
                from diagnostics_dialog import DiagnosticsDialog
                metadata = json.loads((widget.case_path/'metadata.json').read_text(encoding='utf-8'))
                details = json.loads((widget.case_path/'diagnostics.json').read_text(encoding='utf-8'))
                dialog = DiagnosticsDialog(widget, widget.case_arrays, metadata, details,
                                           widget.ert_packet, False)
                assert not dialog.messages, dialog.messages
                dialog.resize(1280, 800); dialog.show(); app.processEvents()
                dialog.tabs.setCurrentIndex(1); app.processEvents()
                dialog.grab().save(str(root/'dipole-diagnostics-preview.png')); dialog.close()
                checks.append('Actual QProcess pyGIMLi inversion and DD diagnostic plots without fallbacks')
                packet = copy.deepcopy(widget.ert_packet)
                before = packet['rows'][0]['rhoa']
                widget.ert_packet['rows'][0]['rhoa'] *= 1.001
                widget.redraw()
                assert widget.result_stale
                widget.ert_packet = packet; widget.redraw()
                assert not widget.result_stale
                checks.append('Observation edit marks result stale; restoring exact packet restores validity')
                assert before > 0
                target = root/'runs/Borth-dipole-comparison.geoscope'
                assert project.save(target)
                from method_shell import MethodProject
                reopened = MethodProject.load(target); restored.append(reopened)
                loaded = reopened.modules['ert']
                assert loaded.ert_packet['array'] == 'dipole-dipole'
                assert len(loaded.ert_packet['rows']) == 397
                assert not loaded.result_stale and len(loaded.case_arrays['observed']) == 397
                for key in ('source_sha256', 'source_electrodes_sha256'):
                    if key in packet:
                        assert loaded.ert_packet[key] == packet[key]
                checks.append('Portable project save/reopen preserves DD packet, sources and computed result')
                widget.view_section(); host.resize(1480, 920); host.show(); app.processEvents()
                host.grab().save(str(root/'dipole-result-preview.png'))
                (root/'dipole-latest-project.txt').write_text(str(target), encoding='utf-8')
                finish()
            except Exception:
                failures.append(traceback.format_exc()); finish()

        timer.timeout.connect(poll); timer.start(250)
        project._dipole_check_timer = timer
    except Exception:
        failures.append(traceback.format_exc()); finish()
