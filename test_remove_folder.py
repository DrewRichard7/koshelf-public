import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
from test_app import epub

class RemoveFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        app.AUTO_CONNECT=False;app.select_library(self.root)
        app.JOURNAL=self.root/'state'/'moves.jsonl'
        self.folder=self.root/'Shelf';self.folder.mkdir()
    def tearDown(self):self.temp.cleanup()
    def test_promotes_all_contents_and_nested_directories(self):
        epub(self.folder/'Novel.epub');original=(self.folder/'Novel.epub').read_bytes()
        (self.folder/'Novel.sdr').mkdir();(self.folder/'Novel.sdr/metadata.epub.lua').write_bytes(b'progress')
        (self.folder/'Nested').mkdir();(self.folder/'Nested/note.txt').write_bytes(b'nested')
        (self.folder/'.hidden').write_bytes(b'hidden');(self.folder/'notes.pdf').write_bytes(b'other')
        result=app.remove_folder('Shelf')
        self.assertEqual(result['moved'],5);self.assertFalse(self.folder.exists())
        self.assertEqual((self.root/'Novel.epub').read_bytes(),original)
        for rel,data in [('Novel.sdr/metadata.epub.lua',b'progress'),('Nested/note.txt',b'nested'),('.hidden',b'hidden'),('notes.pdf',b'other')]:self.assertEqual((self.root/rel).read_bytes(),data)
    def test_nested_parent(self):
        (self.folder/'Child').mkdir();(self.folder/'Child/file.txt').write_text('keep')
        self.assertEqual(app.remove_folder('Shelf/Child')['parent'],'Shelf')
        self.assertEqual((self.folder/'file.txt').read_text(),'keep')
    def test_empty_folder(self):
        self.assertEqual(app.remove_folder('Shelf')['moved'],0);self.assertFalse(self.folder.exists())
    def test_collision_preflight_moves_nothing(self):
        (self.folder/'a.txt').write_text('a');(self.folder/'B.txt').write_text('b');(self.root/'b.TXT').write_text('original')
        with self.assertRaisesRegex(ValueError,'already exists'):app.remove_folder('Shelf')
        self.assertTrue((self.folder/'a.txt').exists());self.assertEqual((self.root/'b.TXT').read_text(),'original')
    def test_companion_conflict(self):
        epub(self.folder/'Novel.epub');(self.root/'Novel.sdr').mkdir()
        with self.assertRaises(ValueError):app.remove_folder('Shelf')
        self.assertTrue((self.folder/'Novel.epub').exists())
    def test_failure_rolls_everything_back(self):
        (self.folder/'a.txt').write_text('a');(self.folder/'b.txt').write_text('b')
        real=app.rename_exclusive
        def fail(source,dest):
            if source.name=='b.txt':raise OSError('injected failure')
            real(source,dest)
        with patch.object(app,'rename_exclusive',side_effect=fail),self.assertRaisesRegex(ValueError,'rolled back'):app.remove_folder('Shelf')
        self.assertEqual((self.folder/'a.txt').read_text(),'a');self.assertTrue((self.folder/'b.txt').exists());self.assertFalse((self.root/'a.txt').exists())
    def test_external_new_file_prevents_removing_folder(self):
        (self.folder/'a.txt').write_text('a');real=app.rename_exclusive
        def concurrent(source,dest):
            real(source,dest)
            if source.parent==self.folder:(self.folder/'new.txt').write_text('new')
        with patch.object(app,'rename_exclusive',side_effect=concurrent),self.assertRaisesRegex(ValueError,'rolled back'):app.remove_folder('Shelf')
        self.assertTrue((self.folder/'a.txt').exists());self.assertEqual((self.folder/'new.txt').read_text(),'new')
    def test_root_and_unsafe_paths_refused(self):
        for rel in ['', '.', '..', '../outside','/tmp','Shelf/../Shelf']:
            with self.subTest(rel=rel),self.assertRaises(ValueError):app.remove_folder(rel)
    def test_symlink_refused(self):
        (self.folder/'link').symlink_to(self.root)
        with self.assertRaises(ValueError):app.remove_folder('Shelf')
        self.assertTrue(self.folder.exists())

if __name__=='__main__':unittest.main()
