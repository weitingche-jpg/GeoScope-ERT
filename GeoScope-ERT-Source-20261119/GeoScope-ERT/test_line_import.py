"""Local single-action survey line import: geometry, persistence and render gates."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtWidgets as Q

from line_import import (LOCAL_ELEVATION_LABEL, is_local_import, mark_line_import,
                         prepare_line_workspace)
from observed_ert import read_observations
from ert_protocol import read_r2_protocol
from survey_project import empty_survey, validate_survey
from survey_workflow import SurveyWorkflow

ROOT = Path(__file__).parent
BORTH = ROOT / 'output/literature_ert_GJI2021/author_original/2D_Borth'
PROTOCOL = BORTH / 'protocol.dat'
ELECTRODES = BORTH / 'electrodes.dat'


def borth_packet():
    return read_r2_protocol(PROTOCOL, ELECTRODES)


def wenner_packet(electrodes=4):
    return dict(schema=1, array='wenner', electrodes=electrodes, spacing=2.,
                relative_error=.03, lam=20., max_iter=12,
                rows=[dict(abmn=[0, 3, 1, 2], rhoa=100.)])


def close_window(testcase, window):
    window.dirty = False
    window.close()
    testcase.app.processEvents()


class LineImportGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    @unittest.skipUnless(PROTOCOL.is_file() and ELECTRODES.is_file(),
                         'Borth R2 source files not present')
    def test_borth_builds_48_input_electrodes_397_observations_and_94m(self):
        packet = borth_packet()
        source_xyz = [list(row) for row in packet['electrode_coordinates']]
        marked = mark_line_import(packet)
        self.assertTrue(is_local_import(marked))
        self.assertEqual(marked['line_import']['frame'], 'local')
        self.assertEqual(marked['line_import']['source_xyz'], source_xyz)
        self.assertEqual(marked['line_import']['source_sha256_protocol'],
                         packet['source_sha256_protocol'])
        self.assertEqual(marked['line_import']['source_sha256_electrodes'],
                         packet['source_sha256_electrodes'])
        workspace = prepare_line_workspace(marked)
        self.assertIsNone(workspace['terrain'])
        self.assertTrue(workspace['local_frame'])
        self.assertEqual(len(workspace['points']), 48)
        self.assertEqual(len(workspace['points']), marked['electrodes'])
        self.assertEqual(workspace['points'][0]['x'], 0.)
        self.assertEqual(workspace['points'][-1]['x'], 94.)
        self.assertEqual(workspace['points'][0]['id'], 'E01')
        self.assertEqual(workspace['points'][-1]['id'], 'E48')
        self.assertTrue(all(point['y'] == 0. and point['z'] == 0.
                            for point in workspace['points']))
        self.assertTrue(all(point['elevation_source'] == LOCAL_ELEVATION_LABEL
                            for point in workspace['points']))
        self.assertEqual(workspace['line'], [[0., 0.], [94., 0.]])
        self.assertEqual(len(workspace['ert_packet']['rows']), 397)
        self.assertEqual(workspace['ert_packet']['line_import']['source_xyz'], source_xyz)
        self.assertTrue(is_local_import(workspace['ert_packet']))

    def test_prepare_uses_exactly_the_input_count_not_a_default_24(self):
        packet = wenner_packet(5)
        workspace = prepare_line_workspace(packet)
        self.assertEqual(len(workspace['points']), 5)
        self.assertEqual(workspace['line'], [[0., 0.], [8., 0.]])
        self.assertEqual(workspace['points'][-1]['x'], 8.)
        self.assertTrue(is_local_import(workspace['ert_packet']))
        self.assertFalse(is_local_import(packet), 'original packet must not be mutated')

    def test_invalid_packet_is_rejected_before_any_workspace_is_returned(self):
        packet = wenner_packet(4)
        packet['rows'] = []
        with self.assertRaises(ValueError):
            prepare_line_workspace(packet)

    def test_validate_survey_without_terrain_allows_only_explicit_local_import(self):
        workspace = prepare_line_workspace(wenner_packet(4))
        state = empty_survey()
        state['lines'] = [dict(id='L01', name='L01', selected=True, visible=True,
                               workspace=workspace)]
        state['active_id'] = 'L01'
        restored = validate_survey(state, None)
        self.assertEqual(len(restored['lines']), 1)
        self.assertTrue(is_local_import(restored['lines'][0]['workspace']['ert_packet']))

        global_workspace = copy.deepcopy(workspace)
        global_workspace['terrain'] = 'old-terrain'
        global_workspace['ert_packet'] = wenner_packet(4)
        global_state = empty_survey()
        global_state['lines'] = [dict(id='L01', name='L01', selected=True, visible=True,
                                      workspace=global_workspace)]
        global_state['active_id'] = 'L01'
        with self.assertRaises(ValueError):
            validate_survey(global_state, None)

    def test_local_acquisition_is_absent_from_dem_until_registration(self):
        old_points = [dict(id=f'E{i+1:02}', x=float(i*2), y=10., z=0.) for i in range(4)]
        local_workspace = prepare_line_workspace(wenner_packet(4))
        lines = [
            dict(id='L01', name='L01', selected=True, visible=True,
                 workspace=dict(line=[[0., 10.], [6., 10.]], points=old_points,
                                line_endpoint_overrides={}, ert_packet=None)),
            dict(id='L02', name='L02', selected=True, visible=True,
                 workspace=local_workspace),
        ]
        owner = SimpleNamespace(
            survey_state=dict(lines=lines, active_id='L01', view='sections', hypothesis=False),
            survey_active=lambda: True,
            sync_survey_line=lambda: None,
            survey_volume_current=lambda: False,
            terrain=SimpleNamespace(origin=np.zeros(3),
                                    height=lambda xy: np.zeros(len(xy))),
            plotter=Mock(), volume_metadata=None,
            region_status=Mock(), scene_badge=Mock(), result_status=Mock())
        SurveyWorkflow.draw_survey_layers(owner)
        drawn = {item[1] for item in owner.survey_line_actors}
        self.assertEqual(drawn, {'L01'})
        self.assertNotIn('L02', {item[1] for item in owner.survey_electrode_actors})

        # Registration is represented by binding.mode=='registered'; the same
        # workspace then becomes drawable and is_local_import is false.
        local_workspace['ert_packet']['binding'] = {'mode': 'registered'}
        self.assertFalse(is_local_import(local_workspace['ert_packet']))
        SurveyWorkflow.draw_survey_layers(owner)
        drawn = {item[1] for item in owner.survey_line_actors}
        self.assertEqual(drawn, {'L01', 'L02'})


class LineImportWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def window(self):
        from app import Window
        window = Window(demo=False)
        self.addCleanup(close_window, self, window)
        return window

    @unittest.skipUnless(PROTOCOL.is_file() and ELECTRODES.is_file(),
                         'Borth R2 source files not present')
    def test_apply_without_terrain_shows_table_preview_and_saves_reopens(self):
        window = self.window()
        window.apply_imported_line(borth_packet())
        self.assertIsNone(window.terrain)
        self.assertEqual(len(window.points), 48)
        self.assertEqual(len(window.survey_state['lines']), 1)
        self.assertTrue(window.section_view.acquisition_mode)
        self.assertIn('未配准', window.section_view.heading.text())
        self.assertIn('非反演', window.section_view.heading.text())
        self.assertEqual(window.table.rowCount(), 48)
        self.assertEqual(window.table.horizontalHeaderItem(0).text(), '电极')
        self.assertEqual(window.table.item(0, 1).text(), '0.0')
        self.assertEqual(window.table.item(47, 1).text(), '94.0')
        self.assertEqual(window.table.item(47, 4).text(), LOCAL_ELEVATION_LABEL)
        self.assertTrue(window.points[0]['local_frame'])

        with tempfile.TemporaryDirectory(dir='.cache') as folder:
            path = Path(folder) / 'local-line.geoscope.json'
            window.save_project_to(path)
            self.assertTrue(path.is_file())
            restored = self.window()
            restored.load_project_from(path)
            self.assertIsNone(restored.terrain)
            self.assertEqual(len(restored.points), 48)
            self.assertEqual(len(restored.survey_state['lines']), 1)
            self.assertTrue(is_local_import(restored.ert_packet))
            self.assertTrue(restored.section_view.acquisition_mode)
            self.assertEqual(restored.table.rowCount(), 48)

    def test_one_import_is_one_undo_step_and_keeps_the_old_workspace(self):
        window = self.window()
        old_points = [dict(id=f'P{i+1:02}', x=float(i), y=5., z=1.) for i in range(3)]
        old_line = [[0., 5.], [2., 5.]]
        window.points = copy.deepcopy(old_points)
        window.line = copy.deepcopy(old_line)
        window.apply_imported_line(wenner_packet(4))
        self.assertEqual(len(window.survey_state['lines']), 2)
        self.assertEqual(window.survey_state['lines'][0]['workspace']['points'], old_points)
        self.assertEqual(window.survey_state['lines'][0]['workspace']['line'], old_line)
        self.assertEqual(window.survey_state['lines'][1]['workspace']['line'],
                         [[0., 0.], [6., 0.]])

        window.undo_edit()
        self.assertEqual(window.survey_state['lines'], [])
        self.assertEqual(window.points, old_points)
        self.assertEqual(window.line, old_line)

        window.redo_edit()
        self.assertEqual(len(window.survey_state['lines']), 2)
        self.assertEqual(len(window.points), 4)
        self.assertTrue(window.points[0]['local_frame'])

    def test_cancel_and_invalid_source_are_atomic(self):
        window = self.window()
        before = copy.deepcopy((window.survey_state, window.points, window.line,
                                window.ert_packet, window.case_path))
        with patch.object(Q.QFileDialog, 'getOpenFileName', return_value=('', '')):
            window.import_line_data()
        self.assertEqual((window.survey_state, window.points, window.line,
                          window.ert_packet, window.case_path), before)

        invalid = wenner_packet(4)
        invalid['rows'] = []
        with self.assertRaises(ValueError):
            window.apply_imported_line(invalid)
        self.assertEqual((window.survey_state, window.points, window.line,
                          window.ert_packet, window.case_path), before)

    def test_method_import_state_accepts_only_local_lines_without_terrain(self):
        from method_shell import MethodProject
        project = MethodProject()
        host = project.new_host()
        widget = project.open_module('ert', host)
        try:
            workspace = prepare_line_workspace(wenner_packet(4))
            state = dict(survey_state=dict(empty_survey(), lines=[
                dict(id='L01', name='L01', selected=True, visible=True,
                     workspace=workspace)], active_id='L01'),
                ert_packet=workspace['ert_packet'], points=workspace['points'],
                line=workspace['line'], line_endpoint_overrides={}, workflow_step=1,
                opacity=40, case=None)
            widget.import_state(state)
            self.assertIsNone(widget.terrain)
            self.assertEqual(len(widget.survey_state['lines']), 1)
            self.assertTrue(is_local_import(widget.ert_packet))
            self.assertTrue(widget.section_view.acquisition_mode)

            global_workspace = copy.deepcopy(workspace)
            global_workspace['terrain'] = 'old-terrain'
            global_workspace['ert_packet'] = wenner_packet(4)
            global_state = dict(state)
            global_state['survey_state'] = dict(empty_survey(), lines=[
                dict(id='L01', name='L01', selected=True, visible=True,
                     workspace=global_workspace)], active_id='L01')
            global_state['ert_packet'] = global_workspace['ert_packet']
            with self.assertRaises(ValueError):
                widget.import_state(global_state)
        finally:
            project.shutdown()
            self.app.processEvents()

    def test_old_wenner_instrument_flow_is_still_plain_observation(self):
        path = ROOT / '电法实验数据/解压数据/26102812708/26102812708.csv'
        if not path.is_file():
            self.skipTest('instrument CSV not present')
        packet = read_observations(path)
        self.assertEqual(packet['array'], 'wenner')
        self.assertEqual(len(packet['rows']), 84)
        self.assertEqual(packet['rows'][0]['abmn'], [0, 3, 1, 2])
        self.assertFalse(is_local_import(packet))


if __name__ == '__main__':
    unittest.main()
