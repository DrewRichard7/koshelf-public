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

    def test_keep_both_leaves_that_book_and_syncs_the_rest(self):
        self.put(self.kindle, 'Old Name.epub', b'same book')
        self.put(self.mac, 'New Name.epub', b'same book')
        self.put(self.kindle, 'Another.epub', b'another book')
        preview = app.sync_libraries(self.kindle)
        [group] = preview['moveGroups']
        app.sync_libraries(self.kindle, 'align', preview['review'], moves={group['id']: 'both'})
        self.assertEqual(sorted(p.name for p in self.mac.iterdir()), ['Another.epub', 'New Name.epub'])
        self.assertEqual(sorted(p.name for p in self.kindle.iterdir()), ['Another.epub', 'Old Name.epub'])

    def test_several_identical_copies_sync_as_ordinary_files(self):
        self.put(self.kindle, 'A.epub', b'same book')
        self.put(self.kindle, 'C.epub', b'same book')
        self.put(self.kindle, 'C.sdr/metadata.epub.lua', settings(0.4))
        self.put(self.mac, 'A.epub', b'same book')
        preview = app.sync_libraries(self.kindle)
        self.assertFalse(preview['moveGroups'][0]['single'])
        self.assertIn('C.epub', preview['toMacPaths'])
        result = app.sync_libraries(self.kindle, 'align', preview['review'], moves={})
        self.assertEqual(result['mac']['failed'], 0)
        self.assertTrue((self.mac / 'C.epub').is_file())
        self.assertTrue((self.mac / 'C.sdr/metadata.epub.lua').is_file())

    def test_more_than_a_hundred_renames_can_all_be_chosen(self):
        for index in range(105):
            self.put(self.kindle, f'Old {index}.epub', f'book {index}'.encode())
            self.put(self.mac, f'New {index}.epub', f'book {index}'.encode())
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(len(preview['moveGroups']), 105)
        moves = {group['id']: 'backup' for group in preview['moveGroups']}
        result = app.sync_libraries(self.kindle, 'align', preview['review'], moves=moves)
        self.assertEqual(result['mac']['moved'], 105)

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
            {'path': 'Book.sdr/metadata.epub.lua', 'book': 'Book', 'winner': 'kindle', 'kindle': 0.30, 'mac': 0.27,
             'books': None, 'note': None}])
        self.assertEqual(preview['versionDifferenceCount'], 0)
        self.assertEqual(result['kindle']['readingProgress'], 1)
        self.assertEqual(old_mac.read_bytes(), settings(0.30))
        self.assertEqual((self.mac / 'Book.sdr/metadata.epub.lua.old').read_bytes(), settings(0.29))
        saved = [p.read_bytes() for p in (self.base / 'state/sync-versions/Book.sdr').iterdir()]
        self.assertIn(settings(0.27), saved)
        self.assertEqual(app.sync_libraries(self.kindle)['readingProgress'], [])

    def test_book_finished_on_mac_keeps_kindle_highlights(self):
        self.put(self.kindle, 'Book.epub', b'book')
        self.put(self.mac, 'Book.epub', b'book')
        highlights = settings(0.60).replace(b'return {\n', b'return {\n    ["annotations"] = {"kindle highlight"},\n')
        kindle = self.put(self.kindle, 'Book.sdr/metadata.epub.lua', highlights)
        mac = self.put(self.mac, 'Book.sdr/metadata.epub.lua', settings(0.50, 'complete'))
        preview, result = self.sync(choice='copy')
        self.assertEqual((preview['readingProgress'][0]['winner'], preview['readingProgress'][0]['note']), ('mac', 'finished'))
        # Only the status moves to the Kindle; its position and highlights stay.
        updated = kindle.read_bytes()
        self.assertIn(b'kindle highlight', updated)
        self.assertIn(b'["percent_finished"] = 0.6', updated)
        self.assertIn(b'["status"] = "complete"', updated)
        self.assertEqual(mac.read_bytes(), updated)
        self.assertEqual(app.sync_libraries(self.kindle)['readingProgress'], [])
        # Both replacements can be undone.
        run = app.sync_history()[0]
        reading = [action for action in run['actions'] if action['kind'] == 'reading']
        self.assertEqual(sorted(action['side'] for action in reading), ['Kindle', 'Mac'])
        with patch.object(app, 'kindle_library', return_value=self.kindle):
            for action in reading:
                app.undo_sync_action(run['id'], action['id'])
        self.assertEqual(kindle.read_bytes(), highlights)
        self.assertEqual(mac.read_bytes(), settings(0.50, 'complete'))

    def test_stale_mac_lead_does_not_replace_the_kindle(self):
        self.put(self.kindle, 'Book.epub', b'book')
        self.put(self.mac, 'Book.epub', b'book')
        kindle = self.put(self.kindle, 'Book.sdr/metadata.epub.lua', settings(0.10))
        mac = self.put(self.mac, 'Book.sdr/metadata.epub.lua', settings(0.80))
        preview, _ = self.sync(choice='copy')
        self.assertEqual((preview['readingProgress'][0]['winner'], preview['readingProgress'][0]['note']), ('kindle', 'mac-ahead'))
        self.assertEqual(kindle.read_bytes(), settings(0.10))
        self.assertEqual(mac.read_bytes(), settings(0.10))

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
