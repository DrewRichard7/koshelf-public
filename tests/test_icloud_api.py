import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class ICloudSetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.mac = self.base / 'KoShelf Library'
        self.drive = self.base / 'iCloud Drive'
        self.cloud = self.drive / 'KoShelf'
        self.mac.mkdir()
        self.cloud.mkdir(parents=True)
        for name, value in {
            'ROOT': None,
            'ROOT_ID': None,
            'JOURNAL': self.base / 'state' / 'moves.jsonl',
        }.items():
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, folder in [('mac_library', self.mac), ('icloud_drive', self.drive)]:
            patcher = patch.object(app, name, return_value=folder)
            patcher.start()
            self.addCleanup(patcher.stop)
        app.select_library(self.mac)

    def test_disconnect_keeps_files_and_history(self):
        self.assertIsNone(app.icloud_status()['path'])
        self.assertTrue(app.configure_icloud(str(self.cloud))['available'])
        book = self.cloud / 'book.epub'
        book.write_bytes(b'book')
        app.icloud_history({'id': 'run', 'status': 'Completed'})
        result = app.configure_icloud(None)
        self.assertFalse(result['available'])
        self.assertEqual(book.read_bytes(), b'book')
        self.assertEqual(app.icloud_history(), [{'id': 'run', 'status': 'Completed'}])

    def test_setup_rejects_drive_root_and_unsafe_paths(self):
        link = self.drive / 'link'
        link.symlink_to(self.cloud, target_is_directory=True)
        for folder in (self.base, self.mac, self.drive, link, Path('relative')):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                app.configure_icloud(str(folder))
        self.assertIsNone(app.icloud_status()['path'])

    def test_replaced_folder_cannot_be_synced_without_setup(self):
        app.configure_icloud(str(self.cloud))
        self.cloud.rename(self.drive / 'old')
        self.cloud.mkdir()
        self.assertFalse(app.icloud_status()['available'])
        with patch.object(app.cloud_sync, 'preview') as preview:
            with self.assertRaisesRegex(ValueError, 'folder changed'):
                app.run_icloud_sync({})
            preview.assert_not_called()

    def test_successful_apply_writes_history(self):
        action = {'kind': 'copy', 'source': 'mac', 'target': 'icloud', 'path': 'book.epub'}
        result = {'applied': 1, 'skipped': 0, 'changes': [action], 'errors': []}
        app.configure_icloud(str(self.cloud))
        with patch.object(app.cloud_sync, 'preview', return_value={'token': 'review'}):
            token = app.run_icloud_sync({})['token']
        with patch.object(app.cloud_sync, 'apply', return_value=result) as apply:
            self.assertEqual(app.run_icloud_sync({'token': token, 'choices': {}}), result)
        mac, cloud, state, token, choices = apply.call_args.args
        self.assertEqual((mac, cloud, token, choices), (self.mac, self.cloud, 'review', {}))
        self.assertEqual(state.parent, app.JOURNAL.parent / 'icloud-sync')
        history, = app.icloud_history()
        self.assertEqual((history['status'], history['actions']), ('Completed', [action]))

    def test_reconnecting_replaced_folder_invalidates_prior_review(self):
        app.configure_icloud(str(self.cloud))
        with patch.object(app.cloud_sync, 'preview', return_value={'token': 'review'}):
            token = app.run_icloud_sync({})['token']
        self.cloud.rename(self.drive / 'old')
        self.cloud.mkdir()
        app.configure_icloud(str(self.cloud))
        with patch.object(app.cloud_sync, 'apply') as apply:
            with self.assertRaisesRegex(ValueError, 'Review sync again'):
                app.run_icloud_sync({'token': token})
            apply.assert_not_called()

    def test_sync_respects_lock_on_shared_mac_folder(self):
        app.configure_icloud(str(self.cloud))
        fd = os.open(self.mac, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(app.cloud_sync, 'preview') as preview:
            with self.assertRaisesRegex(ValueError, 'Another KoShelf window'):
                app.run_icloud_sync({})
            preview.assert_not_called()

    def test_preview_and_apply_use_real_engine_in_temporary_folders(self):
        app.configure_icloud(str(self.cloud))
        (self.mac / 'book.epub').write_bytes(b'book')
        preview = app.run_icloud_sync({})
        self.assertFalse((self.cloud / 'book.epub').exists())
        result = app.run_icloud_sync({'token': preview['token'], 'choices': {}})
        self.assertEqual((result['applied'], result['errors']), (1, []))
        self.assertEqual((self.cloud / 'book.epub').read_bytes(), b'book')
        self.assertEqual(app.run_icloud_sync({})['status'], 'in-sync')
