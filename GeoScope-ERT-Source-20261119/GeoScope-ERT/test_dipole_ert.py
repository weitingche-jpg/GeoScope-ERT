"""Tests for shared dipole-dipole geometry, R2 ingestion, and metadata."""
import copy
import hashlib
import math
import tempfile
import unittest
from pathlib import Path

from ert_arrays import (geometric_factor, geometry_metrics, separation_factor,
                        validate_abmn)
from ert_diagnostics import pseudosection_positions
from ert_protocol import is_r2_protocol, read_r2_protocol
from ert_quality import quality_report
from observed_ert import validate_packet
from observation_tables import analyze_rows, build_packet
from survey_project import make_survey, volume_inputs
from workflow_geometry import _digest, observation_signature

BORTH = Path('output/literature_ert_GJI2021/author_original/2D_Borth')
PROTOCOL = BORTH / 'protocol.dat'
ELECTRODES = BORTH / 'electrodes.dat'


def dd_packet(rows=None, electrodes=8, spacing=2.):
    rows = rows or [dict(abmn=[2, 3, 0, 1], rhoa=150.796447,
                         resistance=-4., k=geometric_factor([2, 3, 0, 1], spacing),
                         source_row=2, source_abmn=[0, 1, 2, 3])]
    return dict(schema=1, array='dipole-dipole', electrodes=electrodes,
                spacing=spacing, relative_error=.03, lam=20., max_iter=12,
                source_name='dd.csv', source_file='dd.csv',
                source_sha256='0' * 64, rows=rows, flat_assumed=True)


def write_text(path, text):
    Path(path).write_text(text, encoding='utf8')
    return Path(path)


class ArrayGeometryTests(unittest.TestCase):
    def test_wenner_keeps_positive_classical_factor(self):
        self.assertAlmostEqual(geometric_factor([0, 3, 1, 2], 2.), 4 * math.pi)

    def test_dd_signed_factor_polarity_reversal_and_reciprocity(self):
        forward = [0, 1, 2, 3]
        reverse_ab = [1, 0, 2, 3]
        reciprocal = [2, 3, 0, 1]
        k = geometric_factor(forward, 2.)
        self.assertAlmostEqual(k, -6 * math.pi * 2.)
        self.assertAlmostEqual(geometric_factor(reverse_ab, 2.), -k)
        self.assertAlmostEqual(geometric_factor(reciprocal, 2.), k)
        self.assertEqual(validate_abmn('dipole-dipole', reverse_ab, 4), (1, 0, 2, 3))
        self.assertEqual(validate_abmn('dipole-dipole', reciprocal, 4), (2, 3, 0, 1))

    def test_dd_geometry_rejects_overlap_unequal_and_fractional_gap(self):
        for bad in ([2, 3, 1, 2], [0, 2, 3, 5], [0, 1, 2, 4], [0, 1, 1, 2]):
            with self.assertRaises(ValueError):
                validate_abmn('dipole-dipole', bad, 8)
        # gap=2, a=1 is valid and gives n=2.
        self.assertEqual(validate_abmn('dipole-dipole', [0, 1, 3, 4], 8), (0, 1, 3, 4))
        # gap=1 with a=2 is not an integral number of dipole lengths.
        with self.assertRaises(ValueError):
            validate_abmn('dipole-dipole', [0, 2, 3, 5], 8)

    def test_metrics_use_four_pole_center_and_integer_n(self):
        metrics = geometry_metrics('dipole-dipole', [2, 3, 0, 1], 2.)
        self.assertEqual(metrics['center_m'], 3.)
        self.assertEqual(metrics['dipole_length_m'], 2.)
        self.assertEqual(metrics['separation_n'], 1)
        self.assertEqual(separation_factor([2, 3, 0, 1], 2.), 1)

    def test_validate_packet_accepts_dd_and_keeps_negative_resistance(self):
        packet = validate_packet(dd_packet())
        self.assertEqual(packet['array'], 'dipole-dipole')
        self.assertLess(packet['rows'][0]['resistance'], 0)
        self.assertLess(packet['rows'][0]['k'], 0)
        self.assertGreater(packet['rows'][0]['rhoa'], 0)
        bad = copy.deepcopy(packet)
        bad['rows'][0]['rhoa'] = -1
        with self.assertRaises(ValueError):
            validate_packet(bad)
        bad = copy.deepcopy(packet)
        bad['rows'][0]['k'] = abs(bad['rows'][0]['k'])
        with self.assertRaises(ValueError):
            validate_packet(bad)

    def test_signature_changes_with_array_but_legacy_wenner_digest_is_stable(self):
        wenner = dict(schema=1, array='wenner', electrodes=4, spacing=2.,
                      relative_error=.03, lam=20., max_iter=12,
                      rows=[dict(abmn=[0, 3, 1, 2], rhoa=100.)])
        old_payload = dict(count=4, spacing=2.,
                           rows=[dict(abmn=[0., 3., 1., 2.], rhoa=100.)],
                           relative_error=.03, lam=20., max_iter=12.)
        self.assertEqual(observation_signature(wenner), _digest(old_payload))
        dd = copy.deepcopy(wenner)
        dd['array'] = 'dipole-dipole'
        dd['rows'][0]['abmn'] = [2, 3, 0, 1]
        self.assertNotEqual(observation_signature(dd), observation_signature(wenner))
        dd_payload = dict(count=4, spacing=2.,
                          rows=[dict(abmn=[2., 3., 0., 1.], rhoa=100.)],
                          relative_error=.03, lam=20., max_iter=12.,
                          array='dipole-dipole')
        self.assertEqual(observation_signature(dd), _digest(dd_payload))


