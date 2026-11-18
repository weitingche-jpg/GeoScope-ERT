"""Portable, checksummed GeoScope project archives.

This service stores finite JSON and source files only. It never executes archive
contents, resolves application references, or changes the original source files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid
import zipfile

FORMAT = 'geoscope-project'
VERSION = 1
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024**2
MAX_MEMBERS = 100_000
CHUNK_SIZE = 1024**2
_RESERVED = {'con', 'prn', 'aux', 'nul'} | {
    f'{prefix}{i}' for prefix in ('com', 'lpt') for i in range(1, 10)}


class BundleError(ValueError):
    """Archive validation or portable-asset error suitable for user display."""


def _safe_member(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise BundleError('项目包包含无效的文件路径。')
    p = PurePosixPath(name)
    if p.is_absolute() or name != p.as_posix():
        raise BundleError('项目包包含绝对路径或非标准路径。')
    for part in name.split('/'):
        if (part in ('', '.', '..') or ':' in part or part.endswith((' ', '.'))
                or part.split('.')[0].casefold() in _RESERVED
                or any(ord(c) < 32 or c in '<>"|?*' for c in part)):
            raise BundleError('项目包包含不安全的文件路径。')
    return name


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise BundleError('项目内容必须为有效且有限的 JSON 数据。') from error


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError('项目包清单包含重复字段。')
        result[key] = value
    return result


def _read_json(data):
    def reject_constant(value):
        raise BundleError('项目包包含非有限数值。')
    try:
        return json.loads(data.decode('utf-8'), parse_constant=reject_constant,
                          object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise BundleError('无法读取项目包清单。') from error


def _references(value):
    if isinstance(value, str) and value.startswith('asset:'):
        yield value[6:]
    elif isinstance(value, dict):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BundleError('项目包容量限制必须是正整数。')
    return value


def _report(progress, value, text=''):
    """Report 0..1 progress from the calling (background) thread."""
    if progress is not None:
        progress(float(value), text)


def _scan_assets(target, assets, limit):
    if not isinstance(assets, dict):
        # Accept Mapping implementations while making duplicate-key semantics explicit.
        try:
            assets = dict(assets)
        except (TypeError, ValueError) as error:
            raise BundleError('素材列表必须是名称到路径的映射。') from error
    declarations, files, directories = {}, {}, set()
    total = 0
    for index, (key, raw_path) in enumerate(assets.items()):
        if not isinstance(key, str) or not key:
            raise BundleError('素材名称必须是非空字符串。')
        original = Path(raw_path)
        if original.is_symlink():
            raise BundleError('项目包不接受符号链接素材。')
        source = original.resolve()
        if not source.exists():
            raise BundleError(f'项目素材不存在：{original}')
        if source == target or (source.is_dir() and target.is_relative_to(source)):
            raise BundleError('项目包不能保存在待打包素材内部。')
        root = _safe_member(f'assets/{index:05d}/{source.name}')
        if source.is_file():
            declarations[key] = {'kind': 'file', 'path': root}
            candidates = [(root, source)]
        elif source.is_dir():
            declarations[key] = {'kind': 'directory', 'path': root}
            directories.add(root)
            candidates = []
            for current, dirnames, filenames in os.walk(source, followlinks=False):
                dirnames.sort()
                filenames.sort()
                current = Path(current)
                for dirname in dirnames:
                    child = current / dirname
                    if child.is_symlink():
                        raise BundleError('项目素材目录中包含符号链接。')
                    directories.add(_safe_member(root + '/' + child.relative_to(source).as_posix()))
                for filename in filenames:
                    child = current / filename
                    if child.is_symlink() or not stat.S_ISREG(child.stat().st_mode):
                        raise BundleError('项目素材中包含非普通文件。')
                    candidates.append((_safe_member(root + '/' + child.relative_to(source).as_posix()), child))
        else:
            raise BundleError('项目素材必须是普通文件或目录。')
        for name, file_path in candidates:
            size = file_path.stat().st_size
            total += size
            if total > limit:
                raise BundleError('项目素材超过允许的总容量。')
            if len(files) + len(directories) >= MAX_MEMBERS:
                raise BundleError('项目包文件数量过多。')
            files[name] = (file_path, size)
    _validate_names(list(files) + list(directories))
    return declarations, files, sorted(directories)


def _validate_names(names):
    seen = set()
    for name in names:
        folded = _safe_member(name).casefold()
        if folded in seen:
            raise BundleError('项目包包含重复或大小写冲突的文件路径。')
        seen.add(folded)


def write_bundle(path, document, assets, *, max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
                 progress=None):
    """Atomically save document and recursive assets; return the absolute archive path.

    Application document values such as ``asset:terrain`` are preserved. An asset
    may be a file or a directory; its basename and all internal names are retained.
    Source files are not modified. Failed saves leave any previous archive intact.
    ``progress`` is an optional callable receiving ``(fraction_0_to_1, text)`` and
    is invoked from the caller's thread; it must not touch GUI objects.
    """
    limit = _limit(max_total_bytes)
    document = _read_json(_json_bytes(document))
    target = Path(path).resolve()
    declarations, sources, directories = _scan_assets(target, assets, limit)
    _report(progress, 0.05, '已完成素材扫描')
    missing = set(_references(document)) - set(declarations)
    if missing:
        raise BundleError('项目引用的素材未提供：' + ', '.join(sorted(missing)))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / ('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        file_records = {}
        total = 0
        total_bytes = sum(size for _source, size in sources.values())
        percent = -1
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            for name, (source, expected_size) in sources.items():
                if total_bytes:
                    current = int(total * 100 / total_bytes)
                    if current != percent:
                        percent = current
                        _report(progress, 0.1 + 0.8 * current / 100,
                                f'正在打包：{source.name}')
                else:
                    _report(progress, 0.1, f'正在打包：{source.name}')
                digest = hashlib.sha256()
                size = 0
                with source.open('rb') as incoming, archive.open(name, 'w', force_zip64=True) as outgoing:
                    while chunk := incoming.read(CHUNK_SIZE):
                        size += len(chunk)
                        total += len(chunk)
                        if total > limit:
                            raise BundleError('写入时素材总容量超限。')
                        digest.update(chunk)
                        outgoing.write(chunk)
                        if total_bytes:
                            current = int(total * 100 / total_bytes)
                            if current != percent:
                                percent = current
                                _report(progress, 0.1 + 0.8 * current / 100,
                                        f'正在打包：{source.name}')
                if size != expected_size:
                    raise BundleError('保存时原始素材发生变化，请重试。')
                file_records[name] = {'size': size, 'sha256': digest.hexdigest()}
            _report(progress, 0.9, '正在写入项目清单')
            manifest = {'format': FORMAT, 'version': VERSION, 'document': document,
                        'assets': declarations, 'directories': directories, 'files': file_records}
            encoded = _json_bytes(manifest)
            if len(encoded) > MAX_MANIFEST_BYTES:
                raise BundleError('项目清单过大。')
            archive.writestr('manifest.json', encoded)
        _report(progress, 0.96, '正在完成项目包')
        with temporary.open('r+b') as completed:
            os.fsync(completed.fileno())
        os.replace(temporary, target)
        _report(progress, 1.0, '项目包已保存')
        return target
    except BundleError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise BundleError(f'保存项目包失败：{error}') from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _validated_manifest(archive, limit):
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise BundleError('项目包文件数量过多。')
    _validate_names([info.filename for info in infos])
    info_by_name = {info.filename: info for info in infos}
    total = 0
    for info in infos:
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
            raise BundleError('项目包包含链接、目录条目或加密条目。')
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise BundleError('项目包包含特殊文件。')
        total += info.file_size
        if total > limit + MAX_MANIFEST_BYTES:
            raise BundleError('项目包解压容量超过限制。')
        # Allow strongly compressible DEMs while rejecting extreme metadata bombs.
        if info.file_size > 1024**2 and info.file_size / max(1, info.compress_size) > 10_000:
            raise BundleError('项目包压缩比例异常。')
    manifest_info = info_by_name.get('manifest.json')
    if manifest_info is None or manifest_info.file_size > MAX_MANIFEST_BYTES:
        raise BundleError('项目包缺少有效清单。')
    manifest = _read_json(archive.read(manifest_info))
    if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
            or type(manifest.get('version')) is not int or manifest['version'] != VERSION):
        raise BundleError('不支持的项目包版本。')
    assets, files, directories = (manifest.get(k) for k in ('assets', 'files', 'directories'))
    if not isinstance(assets, dict) or not isinstance(files, dict) or not isinstance(directories, list):
        raise BundleError('项目包清单结构无效。')
    if 'document' not in manifest:
        raise BundleError('项目包缺少项目内容。')
    _json_bytes(manifest['document'])
    _validate_names(list(files) + directories)
    if len(files) + len(directories) > MAX_MEMBERS:
        raise BundleError('项目包内容条目过多。')
    if set(info_by_name) != set(files) | {'manifest.json'}:
        raise BundleError('项目包文件与清单不一致。')
    declared_roots = []
    for key, asset in assets.items():
        if not isinstance(key, str) or not key or not isinstance(asset, dict):
            raise BundleError('项目包素材声明无效。')
        name = _safe_member(asset.get('path'))
        if not name.startswith('assets/'):
            raise BundleError('项目素材必须位于素材目录。')
        if asset.get('kind') == 'file' and name in files:
            declared_roots.append((name, False))
        elif asset.get('kind') == 'directory' and name in directories:
            declared_roots.append((name, True))
        else:
            raise BundleError('项目素材类型或根路径无效。')
    _validate_names([root for root, _ in declared_roots])
    for i, (root, _) in enumerate(declared_roots):
        for other, _ in declared_roots[i+1:]:
            if root.casefold().startswith(other.casefold() + '/') or other.casefold().startswith(root.casefold() + '/'):
                raise BundleError('项目素材根路径不能互相嵌套。')
    for name in list(files) + directories:
        if not any(name == root or (is_dir and name.startswith(root + '/')) for root, is_dir in declared_roots):
            raise BundleError('项目包中存在未归属的素材。')
    folded_files = {name.casefold() for name in files}
    for name in list(files) + directories:
        parents = PurePosixPath(name).parents
        if any(parent.as_posix().casefold() in folded_files for parent in parents):
            raise BundleError('项目包文件与目录冲突。')
    size_sum = 0
    for name, record in files.items():
        if (not isinstance(record, dict) or type(record.get('size')) is not int
                or record['size'] < 0 or not isinstance(record.get('sha256'), str)
                or re.fullmatch('[0-9a-f]{64}', record['sha256']) is None
                or record['size'] != info_by_name[name].file_size):
            raise BundleError('项目包素材大小或校验信息无效。')
        size_sum += record['size']
    if size_sum > limit:
        raise BundleError('项目包素材超过允许的总容量。')
    if set(_references(manifest['document'])) - set(assets):
        raise BundleError('项目内容引用了不存在的素材。')
    return manifest, info_by_name


def read_bundle(path, destination, *, max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
                progress=None):
    """Validate and extract into a new unique destination child.

    Return ``(document, {asset_key: absolute_extracted_path})``. Existing destination
    files are never overwritten. Failed extraction leaves no partial project child.
    Application code owns retention/deletion of successfully extracted directories.
    ``progress`` is an optional callable receiving ``(fraction_0_to_1, text)`` and
    is invoked from the caller's thread; it must not touch GUI objects.
    """
    limit = _limit(max_total_bytes)
    parent = Path(destination).resolve()
    stage = None
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            manifest, infos = _validated_manifest(archive, limit)
            _report(progress, 0.05, '项目包校验通过')
            parent.mkdir(parents=True, exist_ok=True)
            identifier = uuid.uuid4().hex
            stage = parent / ('.project-' + identifier + '.stage')
            committed = parent / ('project-' + identifier)
            stage.mkdir()
            for name in manifest['directories']:
                (stage / name).mkdir(parents=True, exist_ok=True)
            total = 0
            expected_total = sum(record['size'] for record in manifest['files'].values())
            percent = -1
            for name, record in manifest['files'].items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(infos[name]) as incoming, target.open('xb') as outgoing:
                    while chunk := incoming.read(CHUNK_SIZE):
                        total += len(chunk)
                        size += len(chunk)
                        if total > limit or size > record['size']:
                            raise BundleError('项目包解压数据超过限制。')
                        digest.update(chunk)
                        outgoing.write(chunk)
                        if expected_total:
                            current = int(total * 100 / expected_total)
                            if current != percent:
                                percent = current
                                _report(progress, 0.05 + 0.9 * current / 100,
                                        f'正在解压：{name}')
                if size != record['size'] or digest.hexdigest() != record['sha256']:
                    raise BundleError(f'项目包素材校验失败：{name}')
            stage.rename(committed)
            stage = None
            paths = {key: committed / declaration['path'] for key, declaration in manifest['assets'].items()}
            _report(progress, 1.0, '项目包已解压')
            return manifest['document'], paths
    except BundleError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, EOFError, KeyError, TypeError) as error:
        raise BundleError(f'读取项目包失败：{error}') from error
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
