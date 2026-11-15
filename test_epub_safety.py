import os
from collections import OrderedDict
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import app
import epub_safety
import epub_split
import kindle_delivery
from test_app import CONTAINER, OPF, PNG, epub


class EpubSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'book.epub'

    def test_entity_definitions_are_rejected_even_in_utf16(self):
        xml = ('<!DOCTYPE package [<!ENTITY title "Unexpected expansion">]>'
               + OPF.replace('A Test &amp; a Book', '&title;')).encode('utf-16')
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', xml)
        self.assertIsNotNone(app.metadata(self.path)['warning'])
        with self.assertRaisesRegex(ValueError, 'XML declarations'):
            app.rewrite_opf(xml, 'Edited', None)

    def test_external_doctype_for_legacy_xhtml_is_inert(self):
        data = b'<!DOCTYPE html SYSTEM "http://127.0.0.1:9/ignored"><html><body>Chapter</body></html>'
        self.assertEqual(epub_safety.parse_xml(data).findtext('body'), 'Chapter')
        with self.assertRaisesRegex(ValueError, 'too complex'):
            epub_safety.parse_xml(b'<a>' * 140 + b'</a>' * 140)

    def test_archive_directory_budget_uses_actual_directory_bytes(self):
        with zipfile.ZipFile(self.path, 'w') as archive:
            info = zipfile.ZipInfo('mimetype')
            info.comment = b'0' * 500
            archive.writestr(info, epub_safety.EPUB_MIMETYPE)
        with patch.object(epub_safety, 'MAX_DIRECTORY', 256):
            with self.assertRaisesRegex(ValueError, 'directory is too large'):
                with epub_safety.open_book(self.path):
                    self.fail('Oversized archive directory opened')

    def test_open_book_preserves_zip_comments(self):
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.comment = b'A normal ZIP comment'
            archive.writestr('mimetype', epub_safety.EPUB_MIMETYPE)
        with epub_safety.open_book(self.path) as archive:
            self.assertEqual(archive.comment, b'A normal ZIP comment')
            self.assertEqual(archive.read('mimetype'), epub_safety.EPUB_MIMETYPE)

    def test_archive_rejects_duplicate_names_and_fifo(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.path, 'w') as archive:
                archive.writestr('chapter.xhtml', 'First')
                archive.writestr('chapter.xhtml', 'Second')
        with self.assertRaisesRegex(ValueError, 'duplicate names'):
            kindle_delivery.check_epub(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertIsNotNone(app.metadata(self.path)['warning'])

    def test_split_rejects_an_oversized_mimetype_before_parsing(self):
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('mimetype', epub_safety.EPUB_MIMETYPE + b'x' * 50)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            epub_split.split(self.path, {}, app.rewrite_opf, os.rename)

    def test_split_accepts_legacy_doctype_and_trims_modern_navigation(self):
        package = '''<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
            <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">fixture</dc:identifier>
            <dc:title>Omnibus</dc:title></metadata><manifest>
                <item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>
                <item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>
                <item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>
            </manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>'''
        doctype = '<!DOCTYPE html SYSTEM "http://127.0.0.1:9/ignored">'
        nav = doctype + '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
            <body><nav epub:type="toc"><ol><li><a href="one.xhtml">First</a><ol>
            <li><a href="one.xhtml#chapter">First chapter</a></li></ol></li>
            <li><a href="two.xhtml">Second</a><ol><li><a href="two.xhtml#chapter">Second chapter</a></li></ol>
            </li></ol></nav></body></html>'''
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('mimetype', epub_safety.EPUB_MIMETYPE)
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', package)
            archive.writestr('OPS/nav.xhtml', nav)
            archive.writestr('OPS/one.xhtml', doctype + '<html><body><a href="two.xhtml">Next</a></body></html>')
            archive.writestr('OPS/two.xhtml', doctype + '<html><body>Second chapter</body></html>')
        preview = epub_split.split(self.path, {}, app.rewrite_opf, os.rename)
        self.assertEqual(preview['suggested'], [{'start': 0, 'title': 'First'}, {'start': 1, 'title': 'Second'}])
        request = {'revision': preview['revision'], 'parts': preview['suggested']}
        output = epub_split.split(self.path, request, app.rewrite_opf, os.rename)
        with zipfile.ZipFile(self.path.parent / output['folder'] / 'First.epub') as first:
            self.assertNotIn('OPS/two.xhtml', first.namelist())
            self.assertNotIn(b'two.xhtml', first.read('OPS/nav.xhtml'))
            self.assertNotIn(b'href=', first.read('OPS/one.xhtml'))

    def test_expanded_data_budget_precedes_conversion(self):
        with zipfile.ZipFile(self.path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('first.txt', b'0' * 2048)
            archive.writestr('second.txt', b'0' * 2048)
        with patch.object(kindle_delivery, 'MAX_CONVERSION', 3072):
            with patch.object(kindle_delivery.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'uncompressed data'):
                    kindle_delivery.convert(self.path, self.path.with_suffix('.azw3'), '/converter')
                run.assert_not_called()

    def test_oversized_raster_hidden_under_another_filename(self):
        large_png = PNG[:16] + struct.pack('>II', 20000, 20000) + PNG[24:]
        epub(self.path, large_png)
        self.assertIsNone(app.metadata(self.path)['cover'])
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', OPF)
            archive.writestr('content.data', large_png)
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            kindle_delivery.check_epub(self.path)

    def test_conversion_counts_referenced_css_aliases(self):
        package = (b'<package xmlns:x="urn:test"><manifest>'
                   b'<item id="first" href="style.css" media-type="text/css"/>'
                   b'<item id="second" x:href="st%79le.css#alias" media-type="text/css"/>'
                   b'</manifest></package>')
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', package)
            archive.writestr('OPS/style.css', 'body { color: black }')
        with patch.object(kindle_delivery, 'MAX_CONVERSION_CSS', 30):
            with self.assertRaisesRegex(ValueError, 'stylesheet data'):
                kindle_delivery.check_epub(self.path)

    def test_conversion_counts_repeated_spine_markup(self):
        package = (b'<package><manifest><item id="entry" href="chapter.xhtml"/></manifest>'
                   b'<spine><itemref idref="entry"/><itemref idref="entry"/></spine></package>')
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OPS/book.opf', package)
            archive.writestr('OPS/chapter.xhtml', '<html><body><p>A chapter</p></body></html>')
        with patch.object(kindle_delivery, 'MAX_CONVERSION_MARKUP', 9):
            with self.assertRaisesRegex(ValueError, 'complex chapter markup'):
                kindle_delivery.check_epub(self.path)

    def test_cover_cache_evicts_older_payloads_by_byte_count(self):
        paths = [self.path.with_name(f'{index}.epub') for index in range(3)]
        for path in paths:
            epub(path, PNG)
        with patch.multiple(app, COVER_CACHE=OrderedDict(), COVER_CACHE_SIZE=0, MAX_COVER_CACHE=2 * len(PNG)):
            for path in (paths[0], paths[1], paths[0], paths[2]):
                self.assertEqual(app.cached_cover(path, path.stat()), ('image/png', PNG))
            self.assertEqual([key[0] for key in app.COVER_CACHE], [str(paths[0]), str(paths[2])])
            self.assertEqual(app.COVER_CACHE_SIZE, 2 * len(PNG))

    def test_exact_validated_staged_copy_is_passed_to_converter(self):
        epub(self.path)
        expected = self.path.read_bytes()
        output = self.path.with_suffix('.azw3')

        def run(args, **kwargs):
            self.path.write_bytes(b'Another process replaced the source')
            _, staged, destination = map(Path, args)
            self.assertEqual(staged.name, self.path.name)
            self.assertEqual(staged.read_bytes(), expected)
            self.assertEqual(destination, output)
            destination.write_bytes(b'\0' * 60 + b'BOOKMOBI' + b'\0' * 10)
            return subprocess.CompletedProcess(args, 0)

        with patch.object(kindle_delivery.subprocess, 'run', side_effect=run):
            kindle_delivery.convert(self.path, output, '/converter')
        self.assertTrue(output.exists())
        self.assertEqual(set(output.parent.iterdir()), {self.path, output})


if __name__ == '__main__':
    unittest.main()
