import copy
import unittest
from types import SimpleNamespace
import numpy as np
from survey_project import make_survey,parallel_calibrate,volume_inputs,survey_signature


def packet():
    return dict(schema=1,array='wenner',electrodes=4,spacing=2.,relative_error=.03,lam=20.,max_iter=3,
        confirmed=True,rows=[dict(abmn=[0,3,1,2],rhoa=125.663706,voltage_mV=10.,current_mA=1.)])

class SurveyTests(unittest.TestCase):
    def terrain(self):return SimpleNamespace(source='terrain',crs='LOCAL',x=np.array([-100.,100.]),y=np.array([-100.,100.]),height=lambda xy:np.zeros(len(xy)))

    def test_parallel_calibration_preserves_measurements_and_lengths(self):
        packets=[packet() for _ in range(9)];before=copy.deepcopy(packets)
        state=make_survey(packets,self.terrain(),[0,0,0],0,2,hypothesis=True)
        self.assertEqual(packets,before)
        self.assertEqual(len(state['lines']),9)
        np.testing.assert_allclose([l['workspace']['points'][0]['y'] for l in state['lines']],np.arange(9)*2)
        for line in state['lines']:
            self.assertEqual(line['workspace']['ert_packet']['rows'],before[0]['rows'])
            self.assertEqual(np.linalg.norm(np.subtract(*np.asarray(line['workspace']['line'])[::-1])),6.)
        moved=parallel_calibrate(state,self.terrain(),[10,10,0],90,2)
        self.assertEqual(state['lines'][0]['workspace']['points'][0]['x'],0.)
        np.testing.assert_allclose([l['workspace']['points'][0]['x'] for l in moved['lines']],10-np.arange(9)*2)

    def test_global_electrodes_do_not_merge_same_local_names(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        result=volume_inputs(state,self.terrain(),require_sections=False)
        self.assertEqual(len(result['sensors']),12)
        self.assertEqual(result['abmn'],[[0,3,1,2],[4,7,5,6],[8,11,9,10]])
        self.assertTrue(result['geometry_hypothesis'])
        self.assertEqual(result['resistance'],[10.,10.,10.])

    def test_signature_stale_and_insufficient_geometry(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        initial=survey_signature(state);changed=copy.deepcopy(state)
        changed['lines'][0]['name']='改名';self.assertEqual(initial,survey_signature(changed))
        changed['lines'][0]['workspace']['ert_packet']['rows'][0]['rhoa']=101
        self.assertNotEqual(initial,survey_signature(changed))
        state['lines'][0]['selected']=False
        with self.assertRaises(ValueError):volume_inputs(state,self.terrain(),False)

    def test_hypothesis_cannot_silently_become_field_geometry(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        state['hypothesis']=False
        with self.assertRaisesRegex(ValueError,'假设'):volume_inputs(state,self.terrain(),False)

    def test_original_voltage_current_conflict_is_not_ignored(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        state['lines'][0]['workspace']['ert_packet']['rows'][0]['rhoa']=200.
        with self.assertRaisesRegex(ValueError,'矛盾'):volume_inputs(state,self.terrain(),False)

    def test_missing_sections_explain_disabled_volume_button(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        with self.assertRaisesRegex(ValueError,'L01.*剖面'):volume_inputs(state,self.terrain())

    def test_ui_preflight_rejects_worker_scale_limit(self):
        terrain=self.terrain();terrain.x=np.array([-1000.,1000.])
        p=packet();p['electrodes']=256
        state=make_survey([p for _ in range(3)],terrain,[0,0,0],0,2,True)
        with self.assertRaisesRegex(ValueError,'规模|720'):volume_inputs(state,terrain,False)

    def test_unregistered_local_lines_cannot_enter_global_3d(self):
        state=make_survey([packet() for _ in range(3)],self.terrain(),[0,0,0],0,2,True)
        state['lines'][0]['workspace']['ert_packet']['binding']={'mode':'local'}
        with self.assertRaisesRegex(ValueError,'配准'):volume_inputs(state,self.terrain(),False)

    def test_factory_cannot_remove_an_existing_hypothesis_marker(self):
        p=packet();p['geometry_hypothesis']=True
        with self.assertRaisesRegex(ValueError,'假设'):make_survey([p],self.terrain(),[0,0,0],0,2,False)

if __name__=='__main__':unittest.main()
