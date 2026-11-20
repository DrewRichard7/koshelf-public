"""Apple Books: reading a library snapshot, matching, progress in sync, and adding books."""
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app
import apple_books
from test_sync_review import settings

CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'


def epub(path, title, author):
    path.parent.mkdir(parents=True, exist_ok=True)
    opf = (f'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata></package>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', CONTAINER)
        archive.writestr('OPS/book.opf', opf)
    return path


def books_library(container, rows):
    """A minimal Books database: (title, author, progress, finished, opened, path, store)."""
    folder = container / 'BKLibrary'
    folder.mkdir(parents=True)
    connection = sqlite3.connect(folder / 'BKLibrary-1-100.sqlite')
    connection.execute('CREATE TABLE ZBKLIBRARYASSET (ZASSETID TEXT, ZTITLE TEXT, ZAUTHOR TEXT, ZREADINGPROGRESS REAL, '
                       'ZISFINISHED INTEGER, ZLASTOPENDATE REAL, ZPATH TEXT, ZSTOREID TEXT, ZCONTENTTYPE INTEGER)')
    connection.executemany('INSERT INTO ZBKLIBRARYASSET VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)',
                           [(f'id{index}', *row) for index, row in enumerate(rows)])
    connection.commit()
    connection.close()


class MatchingTests(unittest.TestCase):
    def test_titles_and_authors_match_across_formats(self):
        matcher = apple_books.Matcher([
            {'title': 'The Hobbit: or There and Back Again', 'author': 'J. R. R. Tolkien', 'progress': 0.5, 'finished': False, 'opened': 2},
            {'title': 'Hobbit', 'author': 'Someone Else', 'progress': 0.9, 'finished': False, 'opened': 3},
        ])
        same, title_only = matcher.match('The Hobbit', 'Tolkien, J. R. R.')
        self.assertEqual([book['author'] for book in same], ['J. R. R. Tolkien'])
        self.assertEqual([book['author'] for book in title_only], ['Someone Else'])
        self.assertEqual(matcher.progress('Hobbit', 'J.R.R. Tolkien'), {'progress': 0.5, 'finished': False})

    def test_co_authors_translators_and_subtitles(self):
        matcher = apple_books.Matcher([
            {'title': 'A Memory of Light', 'author': 'Robert Jordan', 'progress': 0, 'finished': False, 'opened': 1},
            {'title': 'Star Wars', 'author': 'Timothy Zahn', 'progress': 0, 'finished': False, 'opened': 1},
            {'title': 'Inferno', 'author': 'Brown, Dan', 'progress': 0, 'finished': False, 'opened': 1},
            {'title': "Ender's Game", 'author': 'Orson Scott Card', 'progress': 0, 'finished': False, 'opened': 1},
        ])
        self.assertTrue(matcher.match('A Memory of Light', 'Robert Jordan, Brandon Sanderson')[0])
        self.assertTrue(matcher.match("Ender's Game", 'Card, Orson Scott')[0])
        self.assertEqual(matcher.match("Star Wars: Queen's Shadow", 'E. K. Johnston'), ([], []))
        same, title_only = matcher.match('Inferno', 'Dante Alighieri')
        self.assertEqual((same, [book['author'] for book in title_only]), ([], ['Brown, Dan']))

    def test_progress_uses_the_latest_copy_and_any_finish(self):
        books = [{'title': 'Dracula', 'author': 'Bram Stoker', 'progress': 0.2, 'finished': False, 'opened': 1},
                 {'title': 'Dracula', 'author': 'Bram Stoker', 'progress': 0.6, 'finished': False, 'opened': 5}]
        self.assertEqual(apple_books.Matcher(books).progress('Dracula', 'Bram Stoker')['progress'], 0.6)
        books[0]['finished'] = True
        self.assertEqual(apple_books.Matcher(books).progress('Dracula', 'Bram Stoker'), {'progress': 1.0, 'finished': True})


class SnapshotTests(unittest.TestCase):
    def test_library_is_read_from_a_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            container = Path(folder)
            books_library(container, [('Dracula', 'Bram Stoker', 0.25, None, 600000000.0, '/books/Dracula.epub', None),
                                      ('Dracula', 'Bram Stoker', 0.0, 1, None, None, '395541616')])
            before = sorted(path.stat().st_mtime_ns for path in container.rglob('*'))
            books = apple_books.read_library(container)
            self.assertEqual([(book['progress'], book['finished'], book['added'], book['store']) for book in books],
                             [(0.25, False, True, False), (1.0, True, False, True)])
            self.assertEqual(sorted(path.stat().st_mtime_ns for path in container.rglob('*')), before)

    def test_missing_permission_is_reported(self):
        with patch.object(Path, 'glob', side_effect=PermissionError('Operation not permitted')):
            with self.assertRaises(apple_books.AccessDenied):
                apple_books.library_database(Path('/nonexistent'))