class GenericResistanceTableTests(unittest.TestCase):
    def test_resistance_input_computes_positive_rhoa_and_preserves_raw(self):
        rows = [['A', 'B', 'M', 'N', 'R'],
                ['3', '4', '1', '2', '-4.3493']]
        mapping = dict(a=0, b=1, m=2, n=3, rho=4)
        records = analyze_rows(rows, 0, mapping, 'Ω', electrodes=4,
                               array='dipole-dipole', value_kind='resistance',
                               spacing=2.)
        self.assertFalse(records[0]['error'])
        self.assertLess(records[0]['resistance'], 0)
        self.assertLess(records[0]['k'], 0)
        self.assertGreater(records[0]['rhoa'], 0)
        with tempfile.TemporaryDirectory() as folder:
            path = write_text(Path(folder) / 'dd.csv',
                              'A,B,M,N,R\n3,4,1,2,-4.3493\n')
            packet = build_packet(path, 'dd', rows, 0, mapping, 'Ω', 4, 2.,
                                  set(), array='dipole-dipole',
                                  value_kind='resistance')
        self.assertEqual(packet['array'], 'dipole-dipole')
        self.assertEqual(packet['value_kind'], 'resistance')
        self.assertEqual(packet['rows'][0]['resistance'], -4.3493)
        self.assertGreater(packet['rows'][0]['rhoa'], 0)

    def test_reversed_polarity_and_bad_unit_are_not_abs_fixed(self):
        rows = [['A', 'B', 'M', 'N', 'R'],
                ['4', '3', '1', '2', '4.3493']]
        mapping = dict(a=0, b=1, m=2, n=3, rho=4)
        record = analyze_rows(rows, 0, mapping, 'Ω', electrodes=4,
                              array='dipole-dipole', value_kind='resistance',
                              spacing=2.)[0]
        self.assertFalse(record['error'])
        self.assertGreater(record['k'], 0)
        self.assertGreater(record['rhoa'], 0)
        with self.assertRaises(ValueError):
            analyze_rows(rows, 0, mapping, 'Ω·m', electrodes=4,
                         array='dipole-dipole', value_kind='resistance',
                         spacing=2.)


