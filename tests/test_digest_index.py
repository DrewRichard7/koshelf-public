import os
from pathlib import Path
import tempfile
import time
import unittest

import digest_index


class DigestIndexTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.state = self.base / 'state' / 'sync-digests.json'
        self.book = self.base / 'Book.epub'
        self.book.write_bytes(b'first')
        self.reads = []

    def tearDown(self):
        self.directory.cleanup()

    def index(self):
        def compute(path):
            self.reads.append(path)
            return format(len(self.reads), '064x')
        return digest_index.DigestIndex(lambda: self.state, compute)

    def test_unchanged_file_is_not_reread(self):
        index = self.index()
        first = index.digest(self.book, self.book.stat())
        self.assertEqual(index.digest(self.book, self.book.stat()), first)
        self.assertEqual(len(self.reads), 1)

    def test_changed_file_is_reread(self):
        index = self.index()
        index.digest(self.book, self.book.stat())
        self.book.write_bytes(b'second, longer')
        index.digest(self.book, self.book.stat())
        self.assertEqual(len(self.reads), 2)

    def test_replaced_file_with_same_size_and_time_is_reread(self):
        index = self.index()
        info = self.book.stat()
        index.digest(self.book, info)
        replacement = self.base / 'new'
        replacement.write_bytes(b'other')
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(replacement, self.book)
        index.digest(self.book, self.book.stat())
        self.assertEqual(len(self.reads), 2)

    def test_saved_index_survives_relaunch_privately(self):
        index = self.index()
        first = index.digest(self.book, self.book.stat())
        index.save()
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        again = self.index()
        self.assertEqual(again.digest(self.book, self.book.stat()), first)
        self.assertEqual(len(self.reads), 1)

    def test_corrupt_index_is_ignored(self):
        self.state.parent.mkdir(mode=0o700)
        self.state.write_text('{not json')
        os.chmod(self.state, 0o600)
        self.index().digest(self.book, self.book.stat())
        self.assertEqual(len(self.reads), 1)

    def test_stale_entries_are_pruned(self):
        index = self.index()
        index.digest(self.book, self.book.stat())
        index.entries[str(self.book)][5] = int(time.time()) - digest_index.MAX_AGE - 1
        index.save()
        self.assertEqual(self.index().digest(self.book, self.book.stat()), format(2, '064x'))

    def test_cancel_stops_comparison(self):
        index = self.index()
        index.cancel.set()
        with self.assertRaises(digest_index.Cancelled):
            index.digest(self.book, self.book.stat())
        self.assertEqual(self.reads, [])


if __name__ == '__main__':
    unittest.main()
