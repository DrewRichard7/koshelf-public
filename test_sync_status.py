from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class SyncStatusTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.kindle, self.mac = base / 'kindle', base / 'mac'
        for root in (self.kindle, self.mac):
            (root / 'Shelf').mkdir(parents=True)
            (root / 'Shelf' / 'Book.epub').write_bytes(b'same')
        self.patches = [patch.object(app, 'JOURNAL', base / 'state' / 'moves.jsonl'),
                        patch.object(app, 'mac_library', return_value=self.mac)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        app.DIGESTS.cancel.clear()
        self.directory.cleanup()

    def test_eject_cancels_a_running_comparison(self):
        app.DIGESTS.cancel.set()
        result = app.kindle_status(self.kindle)
        self.assertTrue(result['cancelled'])
        self.assertNotIn('syncError', result)

    def test_changed_kindle_book_needs_sync_after_cached_check(self):
        self.assertFalse(app.kindle_status(self.kindle)['syncNeeded'])
        (self.kindle / 'Shelf' / 'Book.epub').write_bytes(b'newer')
        self.assertTrue(app.kindle_status(self.kindle)['syncNeeded'])

    def test_symlinked_mac_folder_needs_review(self):
        (self.mac / 'Shelf' / 'Book.epub').unlink()
        (self.mac / 'Shelf').rmdir()
        (self.mac / 'Shelf').symlink_to(self.kindle / 'Shelf')
        self.assertTrue(app.sync_needed(self.kindle, self.mac))


if __name__ == '__main__':
    unittest.main()
