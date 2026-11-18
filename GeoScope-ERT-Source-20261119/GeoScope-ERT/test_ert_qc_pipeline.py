import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from observed_ert import validate_packet
from workflow_geometry import observation_signature


def sample():
    return dict(schema=1,array='wenner',electrodes=5,spacing=2.,
        relative_error=.03,lam=20.,max_iter=3,
        rows=[dict(abmn=[0,3,1,2],rhoa=80.),dict(abmn=[1,4,2,3],rhoa=90.)])


class QCPipelineTests(unittest.TestCase):
    def test_legacy_result_cannot_inherit_current_filtered_signature(self):
        from step_workflow import StepWorkflow
        packet=sample();packet['source_sha256']='same-source'
        packet['rows'][0].update(enabled=False,qc_note='待复核')
        host=SimpleNamespace(ert_packet=packet,terrain=SimpleNamespace(source='terrain'),
            case_info=Mock(),update_workflow_result_placement=Mock())
        meta=dict(source_sha256='same-source',observations=2,cells=1,chi2=1.,rrms_percent=3.)
        with tempfile.TemporaryDirectory(dir='.cache') as folder:
            path=Path(folder)
            np.savez(path/'model.npz',observed=[80.,90.],station=[0.,2.,4.,6.,8.])
            StepWorkflow._load_workflow_case(host,path,meta)
        self.assertIsNone(host.result_observation_signature)

    def test_disabled_row_changes_physical_input_signature(self):
        original=sample();changed=copy.deepcopy(original)
        changed['rows'][0].update(enabled=False,qc_note='复测异常，暂不参与')
        self.assertNotEqual(observation_signature(original),observation_signature(changed))
        changed['rows'][0]['qc_note']='仅修改处理说明'
        signature=observation_signature(changed)
        changed['rows'][0]['qc_note']='补充说明'
        self.assertEqual(observation_signature(changed),signature)
        changed['rows'][0]['enabled']=True
        self.assertEqual(observation_signature(changed),observation_signature(original))

    def test_no_active_data_is_a_saveable_but_not_computable_draft(self):
        packet=sample()
        for row in packet['rows']:row.update(enabled=False,qc_note='待核实')
        with self.assertRaises(ValueError):validate_packet(packet)
        validate_packet(packet,allow_empty=True)

    def test_disabled_state_needs_a_reason_and_boolean_flag(self):
        for enabled,note in [(False,''),('false','有原因')]:
            packet=sample();packet['rows'][0].update(enabled=enabled,qc_note=note)
            with self.assertRaises(ValueError):validate_packet(packet)


if __name__=='__main__':unittest.main()