class R2ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(PROTOCOL.is_file() and ELECTRODES.is_file(),
                         'Borth R2 source files not present')
    def test_borth_397_rows_48_electrodes_and_signed_conversion(self):
        packet = read_r2_protocol(PROTOCOL, ELECTRODES)
        self.assertEqual(packet['array'], 'dipole-dipole')
        self.assertEqual(packet['electrodes'], 48)
        self.assertAlmostEqual(packet['spacing'], 2.)
        self.assertEqual(len(packet['rows']), 397)
        self.assertEqual(packet['rows'][0]['abmn'], [2, 3, 0, 1])
        self.assertEqual(packet['rows'][0]['source_abmn'], [0, 1, 2, 3])
        self.assertEqual(packet['rows'][0]['source_native'], [1, 2, 3, 4])
        self.assertAlmostEqual(packet['rows'][0]['resistance'], -4.3493)
        self.assertLess(packet['rows'][0]['k'], 0)
        self.assertGreater(packet['rows'][0]['rhoa'], 0)
        self.assertTrue(all(row['rhoa'] > 0 for row in packet['rows']))
        self.assertTrue(all(row['resistance'] < 0 for row in packet['rows']))
        self.assertTrue(all(row['k'] < 0 for row in packet['rows']))
        self.assertEqual(packet['source_sha256_protocol'],
                         hashlib.sha256(PROTOCOL.read_bytes()).hexdigest())
        self.assertEqual(packet['source_sha256_electrodes'],
                         hashlib.sha256(ELECTRODES.read_bytes()).hexdigest())
        self.assertEqual(packet['source_sha256'],
                         hashlib.sha256(PROTOCOL.read_bytes() + b'\x00' +
                                        ELECTRODES.read_bytes()).hexdigest())
        self.assertTrue(validate_packet(packet))

    @unittest.skipUnless(PROTOCOL.is_file(), 'Borth protocol not present')
    def test_r2_detection_does_not_treat_res2dinv_as_r2(self):
        self.assertTrue(is_r2_protocol(PROTOCOL))
        self.assertFalse(is_r2_protocol(
            Path('电法实验数据/解压数据/26102812708/26102812708_rd.dat')))

    def test_strict_count_flatness_spacing_and_sign_rejections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            electrodes = write_text(root / 'electrodes.dat',
                                    '0 0 0\n2 0 0\n4 0 0\n6 0 0\n')
            protocol = write_text(root / 'protocol.dat',
                                  '1\n1 1 2 3 4 -4.0\n')
            packet = read_r2_protocol(protocol, electrodes)
            self.assertEqual(len(packet['rows']), 1)
            self.assertGreater(packet['rows'][0]['rhoa'], 0)

            bad = write_text(root / 'bad.dat', '2\n1 1 2 3 4 -4.0\n')
            with self.assertRaises(ValueError):
                read_r2_protocol(bad, electrodes)
            bad = write_text(root / 'bad.dat', '1\n1 1 2 3 4 4.0\n')
            with self.assertRaises(ValueError):
                read_r2_protocol(bad, electrodes)
            flat = write_text(root / 'flat.dat',
                              '0 0 0\n2 1 0\n4 0 0\n6 0 0\n')
            with self.assertRaises(ValueError):
                read_r2_protocol(protocol, flat)
            uniform = write_text(root / 'uniform.dat',
                                 '0 0 0\n2 0 0\n5 0 0\n6 0 0\n')
            with self.assertRaises(ValueError):
                read_r2_protocol(protocol, uniform)


class QualityAndDiagnosticsTests(unittest.TestCase):
    def test_qc_does_not_flag_dd_or_negative_resistance(self):
        packet = dd_packet()
        record = quality_report(packet)['records'][0]
        self.assertNotIn('invalid_abmn', record['flags'])
        self.assertNotIn('invalid_geometry', record['flags'])
        self.assertEqual(record['separation_n'], 1)
        self.assertEqual(record['dipole_length_m'], 2.)
        self.assertEqual(record['center_m'], 3.)
        self.assertEqual(record['resistance'], -4.)

    def test_wenner_qc_record_keys_remain_legacy(self):
        packet = dict(schema=1, array='wenner', electrodes=8, spacing=2.,
                      rows=[dict(abmn=[0, 3, 1, 2], rhoa=100.)])
        record = quality_report(packet)['records'][0]
        self.assertEqual(set(record), {'index', 'source_row', 'enabled', 'center_m',
                                       'spacing_m', 'rhoa', 'voltage_mV',
                                       'current_mA', 'flags', 'note'})

    def test_dd_pseudosection_uses_n_and_dipole_length_not_depth(self):
        packet = dd_packet()
        result = pseudosection_positions(packet, [0])
        self.assertEqual(result['center_m'], [3.])
        self.assertEqual(result['dipole_length_m'], [2.])
        self.assertEqual(result['separation_n'], [1])
        self.assertEqual(result['spacing_m'], [2.])


class Survey3DGateTests(unittest.TestCase):
    def terrain(self):
        from types import SimpleNamespace
        import numpy as np
        return SimpleNamespace(source='terrain', crs='LOCAL',
                               x=np.array([-100., 100.]), y=np.array([-100., 100.]),
                               height=lambda xy: np.zeros(len(xy)))

    def test_volume_entry_rejects_dd_with_explicit_reason(self):
        packets = [dd_packet() for _ in range(3)]
        state = make_survey(packets, self.terrain(), [0, 0, 0], 0, 2, hypothesis=True)
        with self.assertRaisesRegex(ValueError, '偶极|三维'):
            volume_inputs(state, self.terrain(), require_sections=False)


if __name__ == '__main__':
    unittest.main()
