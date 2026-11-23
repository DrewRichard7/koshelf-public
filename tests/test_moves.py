import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
from .test_app import epub


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        root=self.base/'books';root.mkdir()
        app.select_library(root)
        app.JOURNAL=self.base/'state'/'moves.jsonl'
        epub(root/'Novel.epub')
        app.create_folder('', 'Fiction')
    def tearDown(self): self.temp.cleanup()
    def test_create_nested_folder(self):
        self.assertEqual(app.create_folder('Fiction','Favorites'),'Fiction/Favorites')
        self.assertTrue((app.ROOT/'Fiction/Favorites').is_dir())
    def test_invalid_names(self):
        for name in ['../escape','/absolute','.hidden','Novel.sdr','x/y','x\\y',' a','a ','a:folder','', 'x\x00']:
            with self.subTest(name=name), self.assertRaises(ValueError):app.create_folder('',name)
    def test_case_collision_folder(self):
        with self.assertRaises(ValueError):app.create_folder('','fiction')
    def test_move_preserves_epub_and_sidecar_bytes_and_undo(self):
        sidecar=app.ROOT/'Novel.sdr';sidecar.mkdir()
        data=b'return { ["percent_finished"] = 0.42 }'
        (sidecar/'metadata.epub.lua').write_bytes(data)
        (sidecar/'cover.jpg').write_bytes(b'custom-cover')
        original=(app.ROOT/'Novel.epub').read_bytes()
        result=app.move_book('Novel.epub','Fiction')
        self.assertTrue(result['withSidecar'])
        self.assertFalse((app.ROOT/'Novel.epub').exists())
        self.assertEqual((app.ROOT/'Fiction/Novel.epub').read_bytes(),original)
        self.assertEqual((app.ROOT/'Fiction/Novel.sdr/metadata.epub.lua').read_bytes(),data)
        self.assertEqual((app.ROOT/'Fiction/Novel.sdr/cover.jpg').read_bytes(),b'custom-cover')
        app.move_book(result['path'],'')
        self.assertEqual((app.ROOT/'Novel.epub').read_bytes(),original)
        self.assertEqual((app.ROOT/'Novel.sdr/metadata.epub.lua').read_bytes(),data)
    def test_book_collision_no_overwrite(self):
        dest=app.ROOT/'Fiction/novel.epub';dest.write_bytes(b'existing')
        with self.assertRaises(ValueError):app.move_book('Novel.epub','Fiction')
        self.assertEqual(dest.read_bytes(),b'existing');self.assertTrue((app.ROOT/'Novel.epub').exists())
    def test_orphan_sidecar_collision(self):
        (app.ROOT/'Fiction/Novel.sdr').mkdir()
        with self.assertRaises(ValueError):app.move_book('Novel.epub','Fiction')
        self.assertTrue((app.ROOT/'Novel.epub').exists())
    def test_path_escape(self):
        for source,dest in [('../outside.epub','Fiction'),('Novel.epub','../'),('/tmp/book.epub','Fiction'),('Novel.epub','/tmp')]:
            with self.subTest(source=source,dest=dest),self.assertRaises(ValueError):app.move_book(source,dest)
    def test_symlinks_rejected(self):
        (app.ROOT/'linked').symlink_to(self.base,target_is_directory=True)
        with self.assertRaises(ValueError):app.move_book('Novel.epub','linked')
        (app.ROOT/'Novel.sdr').symlink_to(self.base,target_is_directory=True)
        with self.assertRaises(ValueError):app.move_book('Novel.epub','Fiction')
    def test_shared_sidecar_rejected(self):
        (app.ROOT/'Novel.sdr').mkdir();(app.ROOT/'Novel.pdf').write_bytes(b'other-format')
        with self.assertRaises(ValueError):app.move_book('Novel.epub','Fiction')
    def test_failed_book_move_rolls_sidecar_back(self):
        (app.ROOT/'Novel.sdr').mkdir()
        real=app.rename_exclusive
        def fail(source,dest):
            if source.suffix=='.epub':raise OSError(errno.EIO,'Injected failure')
            real(source,dest)
        with patch.object(app,'rename_exclusive',side_effect=fail), self.assertRaisesRegex(ValueError,'rolled back'):
            app.move_book('Novel.epub','Fiction')
        self.assertTrue((app.ROOT/'Novel.sdr').is_dir());self.assertTrue((app.ROOT/'Novel.epub').exists())
        self.assertFalse((app.ROOT/'Fiction/Novel.sdr').exists())
    def test_failed_rollback_reports_recovery_and_logs_paths(self):
        (app.ROOT/'Novel.sdr').mkdir();real=app.rename_exclusive
        def fail(source,dest):
            if source.suffix=='.epub' or source.parent.name=='Fiction':raise OSError(errno.EIO,'Injected disconnect')
            real(source,dest)
        with patch.object(app,'rename_exclusive',side_effect=fail),self.assertRaisesRegex(ValueError,'need recovery'):
            app.move_book('Novel.epub','Fiction')
        self.assertIn('recovery-needed',app.JOURNAL.read_text())
    def test_exclusive_rename_race_does_not_overwrite(self):
        dest=app.ROOT/'Fiction/Novel.epub';dest.write_bytes(b'existing')
        with self.assertRaises(OSError):app.rename_exclusive(app.ROOT/'Novel.epub',dest)
        self.assertEqual(dest.read_bytes(),b'existing')
    def test_replaced_root_rejected(self):
        app.ROOT_ID=(0,0)
        with self.assertRaisesRegex(ValueError,'library changed'):app.create_folder('','New')
    def test_fat_fallback_moves_file_and_directory(self):
        directory=app.ROOT/'Novel.sdr';directory.mkdir();(directory/'metadata.epub.lua').write_bytes(b'progress')
        original=(app.ROOT/'Novel.epub').read_bytes()
        app.rename_reserved(directory,app.ROOT/'Fiction/Novel.sdr')
        app.rename_reserved(app.ROOT/'Novel.epub',app.ROOT/'Fiction/Novel.epub')
        self.assertEqual((app.ROOT/'Fiction/Novel.epub').read_bytes(),original)
        self.assertEqual((app.ROOT/'Fiction/Novel.sdr/metadata.epub.lua').read_bytes(),b'progress')
    def test_fat_fallback_refuses_existing_destinations(self):
        dest=app.ROOT/'Fiction/Novel.epub';dest.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):app.rename_reserved(app.ROOT/'Novel.epub',dest)
        self.assertEqual(dest.read_bytes(),b'existing')
        directory=app.ROOT/'Novel.sdr';directory.mkdir();target=app.ROOT/'Fiction/Novel.sdr';target.mkdir()
        with self.assertRaises(FileExistsError):app.rename_reserved(directory,target)
    def test_fat_fallback_cleans_reservation_on_failure(self):
        dest=app.ROOT/'Fiction/Novel.epub'
        with patch.object(app.os,'rename',side_effect=OSError('Injected failure')),self.assertRaises(OSError):
            app.rename_reserved(app.ROOT/'Novel.epub',dest)
        self.assertFalse(dest.exists());self.assertTrue((app.ROOT/'Novel.epub').exists())
    def test_noop_rejected(self):
        with self.assertRaisesRegex(ValueError,'already'):app.move_book('Novel.epub','')

if __name__=='__main__':unittest.main()
