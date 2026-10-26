"""Publication boundaries: deny unknown files/history and detect altered artifacts."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('export_public', Path(__file__).resolve().parents[1] / 'scripts/export_public.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def test_unclassified_committed_file_stops_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = json.dumps({'include': ['README.md'], 'exclude': {}}).encode()
            responses = [b'', b'a' * 40, plan, b'100644 blob abc\tREADME.md\0' + b'100644 blob def\tprivate.txt\0']
            with patch.object(exporter, 'git', side_effect=responses):
                with self.assertRaisesRegex(ValueError, 'classified'):
                    exporter.export(root / 'source', root / 'source.tar', root / 'private.json')
            self.assertFalse((root / 'source.tar').exists())

    def test_dirty_source_stops_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(exporter, 'git', return_value=b' M README.md'):
                with self.assertRaisesRegex(ValueError, 'clean'):
                    exporter.export(root / 'source', root / 'source.tar', root / 'private.json')

    def test_manifest_detects_tampered_content_and_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'source.tar'
            manifest = {'files': [{'path': 'README.md', 'mode': '100644',
                                  'sha256': hashlib.sha256(b'reviewed').hexdigest()}]}
            for data, extra in [(b'tampered', False), (b'reviewed', True)]:
                with tarfile.open(archive, 'w') as tar:
                    entries = {'PUBLIC-MANIFEST.json': json.dumps(manifest).encode(), 'README.md': data}
                    if extra:
                        entries['operator-state.json'] = b'{}'
                    for name, content in entries.items():
                        item = tarfile.TarInfo(name)
                        item.mode, item.size = 0o644, len(content)
                        tar.addfile(item, io.BytesIO(content))
                with self.assertRaisesRegex(ValueError, 'mismatch|unexpected'):
                    exporter.verify(archive)


if __name__ == '__main__':
    unittest.main()
