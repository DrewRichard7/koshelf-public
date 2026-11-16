import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from library_fs import open_state_text, private_state_subdirectory


class LibraryFilesystemTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        journal = patch.object(app, 'JOURNAL', self.base / '.state' / 'moves.jsonl')
        journal.start()
        self.addCleanup(journal.stop)

    def test_scan_ignores_epub_named_fifo(self):
        os.mkfifo(self.base / 'Blocked.epub')
        with patch.object(app, 'ROOT', self.base), patch.object(app, 'ROOT_ID', None):
            app.select_library(self.base)
            with patch.object(app, 'metadata', side_effect=AssertionError('FIFO reached EPUB parser')):
                self.assertEqual(app.scan()['books'], [])

    def test_state_logs_are_private_and_legacy_modes_are_narrowed(self):
        path = self.base / 'state' / 'log.jsonl'
        with open_state_text(path, 'a') as stream:
            stream.write('{}\n')
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        path.chmod(0o644)
        with open_state_text(path) as stream:
            self.assertEqual(stream.read(), '{}\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_state_log_writes_do_not_append_through_symlinks(self):
        sentinel = self.base / 'outside.txt'
        sentinel.write_text('original')
        path = self.base / 'log.jsonl'
        path.symlink_to(sentinel)
        with self.assertRaises(OSError):
            open_state_text(path, 'a')
        self.assertEqual(sentinel.read_text(), 'original')

    def test_managed_recovery_directory_does_not_follow_symlinks(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.base / 'recovery').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            private_state_subdirectory(self.base, 'recovery', 'change-id')
        self.assertEqual(list(outside.iterdir()), [])
