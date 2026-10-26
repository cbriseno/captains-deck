#!/usr/bin/env python3
"""Export reviewed committed files, never repository metadata or local history."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def export(root, archive, attestation):
    root, archive, attestation = (Path(p).resolve() for p in (root, archive, attestation))
    if archive == attestation or root in archive.parents or root in attestation.parents:
        raise ValueError('archive and private attestation must be separate files outside the source checkout')
    if archive.exists() or attestation.exists():
        raise ValueError('refusing to overwrite an existing artifact')
    if git(root, 'status', '--porcelain').strip():
        raise ValueError('commit and clean the source checkout before export')
    commit = git(root, 'rev-parse', 'HEAD').decode().strip()
    plan = json.loads(git(root, 'show', commit + ':public-export.json'))
    include, exclude = set(plan['include']), set(plan['exclude'])
    tracked = {}
    for line in git(root, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if not line:
            continue
        meta, path = line.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        tracked[path.decode()] = (mode, kind, blob)
    if include & exclude or include - set(tracked) or set(tracked) - (include | exclude):
        raise ValueError('every tracked file must be classified exactly once in public-export.json')
    files, entries = {}, []
    for name in sorted(include):
        path = PurePosixPath(name)
        mode, kind, blob = tracked[name]
        if path.is_absolute() or '..' in path.parts or '\\' in name or kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('unsafe export member: ' + name)
        data = git(root, 'cat-file', 'blob', blob)
        # A useful stop, not a substitute for reviewing additions to the allowlist.
        if re.search(rb'[A-Za-z0-9.-]+\.tail[0-9a-f]+\.ts\.net|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}', data):
            raise ValueError('private network or credential marker in ' + name)
        files[name] = data
        entries.append({'path': name, 'mode': mode, 'sha256': sha(data)})
    manifest = {'schema': 'captains-deck.public-tree.v1', 'files': entries,
                'excluded': plan['exclude'], 'upstream': plan['upstream']}
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    files['PUBLIC-MANIFEST.json'] = encoded
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open('xb') as out, tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, data in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.mode = 0o755 if tracked.get(name, ('100644',))[0] == '100755' else 0o644
            item.size = len(data)
            # Fixed metadata: no machine username, source timestamp, path or refs.
            item.mtime = 0
            tar.addfile(item, io.BytesIO(data))
    receipt = {'schema': 'captains-deck.private-export.v1', 'source_commit': commit,
               'source_tree': git(root, 'rev-parse', commit + '^{tree}').decode().strip(),
               'source_checkout': str(root), 'archive_sha256': sha(archive.read_bytes()),
               'public_manifest_sha256': sha(encoded), 'included_files': len(entries),
               'excluded_files': len(exclude & set(tracked))}
    attestation.parent.mkdir(parents=True, exist_ok=True)
    with attestation.open('x', encoding='utf-8') as out:
        json.dump(receipt, out, indent=2)
        out.write('\n')
    attestation.chmod(0o600)
    return {'included_files': len(entries), 'excluded_files': receipt['excluded_files'],
            'archive_sha256': receipt['archive_sha256']}


def verify(archive):
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() for m in members):
            raise ValueError('archive must contain only unique regular files')
        manifest = json.load(tar.extractfile('PUBLIC-MANIFEST.json'))
        expected = {item['path']: item for item in manifest['files']}
        if set(names) != set(expected) | {'PUBLIC-MANIFEST.json'}:
            raise ValueError('unexpected or missing archive member')
        for name, item in expected.items():
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name:
                raise ValueError('unsafe archive path')
            if sha(tar.extractfile(name).read()) != item['sha256']:
                raise ValueError('hash mismatch: ' + name)
            if tar.getmember(name).mode != (0o755 if item['mode'] == '100755' else 0o644):
                raise ValueError('mode mismatch: ' + name)
    return {'verified_files': len(expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--attestation', help='PRIVATE source identity receipt; never publish this file')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        result = verify(args.archive)
    else:
        if not args.attestation:
            parser.error('--attestation is required for export')
        result = export(Path(__file__).resolve().parents[1], args.archive, args.attestation)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
