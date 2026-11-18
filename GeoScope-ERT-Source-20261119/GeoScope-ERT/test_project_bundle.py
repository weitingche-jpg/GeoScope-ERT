"""Portable archive tests use only disposable directories inside the workspace."""
import json
import shutil
import unittest
import uuid
import zipfile
from pathlib import Path


class ProjectBundleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('.cache') / ('bundle-tests-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def api(self):
        from project_bundle import write_bundle, read_bundle, BundleError
        return write_bundle, read_bundle, BundleError

    def test_relocatable_files_directories_and_closed_module_state(self):
        write, read, _ = self.api()
        source = self.root / '原始数据'
        source.mkdir()
        (source / 'terrain.tif').write_bytes(b'fake-dem\x00')
        case = source / 'result'
        (case / 'nested').mkdir(parents=True)
        (case / 'nested' / 'model.npz').write_bytes(b'model')
        (case / 'empty').mkdir()
        other = self.root / 'different'
        other.mkdir()
        (other / 'terrain.tif').write_bytes(b'other-dem')
        document = {'terrain': 'asset:terrain', 'modules': {'ert': {
            'open': False, 'result': 'asset:case'}}, 'windows': [[1, 2, 3, 4]]}
        bundle = write(self.root / '校园.geoscope', document, {
            'terrain': source / 'terrain.tif', 'case': case,
            'other': other / 'terrain.tif'})
        relocated = self.root / '另一台电脑'
        relocated.mkdir()
        shutil.move(str(bundle), str(relocated / bundle.name))
        shutil.rmtree(source)
        shutil.rmtree(other)
        actual, paths = read(relocated / bundle.name, relocated / '打开')
        self.assertEqual(actual, document)
        self.assertEqual(paths['terrain'].read_bytes(), b'fake-dem\x00')
        self.assertEqual(paths['other'].read_bytes(), b'other-dem')
        self.assertEqual((paths['case'] / 'nested/model.npz').read_bytes(), b'model')
        self.assertTrue((paths['case'] / 'empty').is_dir())

    def create_bundle(self):
        write, _, _ = self.api()
        source = self.root / 'readings.csv'
        source.write_text('station,g\nP1,1\n', encoding='utf-8')
        return write(self.root / 'valid.geoscope', {'source': 'asset:raw'}, {'raw': source})

    def mutate(self, source, callback):
        target = self.root / ('bad-' + uuid.uuid4().hex + '.geoscope')
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'w') as out:
            members = {info.filename: original.read(info) for info in original.infolist()}
            callback(members)
            for name, data in members.items():
                out.writestr(name, data)
        return target

    def test_checksum_corruption_never_changes_existing_destination(self):
        _, read, error = self.api()
        valid = self.create_bundle()
        def corrupt(members):
            key = next(k for k in members if k.startswith('assets/'))
            members[key] += b'changed'
        bad = self.mutate(valid, corrupt)
        destination = self.root / 'existing'
        destination.mkdir()
        (destination / 'keep.txt').write_text('keep')
        before = sorted(p.relative_to(destination).as_posix() for p in destination.rglob('*'))
        with self.assertRaises(error):
            read(bad, destination)
        self.assertEqual(before, sorted(p.relative_to(destination).as_posix() for p in destination.rglob('*')))
        self.assertEqual((destination / 'keep.txt').read_text(), 'keep')

    def test_rejects_traversal_absolute_windows_and_backslash_entries(self):
        _, read, error = self.api()
        valid = self.create_bundle()
        for name in ['../escape', '/absolute', 'C:/drive', 'assets\\escape',
                     'assets/../escape', 'assets/con', 'assets/name:stream']:
            with self.subTest(name=name):
                bad = self.mutate(valid, lambda members: members.update({name: b'x'}))
                with self.assertRaises(error):
                    read(bad, self.root / 'extracted')

    def test_rejects_extra_duplicate_and_case_colliding_members(self):
        _, read, error = self.api()
        valid = self.create_bundle()
        bad = self.mutate(valid, lambda members: members.update({'untracked.txt': b'x'}))
        with self.assertRaises(error):
            read(bad, self.root / 'extracted')
        duplicate = self.root / 'duplicate.geoscope'
        with zipfile.ZipFile(valid) as original, zipfile.ZipFile(duplicate, 'w') as out:
            for info in original.infolist():
                out.writestr(info.filename, original.read(info))
            out.writestr('MANIFEST.JSON', b'{}')
        with self.assertRaises(error):
            read(duplicate, self.root / 'extracted')

    def test_rejects_symlink_archive_entry(self):
        _, read, error = self.api()
        valid = self.create_bundle()
        bad = self.root / 'symlink.geoscope'
        with zipfile.ZipFile(valid) as original, zipfile.ZipFile(bad, 'w') as out:
            for info in original.infolist():
                out.writestr(info.filename, original.read(info))
            info = zipfile.ZipInfo('assets/link')
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            out.writestr(info, '../outside')
        with self.assertRaises(error):
            read(bad, self.root / 'extracted')

    def test_missing_asset_or_nonfinite_json_preserves_existing_archive(self):
        write, _, error = self.api()
        valid = self.create_bundle()
        before = valid.read_bytes()
        with self.assertRaises((error, ValueError)):
            write(valid, {'bad': float('nan')}, {})
        self.assertEqual(valid.read_bytes(), before)
        with self.assertRaises(error):
            write(valid, {}, {'missing': self.root / 'missing'})
        self.assertEqual(valid.read_bytes(), before)

    def test_output_inside_asset_directory_and_size_limits_rejected(self):
        write, read, error = self.api()
        source = self.root / 'source'
        source.mkdir()
        (source / 'data.txt').write_bytes(b'a' * 1024)
        with self.assertRaises(error):
            write(source / 'recursive.geoscope', {}, {'folder': source})
        with self.assertRaises(error):
            write(self.root / 'too-large.geoscope', {}, {'folder': source}, max_total_bytes=10)
        valid = self.create_bundle()
        with self.assertRaises(error):
            read(valid, self.root / 'extracted', max_total_bytes=2)

    def test_rejects_nonfinite_manifest_and_missing_asset_token(self):
        write, read, error = self.api()
        with self.assertRaises(error):
            write(self.root / 'missing-token.geoscope', {'file': 'asset:absent'}, {})
        valid = self.create_bundle()
        def bad_json(members):
            members['manifest.json'] = members['manifest.json'].replace(b'"source":', b'"bad": NaN, "source":')
        with self.assertRaises(error):
            read(self.mutate(valid, bad_json), self.root / 'extracted')

    def test_manifest_file_directory_collision_is_rejected_before_extract(self):
        _, read, error = self.api()
        valid = self.create_bundle()
        def collide(members):
            manifest = json.loads(members['manifest.json'])
            file_name = next(iter(manifest['files']))
            manifest['directories'].append(file_name + '/child')
            members['manifest.json'] = json.dumps(manifest).encode()
        destination = self.root / 'not-created'
        with self.assertRaises(error):
            read(self.mutate(valid, collide), destination)
        self.assertFalse(destination.exists())

    def test_empty_project_and_repeated_reads_remain_independent(self):
        write, read, _ = self.api()
        path = write(self.root / 'empty.geoscope', {'modules': {}, 'terrain': None}, {})
        document, first = read(path, self.root / 'opened')
        self.assertEqual(document, {'modules': {}, 'terrain': None})
        self.assertEqual(first, {})
        read(path, self.root / 'opened')
        self.assertEqual(len(list((self.root / 'opened').iterdir())), 2)


if __name__ == '__main__':
    unittest.main()
