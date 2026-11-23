import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class ReadingStatusTests(unittest.TestCase):
    def test_mac_choice_syncs_only_the_status_to_the_matching_kindle_book(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            mac, kindle = base / 'mac', base / 'kindle'
            mac.mkdir()
            kindle.mkdir()
            for root in (mac, kindle):
                (root / 'Book.epub').write_bytes(b'ebook')
            book = kindle / 'Book.epub'
            sidecar = book.with_suffix('.sdr')
            sidecar.mkdir()
            settings = sidecar / 'metadata.epub.lua'
            original = (b'return {\n    ["percent_finished"] = 0.21,\n'
                        b'    ["summary"] = {\n        ["status"] = "reading",\n    },\n'
                        b'    ["annotations"] = { "fixture" },\n}\n')
            settings.write_bytes(original)
            with patch.multiple(app, ROOT=mac, ROOT_ID=(mac.stat().st_dev, mac.stat().st_ino),
                                JOURNAL=base / 'state' / 'moves.jsonl'), patch.object(app, 'mac_library', return_value=mac):
                result = app.set_reading_status('Book.epub', 'abandoned')
                self.assertTrue(result['readingStatusPending'])
                self.assertEqual(settings.read_bytes(), original)
                review = app.sync_libraries(kindle)
                self.assertEqual(review['readingStatuses'][0]['status'], 'abandoned')
                result = app.sync_libraries(kindle, 'copy', review['review'])
                self.assertEqual(result['kindle']['readingStatuses'], 1)
                self.assertEqual(settings.read_bytes(), original.replace(b'"reading"', b'"abandoned"'))
                self.assertEqual(app.koreader_reading(book)['readingProgress'], 0.21)
                self.assertEqual(app.reading_status_queue(), {})
