import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class EmptyFolderSyncTests(unittest.TestCase):
    def test_review_propagates_removed_empty_folder_and_preserves_new_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            mac, kindle = base / 'mac', base / 'kindle'
            mac.mkdir()
            kindle.mkdir()
            for root in (mac, kindle):
                (root / 'Removed').mkdir()
            (kindle / 'New').mkdir()
            info = mac.stat()
            with patch.multiple(app, ROOT=mac, ROOT_ID=(info.st_dev, info.st_ino),
                                JOURNAL=base / 'state' / 'moves.jsonl'), patch.object(app, 'mac_library', return_value=mac):
                app.remove_folder('Removed')
                review = app.sync_libraries(kindle)
                self.assertEqual(review['removeEmptyFolders'], ['Removed'])
                self.assertEqual(review['incomingEmptyFolders'], ['New'])
                book = kindle / 'Removed' / 'Book.epub'
                book.write_bytes(b'ebook')
                with self.assertRaisesRegex(ValueError, 'changed during review'):
                    app.sync_libraries(kindle, 'copy', review['review'])
                self.assertTrue(book.exists())
                book.unlink()
                review = app.sync_libraries(kindle)
                result = app.sync_libraries(kindle, 'copy', review['review'])
                self.assertEqual(result['kindle']['removedEmptyFolders'], 1)
                self.assertFalse((kindle / 'Removed').exists())
                self.assertFalse((mac / 'Removed').exists())
                self.assertTrue((mac / 'New').is_dir())
