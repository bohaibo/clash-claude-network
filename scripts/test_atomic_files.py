"""Offline file replacement retry and preservation checks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import atomic_files
import client


class AtomicFiles(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='atomic-file-test-')
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'value.new'
        self.target = Path(temp.name) / 'value.json'
        self.source.write_text('new', encoding='utf-8')
        self.target.write_text('original', encoding='utf-8')

    def test_transient_access_failure_retries_without_deleting_target(self):
        real_replace = Path.replace
        attempts = []
        def replace(source, target):
            attempts.append(1)
            self.assertEqual(self.target.read_text(), 'original')
            if len(attempts) < 3:
                raise PermissionError('sharing fixture')
            return real_replace(source, target)
        with patch.object(Path, 'replace', replace), patch.object(atomic_files.time, 'sleep'):
            atomic_files.replace_file(self.source, self.target)
        # Windows can add genuine sharing failures after the two injected ones.
        self.assertGreaterEqual(len(attempts), 3)
        self.assertEqual(self.target.read_text(), 'new')

    def test_permanent_failure_preserves_both_files(self):
        with patch.object(Path, 'replace', side_effect=PermissionError('locked')) as replace, \
             patch.object(atomic_files.time, 'sleep'):
            with self.assertRaises(PermissionError):
                atomic_files.replace_file(self.source, self.target)
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(self.target.read_text(), 'original')
        self.assertEqual(self.source.read_text(), 'new')

    def test_unrelated_error_is_not_retried(self):
        with patch.object(Path, 'replace', side_effect=FileNotFoundError('missing')) as replace:
            with self.assertRaises(FileNotFoundError):
                atomic_files.replace_file(self.source, self.target)
        self.assertEqual(replace.call_count, 1)
