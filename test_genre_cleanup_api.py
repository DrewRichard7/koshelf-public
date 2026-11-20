import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app
from test_app import CONTAINER, OPF


class GenreCleanupPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'books'
        self.root.mkdir()
        self.state = Path(temp.name) / 'state'
        for name, value in {
            'ROOT': None,
            'ROOT_ID': None,
            'CACHE': {},
            'JOURNAL': self.state / 'moves.jsonl',
        }.items():
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        local = patch.object(app, 'mac_library', return_value=self.root)
        local.start()
        self.addCleanup(local.stop)
        app.select_library(self.root)

    def book(self, filename, title=None):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        opf = OPF.replace('</metadata>', '<dc:subject>Sci-Fi</dc:subject></metadata>')
        if title is not None:
            opf = opf.replace('<dc:title>A Test &amp; a Book</dc:title>', title)
            opf = opf.replace('</metadata>', '<meta name="calibre:series" content="Wicked Years"/>'
                              '<meta name="calibre:series_index" content="3.0"/></metadata>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', 'application/epub+zip')
            archive.writestr('OPS/chapter.xhtml', 'original chapter')
            archive.writestr('OPS/images/cover.png', b'\x89PNG\r\n\x1a\nfixture')
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', opf)
        return path

    def test_preview_and_save_only_write_settings(self):
        book = self.book('Book.epub')
        original = book.read_bytes()
        modified = book.stat().st_mtime_ns
        (self.root / 'Broken.epub').write_bytes(b'invalid archive')
        mappings = {'Sci-Fi': 'Speculative Fiction'}
        preview = app.genre_cleanup_preview({'mappings': mappings})
        self.assertEqual((preview['scanned'], preview['skipped']), (2, 1))
        self.assertEqual(preview['books'][0]['proposed'], ['Speculative Fiction'])
        self.assertFalse(self.state.exists())
        saved = app.genre_cleanup_preview({'mappings': mappings, 'saveMappings': True})
        self.assertEqual(app.genre_cleanup_preview({}), saved)
        self.assertEqual(book.read_bytes(), original)
        self.assertEqual(book.stat().st_mtime_ns, modified)
        self.assertEqual([path.name for path in self.state.iterdir()], ['genre-mappings.json'])

    def test_preview_respects_scope_and_excludes_special_paths(self):
        self.book('Root.epub')
        self.book('Fantasy/Nested/Book.epub')
        self.book('Fantasy/.archive/Archived.epub')
        self.book('Fantasy/Book.SDR/Book.epub')
        (self.root / 'Fantasy' / 'Link.epub').symlink_to(self.root / 'Root.epub')
        for request, expected in [
            ({'scope': 'folder', 'folder': ''}, 'Root.epub'),
            ({'scope': 'folder', 'folder': 'Fantasy'}, 'Fantasy/Nested/Book.epub'),
            ({'scope': 'selection', 'paths': ['Root.epub', 'Root.epub']}, 'Root.epub'),
        ]:
            with self.subTest(request=request):
                result = app.genre_cleanup_preview(request)
                self.assertEqual(result['scanned'], 1)
                self.assertEqual([book['path'] for book in result['books']], [expected])
        with self.assertRaises(ValueError):
            app.genre_cleanup_preview({'scope': 'selection', 'paths': ['Fantasy/Link.epub']})

    def test_another_library_cannot_save_mappings(self):
        other = self.root.parent / 'kindle'
        other.mkdir()
        app.select_library(other)
        with self.assertRaisesRegex(ValueError, 'Open Mac Library'):
            app.genre_cleanup_preview({'mappings': {}, 'saveMappings': True})
        self.assertFalse(self.state.exists())

    def test_approved_fields_save_once_with_original_backup_and_audit(self):
        book = self.book('Lion.epub', '<dc:title>A Lion Among Men: Volume Three in the Wicked Years</dc:title>')
        other = self.book('Other.epub')
        sidecar = book.with_suffix('.sdr')
        sidecar.mkdir()
        original, other_original = book.read_bytes(), other.read_bytes()
        preview = app.genre_cleanup_preview({})
        suggestion = preview['books'][0]
        self.assertEqual(suggestion['proposedTitle'], 'A Lion Among Men')
        request = {'path': suggestion['path'], 'revision': suggestion['revision'], 'mappings': {}}
        saved = app.apply_metadata_cleanup({**request, 'title': suggestion['proposedTitle'],
                                            'genres': suggestion['proposed']})
        self.assertEqual(saved['title'], 'A Lion Among Men')
        self.assertEqual(saved['genres'], ['Science Fiction'])
        self.assertEqual(saved['series'], 'Wicked Years')
        self.assertEqual(Path(saved['backup']).read_bytes(), original)
        self.assertEqual(other.read_bytes(), other_original)
        self.assertTrue(sidecar.is_dir())
        with zipfile.ZipFile(book) as archive:
            self.assertEqual(archive.read('OPS/chapter.xhtml'), b'original chapter')
        event = json.loads(app.JOURNAL.read_text().splitlines()[-1])
        self.assertEqual(event['cleanup']['genres'], {'before': ['Sci-Fi'], 'after': ['Science Fiction']})
        self.assertEqual(event['status'], 'completed')
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            app.apply_metadata_cleanup({**request, 'title': suggestion['proposedTitle']})
        rescanned = app.genre_cleanup_preview({})
        self.assertEqual([row['path'] for row in rescanned['books']], ['Other.epub'])

    def test_single_tag_change_applies_without_the_others(self):
        book = self.book('Novel.epub')
        preview = app.genre_cleanup_preview({'scope': 'selection', 'paths': [book.name]})
        row = preview['books'][0]
        request = {'path': book.name, 'revision': row['revision'], 'mappings': {}}
        with self.assertRaisesRegex(ValueError, 'differ'):
            app.apply_metadata_cleanup({**request, 'acceptedChanges': [99]})
        with self.assertRaisesRegex(ValueError, 'at least one'):
            app.apply_metadata_cleanup({**request, 'acceptedChanges': []})
        first = row['changes'][0]['index']
        saved = app.apply_metadata_cleanup({**request, 'acceptedChanges': [first]})
        self.assertEqual(saved['genres'], app.choose_changes(row['current'], row['changes'], [first]))

    def test_previewed_mappings_are_reported_apart_from_saved_ones(self):
        self.book('Novel.epub')
        draft = app.genre_cleanup_preview({'mappings': {'sci-fi': 'Sci-Fi'}})
        self.assertEqual((draft['mappings'], draft['savedMappings']), ({'sci-fi': 'Sci-Fi'}, {}))
        saved = app.genre_cleanup_preview({'mappings': {'sci-fi': 'Sci-Fi'}, 'saveMappings': True})
        self.assertEqual(saved['savedMappings'], {'sci-fi': 'Sci-Fi'})
        self.assertEqual(app.genre_cleanup_preview({})['savedMappings'], {'sci-fi': 'Sci-Fi'})

    def test_ambiguous_titles_remain_intact_when_applying_genres(self):
        cases = (
            '<dc:title>Example: A Real Subtitle</dc:title>',
            '<dc:title>Example: Book 2 of Wicked Years</dc:title>',
            '<dc:title>Example: Book 3 of Another Series</dc:title>',
            '<dc:title id="main">Example: Book 3 of Wicked Years</dc:title>',
            '<dc:title>Example: Book 3 of Wicked Years</dc:title><dc:title>Alternate</dc:title>',
        )
        for index, title in enumerate(cases):
            book = self.book(f'{index}.epub', title)
            preview = app.genre_cleanup_preview({'scope': 'selection', 'paths': [book.name]})
            row = preview['books'][0]
            self.assertIsNone(row['proposedTitle'])
            request = {'path': book.name, 'revision': row['revision'], 'mappings': {}}
            with self.assertRaisesRegex(ValueError, 'differ'):
                app.apply_metadata_cleanup({**request, 'title': 'Example'})
            app.apply_metadata_cleanup({**request, 'genres': row['proposed']})
            with zipfile.ZipFile(book) as archive:
                self.assertIn(title, archive.read('OPS/book.opf').decode())
