import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import app
from test_app import CONTAINER

OPF = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf" version="3.0"><metadata><dc:identifier id="uid">urn:test</dc:identifier><dc:title id="title" xml:lang="ru">Старая книга</dc:title><dc:creator id="author" opf:file-as="Wrong sort" xml:lang="ru">Старое имя</dc:creator><meta refines="#author" property="file-as">Wrong sort</meta><meta name="calibre:author_sort" content="Old"/><dc:language>en</dc:language><dc:subject id="genre">Fantasy</dc:subject><meta refines="#genre" property="authority">BISAC</meta><meta refines="#genre" property="term">FIC009000</meta><dc:contributor>Translator to preserve</dc:contributor></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>'''

class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();app.AUTO_CONNECT=False;app.select_library(self.root)
        app.JOURNAL=self.root/'state/moves.jsonl';self.book=self.root/'book.epub'
        with zipfile.ZipFile(self.book,'w') as z:
            z.writestr('mimetype',b'application/epub+zip',compress_type=zipfile.ZIP_STORED)
            z.writestr('META-INF/container.xml',CONTAINER)
            z.writestr('OPS/book.opf',OPF)
            z.writestr('OPS/chapter.xhtml',b'<html>All the original text.</html>')
            z.writestr('OPS/image.jpg',b'cover-image')
        self.original=self.book.read_bytes();self.stat=self.book.stat()
    def tearDown(self):self.temp.cleanup()
    def save(self,**kw):
        return app.edit_metadata(kw.get('path','book.epub'),kw.get('title','Correct Title & Subtitle'),kw.get('authors',['Renée Example','作者']),kw.get('size',self.stat.st_size),kw.get('modified',self.stat.st_mtime),kw.get('genres'))
    def test_cover_edit_adds_art_and_then_replaces_it_in_place(self):
        import base64

        encoded = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6ioAAAAASUVORK5CYII='
        image = base64.b64decode(encoded)
        for version in ('3.0', '2.0'):
            with self.subTest(version=version):
                self.add_series_fixture('', version=version)
                original = self.book.read_bytes()
                stat = self.book.stat()
                saved = app.edit_metadata('book.epub', None, None, stat.st_size, stat.st_mtime, cover=encoded)
                self.assertTrue(saved['hasCover'])
                self.assertEqual(app.metadata(self.book)['cover'], ('image/png', image))
                self.assertEqual(Path(saved['backup']).read_bytes(), original)
                with zipfile.ZipFile(self.book) as book:
                    names = book.namelist()
                    self.assertIsNone(book.testzip())
                    self.assertEqual(book.read('OPS/chapter.xhtml'), b'<html>All the original text.</html>')
                app.edit_metadata('book.epub', 'New title', None, saved['size'], saved['modified'], cover=encoded)
                with zipfile.ZipFile(self.book) as book:
                    self.assertEqual(book.namelist(), names)
                self.assertEqual(app.metadata(self.book)['title'], 'New title')

    def test_invalid_cover_does_not_change_original(self):
        with self.assertRaisesRegex(ValueError, 'JPEG or PNG'):
            app.edit_metadata('book.epub', None, None, self.stat.st_size, self.stat.st_mtime, cover='dGV4dA==')
        self.assertEqual(self.book.read_bytes(), self.original)

    def test_cover_edit_preserves_a_chapter_mislabeled_as_cover_art(self):
        from base64 import b64encode
        from test_app import PNG

        chapter = b'<html>Keep this chapter</html>'
        malformed = OPF.replace('</manifest>', '<item id="bogus" href="chapter.xhtml" '
                                'media-type="image/png" properties="cover-image"/></manifest>')
        with zipfile.ZipFile(self.book, 'w') as archive:
            archive.writestr('mimetype', b'application/epub+zip')
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', malformed)
            archive.writestr('OPS/chapter.xhtml', chapter)
        info = self.book.stat()
        app.edit_metadata('book.epub', None, None, info.st_size, info.st_mtime, cover=b64encode(PNG).decode())
        self.assertEqual(app.metadata(self.book)['cover'], ('image/png', PNG))
        with zipfile.ZipFile(self.book) as archive:
            self.assertEqual(archive.read('OPS/chapter.xhtml'), chapter)

    def test_unicode_metadata_backup_and_other_entries_preserved(self):
        result=self.save();self.assertEqual(app.metadata(self.book)['authors'],['Renée Example','作者'])
        self.assertEqual(app.metadata(self.book)['title'],'Correct Title & Subtitle')
        self.assertEqual(Path(result['backup']).read_bytes(),self.original)
        with zipfile.ZipFile(self.book) as z:
            self.assertIsNone(z.testzip());self.assertEqual(z.infolist()[0].filename,'mimetype');self.assertEqual(z.infolist()[0].compress_type,0)
            self.assertEqual(z.read('OPS/chapter.xhtml'),b'<html>All the original text.</html>');self.assertEqual(z.read('OPS/image.jpg'),b'cover-image')
            package=z.read('OPS/book.opf').decode();self.assertIn('urn:test',package);self.assertIn('Translator to preserve',package)
            self.assertNotIn('Wrong sort',package);self.assertNotIn('xml:lang="ru"',package)
        self.assertTrue(self.book.is_file());self.assertEqual(result['size'],self.book.stat().st_size)
    def test_subjects_survive_title_edit_and_can_be_changed_or_cleared(self):
        self.assertEqual(app.scan()['books'][0]['genres'], ['Fantasy'])
        first = self.save()
        with zipfile.ZipFile(self.book) as epub:
            self.assertIn(b'FIC009000', epub.read('OPS/book.opf'))
        revised = self.save(size=first['size'], modified=first['modified'], genres=['  Science   Fiction ', '歴史', ''])
        self.assertEqual(revised['genres'], ['Science Fiction', '歴史'])
        self.assertEqual(app.metadata(self.book)['genres'], revised['genres'])
        with zipfile.ZipFile(self.book) as epub:
            self.assertNotIn(b'FIC009000', epub.read('OPS/book.opf'))
        cleared = self.save(size=revised['size'], modified=revised['modified'], genres=[])
        self.assertEqual(cleared['genres'], [])
        self.assertEqual(app.metadata(self.book)['genres'], [])

    def test_genre_only_edit_preserves_title_and_author_markup(self):
        result = self.save(title=None, authors=None, genres=['Literary Fiction'])
        self.assertEqual(result['title'], 'Старая книга')
        self.assertEqual(result['authors'], ['Старое имя'])
        self.assertEqual(result['genres'], ['Literary Fiction'])
        self.assertEqual(Path(result['backup']).read_bytes(), self.original)
        with zipfile.ZipFile(self.book) as epub:
            package = epub.read('OPS/book.opf').decode()
            self.assertIn('<dc:title id="title" xml:lang="ru">Старая книга</dc:title>', package)
            self.assertIn('<dc:creator id="author" opf:file-as="Wrong sort" xml:lang="ru">Старое имя</dc:creator>', package)
            self.assertIn('<meta refines="#author" property="file-as">Wrong sort</meta>', package)
            self.assertIn('calibre:author_sort', package)

    def test_author_only_edit_preserves_genre_and_title_markup(self):
        result = self.save(title=None, authors=['New Author'])
        self.assertEqual(result['title'], 'Старая книга')
        self.assertEqual(result['genres'], ['Fantasy'])
        with zipfile.ZipFile(self.book) as epub:
            package = epub.read('OPS/book.opf').decode()
            self.assertIn('<dc:title id="title" xml:lang="ru">Старая книга</dc:title>', package)
            self.assertIn('FIC009000', package)
            self.assertNotIn('Wrong sort', package)

    def add_series_fixture(self, extra, version='3.0'):
        with zipfile.ZipFile(self.book) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
        with zipfile.ZipFile(self.book, 'w') as target:
            for info, data in entries:
                if info.filename == 'OPS/book.opf':
                    data = OPF.replace('version="3.0"', f'version="{version}"').replace(
                        '</metadata>', extra + '</metadata>').encode()
                target.writestr(info, data)

    def test_epub3_series_edit_preserves_unrelated_collection_and_shows_sync_difference(self):
        self.add_series_fixture(
            '<meta property="belongs-to-collection" id="story">Old Series</meta>'
            '<meta refines="#story" property="collection-type">series</meta>'
            '<meta refines="#story" property="group-position">1</meta>'
            '<meta property="belongs-to-collection" id="set">Boxed Set</meta>'
            '<meta refines="#set" property="collection-type">set</meta>'
        )
        original = self.root / 'original.epub'
        original.write_bytes(self.book.read_bytes())
        stat = self.book.stat()
        first = app.edit_metadata('book.epub', None, None, stat.st_size, stat.st_mtime,
                                  series='New Series', series_position='2.5')
        self.assertEqual((first['series'], first['seriesPosition']), ('New Series', '2.5'))
        self.assertEqual(Path(first['backup']).read_bytes(), original.read_bytes())
        comparison = app.compare_epub_versions(self.book, original)
        self.assertFalse(comparison['contentsDiffer'])
        self.assertEqual(comparison['changes'], [
            {'label': 'Series', 'mac': 'New Series', 'kindle': 'Old Series'},
            {'label': 'Series position', 'mac': '2.5', 'kindle': '1'},
        ])
        cleared = app.edit_metadata('book.epub', None, None, first['size'], first['modified'], series='')
        self.assertEqual((cleared['series'], cleared['seriesPosition']), ('', ''))
        with zipfile.ZipFile(self.book) as epub:
            self.assertIn(b'Boxed Set', epub.read('OPS/book.opf'))
            self.assertIn(b'refines="#set"', epub.read('OPS/book.opf'))

    def test_epub2_calibre_position_survives_series_rename(self):
        self.add_series_fixture(
            '<meta name="calibre:series" content="Old Series"/>'
            '<meta name="calibre:series_index" content="3.0"/>', version='2.0'
        )
        stat = self.book.stat()
        result = app.edit_metadata('book.epub', None, None, stat.st_size, stat.st_mtime, series='New Series')
        self.assertEqual((result['series'], result['seriesPosition']), ('New Series', '3.0'))
        with zipfile.ZipFile(self.book) as epub:
            package = epub.read('OPS/book.opf')
            self.assertIn(b'name="calibre:series" content="New Series"', package)
            self.assertNotIn(b'belongs-to-collection', package)

    def test_sidecar_unchanged(self):
        d=self.root/'book.sdr';d.mkdir();(d/'metadata.epub.lua').write_bytes(b'progress and notes')
        self.save();self.assertEqual((d/'metadata.epub.lua').read_bytes(),b'progress and notes')
    def test_multiple_saves_each_backed_up(self):
        first=self.save();new=self.book.read_bytes();st=self.book.stat()
        second=self.save(title='Another title',size=st.st_size,modified=st.st_mtime)
        self.assertNotEqual(first['backup'],second['backup']);self.assertEqual(Path(second['backup']).read_bytes(),new)
    def test_invalid_fields_do_not_write(self):
        for title,authors in [('', ['Name']),('Title',[]),('Title',['']),('Title','Name'),('Title',['Bad\x00Name'])]:
            with self.subTest(title=title,authors=authors),self.assertRaises(ValueError):self.save(title=title,authors=authors)
        self.assertEqual(self.book.read_bytes(),self.original)
    def test_stale_book_rejected(self):
        with self.assertRaisesRegex(ValueError,'changed'):self.save(size=1)
        self.assertEqual(self.book.read_bytes(),self.original)
    def test_failed_replace_preserves_original_and_backup(self):
        with patch.object(app.os,'replace',side_effect=OSError('disk failure')),self.assertRaisesRegex(ValueError,'not saved'):self.save()
        self.assertEqual(self.book.read_bytes(),self.original);self.assertFalse(list(self.root.glob('.koshelf-edit-*')))
        self.assertEqual(next((self.root/'state/metadata-backups').glob('*.epub')).read_bytes(),self.original)
    def test_signed_file_refused(self):
        with zipfile.ZipFile(self.book,'a') as z:z.writestr('META-INF/signatures.xml','signature')
        original=self.book.read_bytes();self.stat=self.book.stat()
        with self.assertRaisesRegex(ValueError,'signed'):self.save()
        self.assertEqual(self.book.read_bytes(),original)
    def test_escape_rejected(self):
        with self.assertRaises(ValueError):self.save(path='../book.epub')
    def test_missing_title_and_creator_created(self):
        revised=app.rewrite_opf(b'<package xmlns="http://www.idpf.org/2007/opf"><metadata/></package>','Title',['Author'])
        self.assertIn(b'<dc:title>Title</dc:title>',revised);self.assertIn(b'<dc:creator>Author</dc:creator>',revised)
    def test_xml_declarations_refused(self):
        with self.assertRaisesRegex(ValueError,'XML declarations'):app.rewrite_opf(b'<!DOCTYPE test>'+OPF.encode(),'Title',['Author'])

if __name__=='__main__':unittest.main()
