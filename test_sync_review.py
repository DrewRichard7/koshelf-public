"""Sync review: renamed books and KOReader reading progress."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


def settings(fraction, status='reading'):
    return (f'return {{\n    ["percent_finished"] = {fraction},\n'
            f'    ["summary"] = {{\n        ["status"] = "{status}",\n    }},\n}}\n').encode()


class SyncReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.kindle = self.base / 'kindle'
        self.mac = self.base / 'mac'
        self.kindle.mkdir()
        self.mac.mkdir()
        for target in (patch.object(app, 'JOURNAL', self.base / 'state' / 'moves.jsonl'),
                       patch.object(app, 'mac_library', return_value=self.mac)):
            target.start()
            self.addCleanup(target.stop)
        app.AUTO_CONNECT = False
        app.select_library(self.mac)

    def put(self, root, path, content):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def sync(self, **choices):
        preview = app.sync_libraries(self.kindle)
        return preview, app.sync_libraries(self.kindle, review=preview['review'], **choices)

    def test_renamed_book_is_a_choice_not_a_new_file(self):
        self.put(self.kindle, 'Old_Name.epub', b'same book')
        self.put(self.kindle, 'Old_Name.sdr/metadata.epub.lua', settings(0.4))
        self.put(self.mac, 'New Name.epub', b'same book')
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['toMacPaths'], [])
        self.assertEqual(preview['toKindle'], 0)
        [group] = preview['moveGroups']
        self.assertEqual((group['kindle'], group['mac'], group['single']), (['Old_Name.epub'], ['New Name.epub'], True))
        with self.assertRaisesRegex(ValueError, 'Choose a name'):
            app.sync_libraries(self.kindle, 'align', preview['review'], moves={})
        app.sync_libraries(self.kindle, 'align', preview['review'], moves={group['id']: 'backup'})
        # The Kindle copy and its reading folder take the Mac name; nothing is duplicated.
        self.assertEqual(sorted(p.name for p in self.kindle.iterdir()), ['New Name.epub', 'New Name.sdr'])
        self.assertEqual(sorted(p.name for p in self.mac.iterdir()), ['New Name.epub', 'New Name.sdr'])
        self.assertEqual((self.mac / 'New Name.sdr/metadata.epub.lua').read_bytes(), settings(0.4))
        self.assertFalse(app.sync_libraries(self.kindle)['moveGroups'])

    def test_kindle_name_can_win(self):
        self.put(self.kindle, 'Kindle Name.epub', b'same book')
        self.put(self.mac, 'Mac Name.epub', b'same book')
        preview = app.sync_libraries(self.kindle)
        [group] = preview['moveGroups']
        app.sync_libraries(self.kindle, 'align', preview['review'], moves={group['id']: 'kindle'})
        self.assertEqual([p.name for p in self.mac.iterdir()], ['Kindle Name.epub'])
        self.assertEqual([p.name for p in self.kindle.iterdir()], ['Kindle Name.epub'])

    def test_further_kindle_progress_updates_the_mac(self):
        self.put(self.kindle, 'Book.epub', b'book')
        self.put(self.mac, 'Book.epub', b'book')
        self.put(self.kindle, 'Book.sdr/metadata.epub.lua', settings(0.30))
        self.put(self.kindle, 'Book.sdr/metadata.epub.lua.old', settings(0.29))
        old_mac = self.put(self.mac, 'Book.sdr/metadata.epub.lua', settings(0.27))
        self.put(self.mac, 'Book.sdr/metadata.epub.lua.old', settings(0.26))
        preview, result = self.sync(choice='copy')
        # KOReader's .old backup is synced quietly, not listed.
        self.assertEqual(preview['readingProgress'], [
            {'path': 'Book.sdr/metadata.epub.lua', 'book': 'Book', 'winner': 'kindle', 'kindle': 0.30, 'mac': 0.27}])
        self.assertEqual(preview['versionDifferenceCount'], 0)
        self.assertEqual(result['kindle']['readingProgress'], 1)
        self.assertEqual(old_mac.read_bytes(), settings(0.30))
        self.assertEqual((self.mac / 'Book.sdr/metadata.epub.lua.old').read_bytes(), settings(0.29))
        saved = [p.read_bytes() for p in (self.base / 'state/sync-versions/Book.sdr').iterdir()]
        self.assertIn(settings(0.27), saved)
        self.assertEqual(app.sync_libraries(self.kindle)['readingProgress'], [])

    def test_book_finished_on_mac_wins(self):
        self.put(self.kindle, 'Book.epub', b'book')
        self.put(self.mac, 'Book.epub', b'book')
        kindle = self.put(self.kindle, 'Book.sdr/metadata.epub.lua', settings(0.60))
        self.put(self.mac, 'Book.sdr/metadata.epub.lua', settings(0.50, 'complete'))
        preview, result = self.sync(choice='copy')
        self.assertEqual(preview['readingProgress'][0]['winner'], 'mac')
        self.assertEqual(kindle.read_bytes(), settings(0.50, 'complete'))
        saved = [p.read_bytes() for p in (self.base / 'state/sync-versions/Book.sdr').iterdir()]
        self.assertIn(settings(0.60), saved)

    def test_unreadable_progress_is_still_a_version_difference(self):
        self.put(self.kindle, 'Book.epub', b'book')
        self.put(self.mac, 'Book.epub', b'book')
        self.put(self.kindle, 'Book.sdr/metadata.epub.lua', b'return {}')
        self.put(self.mac, 'Book.sdr/metadata.epub.lua', settings(0.5))
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'], [])
        self.assertEqual(preview['versionDifferencePaths'], ['Book.sdr/metadata.epub.lua'])


if __name__ == '__main__':
    unittest.main()