class AddToBooksTests(unittest.TestCase):
    def test_copies_without_replacing(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = epub(folder / 'library/Paradiso.epub', 'Paradiso', 'Dante')
            books = folder / 'books'
            books.mkdir()
            (books / 'Paradiso.epub').write_bytes(b'existing')
            added = apple_books.add_to_books(source, app.rename_exclusive, app.open_regular, books)
            self.assertEqual(added.name, 'Paradiso (2).epub')
            self.assertEqual(added.read_bytes(), source.read_bytes())
            self.assertEqual((books / 'Paradiso.epub').read_bytes(), b'existing')
            self.assertEqual(sorted(path.name for path in books.iterdir()), ['Paradiso (2).epub', 'Paradiso.epub'])

    def test_symbolic_links_are_never_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            secret = folder / 'secret.txt'
            secret.write_text('private key')
            (folder / 'books').mkdir()
            link = folder / 'Book.epub'
            link.symlink_to(secret)
            with self.assertRaises((OSError, ValueError)):
                apple_books.add_to_books(link, app.rename_exclusive, app.open_regular, folder / 'books')
            self.assertEqual(list((folder / 'books').iterdir()), [])


class ReadingPositionTests(unittest.TestCase):
    def test_position_moves_and_highlights_stay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Book.sdr/metadata.epub.lua'
            path.parent.mkdir()
            path.write_bytes(b'-- header\nreturn {\n    ["annotations"] = {\n        [1] = {\n            ["pos0"] = "/body/p[3]",\n        },\n    },\n'
                             b'    ["last_xpointer"] = "/body/DocFragment[2]/p[4]",\n    ["percent_finished"] = 0.25,\n}\n')
            app.write_reading_position(path, 0.62)
            data = path.read_bytes()
            self.assertNotIn(b'last_xpointer', data)
            self.assertIn(b'["pos0"] = "/body/p[3]"', data)
            self.assertIn(b'    ["percent_finished"] = 0.62,', data)
            self.assertIn(b'    ["last_percent"] = 0.62,', data)
            self.assertEqual(app.parse_koreader_reading(path)['readingProgress'], 0.62)

    def test_new_settings_file_for_a_book_never_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Book.sdr/metadata.epub.lua'
            app.write_reading_position(path, 0.4)
            self.assertEqual(app.parse_koreader_reading(path)['readingProgress'], 0.4)


class BooksFixture(unittest.TestCase):
    """A Kindle and Mac library with Dracula, and an empty Books container."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.kindle, self.mac, self.container = base / 'kindle', base / 'mac', base / 'books'
        self.kindle.mkdir()
        self.mac.mkdir()
        for target in (patch.object(app, 'JOURNAL', base / 'state' / 'moves.jsonl'),
                       patch.object(app, 'mac_library', return_value=self.mac),
                       patch.object(app, 'APPLE_BOOKS_ENABLED', True),
                       patch.object(app, 'APPLE_BOOKS', {'revision': None, 'matcher': None}),
                       patch.object(apple_books, 'CONTAINER', self.container),
                       patch.object(apple_books, 'available', return_value=True)):
            target.start()
            self.addCleanup(target.stop)
        app.AUTO_CONNECT = False
        app.select_library(self.mac)
        app.save_settings({'appleBooksProgress': True})  # The user turned on Apple Books progress.
        for root in (self.kindle, self.mac):
            epub(root / 'Dracula.epub', 'Dracula', 'Bram Stoker')


class BooksSyncTests(BooksFixture):
    def test_books_further_along_moves_the_kindle_and_the_mac(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.62, None, 1.0, '/x/Dracula.epub', None)])
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        kindle.parent.mkdir()
        kindle.write_bytes(settings(0.30).replace(b'return {\n', b'return {\n    ["last_xpointer"] = "/body/p[9]",\n'))
        mac = self.mac / 'Dracula.sdr/metadata.epub.lua'
        mac.parent.mkdir()
        mac.write_bytes(kindle.read_bytes())
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['appleBooks'], 'ok')
        [card] = preview['readingProgress']
        self.assertEqual((card['winner'], card['books'], card['kindle'], card['note']), ('books', 0.62, 0.30, 'books'))
        result = app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual(result['kindle']['readingProgress'], 1)
        self.assertEqual(app.parse_koreader_reading(kindle)['readingProgress'], 0.62)
        self.assertEqual(mac.read_bytes(), kindle.read_bytes())
        # Nothing left to do, and the Kindle change can be undone.
        self.assertEqual(app.sync_libraries(self.kindle)['readingProgress'], [])
        reading = [a for a in app.sync_history()[0]['actions'] if a['kind'] == 'reading' and a['side'] == 'Kindle']
        with patch.object(app, 'kindle_library', return_value=self.kindle):
            app.undo_sync_action(app.sync_history()[0]['id'], reading[0]['id'])
        self.assertIn(b'last_xpointer', kindle.read_bytes())

    def test_book_never_opened_on_the_kindle_gets_a_reading_folder(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.6, None, 1.0, '/x/Dracula.epub', None)])
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'][0]['kindle'], 0.0)
        result = app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual((result['kindle']['readingProgress'], result['kindle']['failed']), (1, 0))
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        self.assertEqual(app.parse_koreader_reading(kindle)['readingProgress'], 0.6)
        # The next sync brings the new reading file to the Mac and has nothing else to do.
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'], [])
        app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual((self.mac / 'Dracula.sdr/metadata.epub.lua').read_bytes(), kindle.read_bytes())

    def test_finished_in_books_for_a_book_never_opened(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.3, 1, 1.0, '/x/Dracula.epub', None)])
        preview = app.sync_libraries(self.kindle)
        result = app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual(result['kindle']['failed'], 0)
        self.assertEqual(app.parse_koreader_reading(self.kindle / 'Dracula.sdr/metadata.epub.lua')['readingStatus'], 'complete')

    def test_finished_in_books_marks_the_kindle_finished(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.4, 1, 1.0, '/x/Dracula.epub', None)])
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        kindle.parent.mkdir()
        kindle.write_bytes(settings(0.5))
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'][0]['note'], 'books-finished')
        app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual(app.parse_koreader_reading(kindle)['readingStatus'], 'complete')
        self.assertIn(b'["percent_finished"] = 0.5', kindle.read_bytes())

    def test_kindle_ahead_of_books_is_left_alone(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.2, None, 1.0, '/x/Dracula.epub', None)])
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        kindle.parent.mkdir()
        kindle.write_bytes(settings(0.5))
        self.assertEqual(app.sync_libraries(self.kindle)['readingProgress'], [])

    def test_without_access_sync_still_works(self):
        with patch.object(apple_books, 'database_revision', side_effect=apple_books.AccessDenied('no')):
            preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['appleBooks'], 'needs-access')

    def test_shelf_shows_books_progress(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.62, None, 1.0, '/x/Dracula.epub', None)])
        self.assertEqual(app.scan()['books'][0]['booksProgress'], 0.62)



class BooksReviewFindingTests(BooksFixture):
    def test_a_book_finished_on_the_mac_stays_finished(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.6, None, 1.0, '/x/Dracula.epub', None)])
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        kindle.parent.mkdir()
        kindle.write_bytes(settings(0.3))
        mac = self.mac / 'Dracula.sdr/metadata.epub.lua'
        mac.parent.mkdir()
        mac.write_bytes(settings(0.3, 'complete'))
        preview = app.sync_libraries(self.kindle)
        self.assertEqual([card['winner'] for card in preview['readingProgress']], ['mac'])
        app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual(app.parse_koreader_reading(mac)['readingStatus'], 'complete')
        self.assertEqual(app.parse_koreader_reading(kindle)['readingStatus'], 'complete')

    def test_mac_only_reading_file_is_never_replaced(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.5, None, 1.0, '/x/Dracula.epub', None)])
        mac = self.mac / 'Dracula.sdr/metadata.epub.lua'
        mac.parent.mkdir()
        highlights = settings(0.9).replace(b'return {\n', b'return {\n    ["annotations"] = {"mac highlight"},\n')
        mac.write_bytes(highlights)
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'], [])
        app.sync_libraries(self.kindle, 'copy', preview['review'])
        self.assertEqual(mac.read_bytes(), highlights)

    def test_undo_removes_a_reading_file_sync_created(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.4, None, 1.0, '/x/Dracula.epub', None)])
        preview = app.sync_libraries(self.kindle)
        self.assertEqual(preview['readingProgress'][0]['note'], 'books-new')
        app.sync_libraries(self.kindle, 'copy', preview['review'])
        kindle = self.kindle / 'Dracula.sdr/metadata.epub.lua'
        self.assertTrue(kindle.is_file())
        run = app.sync_history()[0]
        [created] = [a for a in run['actions'] if a['kind'] == 'reading']
        self.assertIsNone(created['previous'])
        with patch.object(app, 'kindle_library', return_value=self.kindle):
            app.undo_sync_action(run['id'], created['id'])
        self.assertFalse(kindle.exists())

    def test_saved_backup_copies_get_no_books_progress(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.4, None, 1.0, '/x/Dracula.epub', None)])
        epub(self.kindle / 'Dracula (backup 0123456789abcdef).epub', 'Dracula', 'Bram Stoker')
        paths = [card['path'] for card in app.sync_libraries(self.kindle)['readingProgress']]
        self.assertEqual(paths, ['Dracula.sdr/metadata.epub.lua'])

    def test_nothing_is_read_until_the_user_opts_in(self):
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.62, None, 1.0, '/x/Dracula.epub', None)])
        app.save_settings({'appleBooksProgress': False})
        with patch.object(apple_books, 'read_library', side_effect=AssertionError('read without opt-in')), \
                patch.object(apple_books, 'available', side_effect=AssertionError('looked without opt-in')):
            self.assertNotIn('booksProgress', app.scan()['books'][0])
            self.assertEqual(app.sync_libraries(self.kindle)['appleBooks'], 'off')

    def test_non_latin_titles_and_authors_match(self):
        matcher = apple_books.Matcher([{'title': 'Мастер и Маргарита', 'author': 'Михаил Булгаков',
                                        'progress': 0.3, 'finished': False, 'opened': 1}])
        self.assertEqual(matcher.progress('Мастер и Маргарита', 'Булгаков, Михаил')['progress'], 0.3)
        self.assertTrue(matcher.match('Crème Brûlée', 'Zoë')[0] == [])


class AddToBooksReviewTests(BooksFixture):
    def test_review_lists_missing_and_title_only_books_and_adds_chosen_ones(self):
        epub(self.mac / 'Shelf/Paradiso.epub', 'Paradiso', 'Dante Alighieri')
        epub(self.mac / 'Shelf/Emma.epub', 'Emma', 'Jane Austen')
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.0, None, None, None, '1'),
                                       ('Emma', 'Someone Else', 0.0, None, None, None, None)])
        icloud = self.container.parent / 'icloud'
        icloud.mkdir()
        with patch.object(apple_books, 'ICLOUD_BOOKS', icloud):
            preview = app.apple_books_preview()
            self.assertEqual(preview['present'], 1)
            self.assertEqual([row['title'] for row in preview['missing']], ['Paradiso'])
            self.assertEqual([row['title'] for row in preview['ambiguous']], ['Emma'])
            with self.assertRaisesRegex(ValueError, 'current review'):
                app.add_to_apple_books({'review': preview['review'], 'paths': ['Dracula.epub']})
            result = app.add_to_apple_books({'review': preview['review'], 'paths': ['Shelf/Paradiso.epub']})
        self.assertEqual(result, {'added': 1, 'failed': 0, 'errors': []})
        self.assertEqual((icloud / 'Paradiso.epub').read_bytes(), (self.mac / 'Shelf/Paradiso.epub').read_bytes())
        # Until Apple Books lists it, the copied book isn't offered again.
        with patch.object(apple_books, 'ICLOUD_BOOKS', icloud):
            again = app.apple_books_preview()
            self.assertEqual((again['waiting'], [row['title'] for row in again['missing']]), (1, []))
            # Removing it in Apple Books removes the file, so it can be added again.
            (icloud / 'Paradiso.epub').unlink()
            self.assertEqual([row['title'] for row in app.apple_books_preview()['missing']], ['Paradiso'])

    def test_review_works_without_turning_on_progress(self):
        app.save_settings({'appleBooksProgress': False})
        books_library(self.container, [('Dracula', 'Bram Stoker', 0.5, None, 1.0, None, '1')])
        preview = app.apple_books_preview()
        self.assertEqual((preview['state'], preview['present']), ('ok', 1))
        self.assertFalse(app.settings()['appleBooksProgress'])
        self.assertNotIn('booksProgress', app.scan()['books'][0])

    def test_review_reports_missing_permission(self):
        with patch.object(apple_books, 'database_revision', side_effect=apple_books.AccessDenied('no')):
            self.assertEqual(app.apple_books_preview(), {'state': 'needs-access'})


if __name__ == '__main__':
    unittest.main()
