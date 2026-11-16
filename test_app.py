"""Run with: python3 -m unittest discover -s . -p 'test_*.py'"""
from base64 import b64decode
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import app

CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'
OPF = '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>A Test &amp; a Book</dc:title><dc:creator>Some Author</dc:creator></metadata><manifest><item id="cover" href="images/cover.png" media-type="image/png" properties="cover-image"/></manifest></package>'
PNG = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6ioAAAAASUVORK5CYII=')


def epub(path, cover=PNG):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('OPS/book.opf', OPF)
        z.writestr('OPS/images/cover.png', cover)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.AUTO_CONNECT = False
        app.select_library(Path(self.temp.name))
        app.CACHE.clear()
        self.state = tempfile.TemporaryDirectory()
        self.journal = patch.object(app, 'JOURNAL', Path(self.state.name) / 'moves.jsonl')
        self.journal.start()
    def tearDown(self):
        self.journal.stop()
        self.state.cleanup()
        self.temp.cleanup()
    def test_epub3_metadata_cover_and_folders(self):
        d=app.ROOT/'Fantasy';d.mkdir();epub(d/'book.epub')
        data=app.scan();b=data['books'][0]
        self.assertEqual((b['title'],b['author'],b['folder']),('A Test & a Book','Some Author','Fantasy'))
        self.assertTrue(b['hasCover']);self.assertEqual(data['folders'],['Fantasy'])
    def test_epub2_cover(self):
        p=app.ROOT/'old.epub'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('META-INF/container.xml',CONTAINER)
            z.writestr('OPS/book.opf',OPF.replace('<dc:title>','<meta name="cover" content="cover"/><dc:title>').replace(' properties="cover-image"',''))
            z.writestr('OPS/images/cover.png', PNG)
        self.assertTrue(app.metadata(p)['cover'])
    def test_corrupt_epub_does_not_break_library(self):
        (app.ROOT/'Broken.epub').write_bytes(b'bad zip')
        b=app.scan()['books'][0]
        self.assertEqual(b['title'],'Broken');self.assertIsNotNone(b['warning'])
    def test_hidden_sidecars_and_symlinks_excluded(self):
        for name in ['.hidden','book.sdr']:
            d=app.ROOT/name;d.mkdir();epub(d/'hidden.epub')
        epub(app.ROOT/'visible.epub')
        (app.ROOT/'link.epub').symlink_to(app.ROOT/'visible.epub')
        self.assertEqual(len(app.scan()['books']),1)
    def test_svg_cover_not_served(self):
        p=app.ROOT/'svg.epub';epub(p,b'<svg><script>bad()</script></svg>')
        self.assertIsNone(app.metadata(p)['cover'])
    def test_disconnect(self):
        app.ROOT=app.ROOT/'unmounted'
        self.assertFalse(app.scan()['connected'])
    def test_scan_does_not_change_files(self):
        p=app.ROOT/'book.epub';epub(p)
        before=p.read_bytes();mtime=p.stat().st_mtime_ns
        app.scan();app.scan()
        self.assertEqual(p.read_bytes(),before);self.assertEqual(p.stat().st_mtime_ns,mtime)
        self.assertEqual([x.name for x in app.ROOT.iterdir()],['book.epub'])
    def test_metadata_survives_relaunch_without_rereading(self):
        epub(app.ROOT/'book.epub')
        title=app.scan()['books'][0]['title']
        app.CACHE.clear();app.CACHE_STATE['loaded']=False
        with patch.object(app,'metadata',side_effect=AssertionError('reread')):
            book=app.scan()['books'][0]
        self.assertEqual(book['title'],title)
        self.assertNotIn('description',book)
        self.assertEqual((Path(self.state.name)/'metadata-cache.json').stat().st_mode&0o777,0o600)

if __name__=='__main__': unittest.main()
