"""Provenance parsing uses synthetic offline sources only."""
import base64
import hashlib
import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_provenance as provenance


class RuntimeProvenance(unittest.TestCase):
    def test_official_content_digest(self):
        payload = b'offline fixture, never executed'
        entry = {'method': 'official-content-sha256', 'name': 'get-pip.py',
                 'sha256': hashlib.sha256(payload).hexdigest(),
                 'source_url': 'https://bootstrap.pypa.io/get-pip.py',
                 'download_url': 'https://bootstrap.pypa.io/get-pip.py'}
        provenance.verify(entry, payload)
        with self.assertRaises(ValueError):
            provenance.verify(entry, b'tampered')

    def test_checksum_file_requires_one_exact_filename(self):
        entry = {'method': 'shasums256', 'name': 'node.zip'}
        digest = 'a' * 64
        self.assertEqual(provenance.published_digest(entry, (digest + '  node.zip\n').encode()), digest)
        for data in [b'', (digest + '  other.zip\n').encode(), ((digest + ' node.zip\n') * 2).encode()]:
            with self.assertRaises(ValueError):
                provenance.published_digest(entry, data)

    def test_sigstore_published_digest_is_parsed_without_claiming_signature_verification(self):
        data = json.dumps({'messageSignature': {'messageDigest': {
            'algorithm': 'SHA2_256', 'digest': base64.b64encode(bytes.fromhex('b' * 64)).decode()}}}).encode()
        self.assertEqual(provenance.published_digest({'method': 'sigstore-published-digest'}, data), 'b' * 64)
        with self.assertRaises(ValueError):
            provenance.published_digest({'method': 'sigstore-published-digest'}, data.replace(b'SHA2_256', b'other'))

    def test_nonofficial_urls_and_redirects_are_rejected(self):
        for url in ['http://www.python.org/file', 'https://claude.ai/', 'https://nodejs.org.evil.invalid/',
                    'https://user:password@nodejs.org/file', 'https://nodejs.org:8443/file']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                provenance.official_url(url)
        with self.assertRaises(ValueError):
            provenance.OfficialRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid/')

    def test_shipped_manifest_uses_official_sources_and_valid_pins(self):
        manifest = json.loads((provenance.PROJECT / 'assets/runtime-provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], 1)
        self.assertEqual(len(manifest['files']), 3)
        for entry in manifest['files']:
            provenance.official_url(entry['source_url'])
            provenance.official_url(entry['download_url'])
            self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')

    def test_failed_recheck_replaces_old_success_report(self):
        with tempfile.TemporaryDirectory(dir=provenance.PROJECT / '.local') as folder:
            root = Path(folder)
            (root / 'assets').mkdir()
            manifest = {'version': 1, 'files': [{'name': 'node.zip', 'sha256': 'a'*64,
                'source_url': 'https://nodejs.org/checksums', 'download_url': 'https://nodejs.org/node.zip',
                'method': 'shasums256'}]}
            (root / 'assets/runtime-provenance.json').write_text(json.dumps(manifest), encoding='utf-8')
            output = root / '.local/runtime-provenance-check.json'
            output.parent.mkdir()
            output.write_text('{"success":true}', encoding='utf-8')
            with patch.object(provenance, 'PROJECT', root), \
                 patch.object(provenance.urllib.request, 'build_opener') as opener, \
                 contextlib.redirect_stdout(io.StringIO()):
                opener.return_value.open.side_effect = OSError('offline fixture')
                self.assertEqual(provenance.main(['--online']), 2)
            report = json.loads(output.read_text())
            self.assertFalse(report['success'])
            self.assertIn('checked_at', report)

    def test_invalid_or_missing_manifest_clears_prior_success_without_network(self):
        for data in (None, '{broken', '[]', '{}', '{"version":1,"files":[]}',
                     '{"version":1,"files":[{}]}'):
            with self.subTest(data=data), tempfile.TemporaryDirectory(dir=provenance.PROJECT/'.local') as folder:
                root = Path(folder)
                (root/'assets').mkdir()
                if data is not None:(root/'assets/runtime-provenance.json').write_text(data, encoding='utf-8')
                output=root/'.local/runtime-provenance-check.json'
                output.parent.mkdir();output.write_text('{"success":true}', encoding='utf-8')
                with patch.object(provenance,'PROJECT',root), \
                     patch.object(provenance.urllib.request,'build_opener') as opener, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(provenance.main(['--online']),2)
                    opener.assert_not_called()
                report=json.loads(output.read_text())
                self.assertFalse(report['success'])
                self.assertIn('error',report)
