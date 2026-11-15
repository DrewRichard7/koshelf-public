import plistlib
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch
import app

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        app.ROOT=None;app.ROOT_ID=None;app.EJECTED_VOLUME=None;app.AUTO_CONNECT=True
    def tearDown(self):
        app.AUTO_CONNECT=False;app.EJECTED_VOLUME=None;self.temp.cleanup()
    def test_auto_connect(self):
        with patch.object(app,'kindle_candidates',return_value=[str(self.root)]):
            self.assertTrue(app.sync_connection()['connected']);self.assertEqual(app.ROOT,self.root)
    def test_wait_without_device(self):
        with patch.object(app,'kindle_candidates',return_value=[]):self.assertFalse(app.sync_connection()['connected'])
    def test_multiple_devices_do_not_choose_arbitrarily(self):
        with patch.object(app,'kindle_candidates',return_value=[str(self.root),'other']):self.assertFalse(app.sync_connection()['connected'])
    def test_reconnect_revalidates_identity(self):
        app.select_library(self.root);app.ROOT_ID=(0,0)
        with patch.object(app,'kindle_candidates',return_value=[str(self.root)]):self.assertTrue(app.sync_connection()['connected'])
    def test_manual_library_not_switched(self):
        app.select_library(self.root)
        with patch.object(app,'kindle_candidates',return_value=['/unrelated']):self.assertEqual(app.sync_connection()['root'],str(self.root))
    def test_mac_library_opens_and_kindle_is_detected_separately(self):
        kindle = self.root/'Kindle'/'books'
        kindle.mkdir(parents=True)
        local = self.root/'Mac library'
        with patch.object(app, 'LOCAL_FIRST', True), patch.object(app, 'mac_library', return_value=local), patch.object(app, 'kindle_candidates', return_value=[str(kindle)]):
            self.assertEqual(app.sync_connection()['root'], str(local))
            self.assertTrue(local.is_dir())
            self.assertEqual(app.kindle_library(), kindle)
            self.assertEqual(app.sync_connection()['root'], str(local))

    def test_stock_kindle_delivery_is_independent_of_koreader_sync(self):
        volume = self.root / 'Kindle'
        documents = volume / 'documents'
        documents.mkdir(parents=True)
        with patch.object(app, 'mounted_volumes', return_value=[volume]), patch.object(Path, 'is_mount', return_value=True):
            self.assertEqual(app.kindle_documents(), documents)
            self.assertIsNone(app.kindle_library())
            (volume / 'books').mkdir()
            self.assertIsNone(app.kindle_library())
            (volume / 'koreader').mkdir()
            self.assertEqual(app.usb_kindle_status(documents)['mode'], 'kindle')
            (volume / 'koreader' / 'koreader.sh').touch()
            self.assertEqual(app.usb_kindle_status(documents)['mode'], 'koreader')
            self.assertEqual(app.kindle_library(), volume / 'books')
            self.assertEqual(app.kindle_documents(), documents)

    def test_ejecting_kindle_from_mac_view_keeps_mac_library_open(self):
        app.select_library(self.root)
        with patch.object(app, 'device_info', return_value={'volume':'/Volumes/Kindle','device':'disk4','name':'Kindle'}), patch.object(app.subprocess,'run',side_effect=[CompletedProcess([],0,'',''),CompletedProcess([],1,b'',b'')]):
            self.assertTrue(app.eject_library(Path('/Volumes/Kindle/books'))['ejected'])
        self.assertEqual(app.ROOT, self.root)

    def eject_setup(self):
        app.select_library(self.root)
        return patch.object(app,'device_info',return_value={'volume':'/Volumes/Kindle','device':'disk4','name':'Kindle'})
    def test_eject_success_clears_library_only_after_confirmation(self):
        with self.eject_setup(),patch.object(app.subprocess,'run',side_effect=[CompletedProcess([],0,'',''),CompletedProcess([],1,b'',b'')]) as run:
            self.assertTrue(app.eject_library()['ejected']);self.assertIsNone(app.ROOT)
            self.assertEqual(run.call_args_list[0].args[0],['/usr/sbin/diskutil','eject','/Volumes/Kindle'])
    def test_eject_busy_keeps_library(self):
        with self.eject_setup(),patch.object(app.subprocess,'run',return_value=CompletedProcess([],1,'','busy')),self.assertRaisesRegex(ValueError,'could not eject'):app.eject_library()
        self.assertEqual(app.ROOT,self.root)
    def test_eject_not_unmounted_is_not_success(self):
        with self.eject_setup(),patch.object(app.subprocess,'run',side_effect=[CompletedProcess([],0,'',''),CompletedProcess([],0,plistlib.dumps({'MountPoint':'/Volumes/Kindle'}),b'')]),self.assertRaisesRegex(ValueError,'still mounted'):app.eject_library()
        self.assertEqual(app.ROOT,self.root)
    def test_timeout_not_reported_as_safe(self):
        with self.eject_setup(),patch.object(app.subprocess,'run',side_effect=TimeoutExpired('diskutil',30)),self.assertRaisesRegex(ValueError,'not confirmed'):app.eject_library()
    def test_internal_folder_cannot_eject(self):
        app.select_library(self.root);self.assertIsNone(app.device_info())

if __name__=='__main__':unittest.main()
