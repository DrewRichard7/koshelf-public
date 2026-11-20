import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app

MAGIC = b'\x00\x05\x16\x07'


class SyncLeftoverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.mac = self.base / 'KoShelf Library'
        self.cloud = self.base / 'iCloud'
        self.state = self.base / 'state'
        for folder in (self.mac, self.cloud):
            folder.mkdir()
        for item in (patch.object(app, 'JOURNAL', self.state / 'moves.jsonl'),
                     patch.object(app, 'mac_library', return_value=self.mac),
                     patch.object(app, 'cleanup_libraries',
                                  side_effect=lambda: {'mac': self.mac, 'icloud': self.cloud})):
            item.start()
            self.addCleanup(item.stop)

    def put(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def backup(self, root, relative, data):
        path = Path(relative)
        name = f'{path.stem} (backup {hashlib.sha256(data).hexdigest()[:16]}){path.suffix}'
        return self.put(root, (path.parent / name).as_posix(), data)

    def test_preview_finds_only_genuine_leftovers(self):
        self.put(self.mac, 'Shelf/Book.epub', b'mac book')
        self.put(self.mac, 'Shelf/._Book.epub', MAGIC + b' attributes')
        self.put(self.mac, 'Shelf/._notes.txt', b'not appledouble')
        self.backup(self.mac, 'Shelf/Book.epub', b'kindle book')
        self.put(self.mac, 'Shelf/Fake (backup 0123456789abcdef).epub', b'hash does not match')
        self.backup(self.mac, 'Shelf/Orphan.epub', b'only copy')
        self.put(self.cloud, 'Book.epub', b'mac book')
        self.backup(self.cloud, 'Book.epub', b'kindle book')
        preview = app.clean_sync_leftovers()
        found = {(item['library'], item['kind'], item['path']) for item in preview['items']}
        self.assertEqual(found, {
            ('mac', 'appledouble', 'Shelf/._Book.epub'),
            ('mac', 'backup', f"Shelf/Book (backup {hashlib.sha256(b'kindle book').hexdigest()[:16]}).epub"),
            ('icloud', 'backup', f"Book (backup {hashlib.sha256(b'kindle book').hexdigest()[:16]}).epub"),
        })
        self.assertEqual([item['path'] for item in preview['kept']],
                         [f"Shelf/Orphan (backup {hashlib.sha256(b'only copy').hexdigest()[:16]}).epub"])
        self.assertTrue((self.mac / 'Shelf/._Book.epub').exists(), 'preview must not change files')

    def test_apply_moves_leftovers_privately_and_keeps_sync_recognition(self):
        book = self.put(self.mac, 'Shelf/Book.epub', b'mac book')
        self.put(self.mac, 'Shelf/._Book.epub', MAGIC + b' attributes')
        copy = self.backup(self.mac, 'Shelf/Book.epub', b'kindle book')
        cloud_copy = self.backup(self.cloud, 'Book.epub', b'kindle book')
        self.put(self.cloud, 'Book.epub', b'mac book')
        orphan = self.backup(self.mac, 'Orphan.epub', b'only copy')
        preview = app.clean_sync_leftovers()
        result = app.clean_sync_leftovers(preview['review'])
        self.assertEqual((result['moved'], result['failed']), (3, 0))
        self.assertEqual(book.read_bytes(), b'mac book')
        self.assertTrue(orphan.exists())
        for gone in (self.mac / 'Shelf/._Book.epub', copy, cloud_copy):
            self.assertFalse(gone.exists())
        digest = hashlib.sha256(b'kindle book').hexdigest()
        # Sync still finds the Kindle version, so the conflict stays settled.
        self.assertEqual(app.saved_sync_version('Shelf/Book.epub', digest, book).read_bytes(), b'kindle book')
        recovery = Path(result['recovery'])
        self.assertEqual((recovery / 'mac/Shelf/._Book.epub').read_bytes(), MAGIC + b' attributes')
        self.assertEqual((recovery / 'icloud' / cloud_copy.name).read_bytes(), b'kindle book')
        self.assertEqual(recovery.stat().st_mode & 0o077, 0)
        self.assertEqual(app.clean_sync_leftovers()['items'], [])

    def test_apply_rejects_stale_review_and_changed_files(self):
        self.put(self.mac, 'Book.epub', b'mac book')
        junk = self.put(self.mac, '._Book.epub', MAGIC)
        preview = app.clean_sync_leftovers()
        self.put(self.mac, '._Other.epub', MAGIC)
        with self.assertRaisesRegex(ValueError, 'changed during review'):
            app.clean_sync_leftovers(preview['review'])
        self.assertTrue(junk.exists())

    def test_symlinked_folders_are_not_followed(self):
        outside = self.base / 'outside'
        self.put(outside, '._Book.epub', MAGIC)
        (self.mac / 'Link').symlink_to(outside)
        self.assertEqual(app.clean_sync_leftovers()['items'], [])


if __name__ == '__main__':
    unittest.main()
