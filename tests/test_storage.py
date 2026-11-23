import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.root=self.base/'books';self.root.mkdir()
        app.AUTO_CONNECT=False;app.select_library(self.root);app.JOURNAL=self.base/'state/moves.jsonl'
    def tearDown(self):self.temp.cleanup()
    def put(self,path,count):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'x'*count)
    def test_recursive_totals_and_direct_sizes(self):
        self.put('root.epub',10);self.put('Fantasy/book.epub',20);self.put('Fantasy/Series/next.epub',30);self.put('Other/notes.pdf',40)
        d=app.storage_usage()['folders']
        self.assertEqual(d['']['bytes'],100);self.assertEqual(d['']['direct'],10);self.assertEqual(d['']['files'],4)
        self.assertEqual(d['Fantasy']['bytes'],50);self.assertEqual(d['Fantasy']['direct'],20);self.assertEqual(d['Fantasy/Series']['bytes'],30)
    def test_hidden_sidecars_and_non_epubs_count(self):
        self.put('.hidden',7);self.put('book.sdr/metadata.epub.lua',11);self.put('book.sdr/.backup',13);self.put('notes.txt',17)
        d=app.storage_usage()['folders'];self.assertEqual(d['']['bytes'],48);self.assertEqual(d['book.sdr']['bytes'],24)
    def test_symlinks_excluded(self):
        self.put('file.txt',10);(self.root/'link.txt').symlink_to(self.root/'file.txt');(self.root/'cycle').symlink_to(self.root,target_is_directory=True)
        self.assertEqual(app.storage_usage()['folders']['']['bytes'],10)
    def test_empty_folders_listed(self):
        (self.root/'Empty').mkdir();d=app.storage_usage()['folders'];self.assertEqual(d['Empty']['bytes'],0);self.assertEqual(d['']['files'],0)
    def test_volume_capacity_separate_from_library(self):
        self.put('book.epub',20)
        usage=app.shutil._ntuple_diskusage(1000,700,300)
        with patch.object(app.shutil,'disk_usage',return_value=usage):data=app.storage_usage()
        self.assertEqual(data['volume'],{'total':1000,'used':700,'free':300});self.assertEqual(data['folders']['']['bytes'],20)
    def test_recounts_after_file_moves_and_folder_removal(self):
        self.put('Shelf/book.epub',20);app.create_folder('','Other')
        app.move_book('Shelf/book.epub','Other')
        d=app.storage_usage()['folders'];self.assertEqual(d['Shelf']['bytes'],0);self.assertEqual(d['Other']['bytes'],20)
        app.remove_folder('Other');d=app.storage_usage()['folders'];self.assertNotIn('Other',d);self.assertEqual(d['']['direct'],20)
    def test_volume_failure_reports_partial(self):
        with patch.object(app.shutil,'disk_usage',side_effect=OSError('unavailable')):data=app.storage_usage()
        self.assertTrue(data['partial']);self.assertIsNone(data['volume'])

if __name__=='__main__':unittest.main()
