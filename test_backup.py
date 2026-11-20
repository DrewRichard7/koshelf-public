import os
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name).resolve()
        self.root=self.base/'library'
        self.root.mkdir()
        self.destination=self.base/'KoShelf Library'
        history = patch.object(app, 'JOURNAL', self.base / 'moves.jsonl')
        history.start()
        self.addCleanup(history.stop)
        app.AUTO_CONNECT=False
        app.select_library(self.root)
    def tearDown(self):self.temp.cleanup()
    def put(self, path, content):
        p=self.root/path
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(content)
        return p
    def test_default_creates_destination_and_preserves_tree(self):
        self.put('Series/Book.epub',b'book')
        self.put('Series/Book.sdr/metadata.epub.lua',b'progress')
        self.put('.hidden',b'hidden')
        (self.root/'Empty').mkdir()
        with patch.object(Path, 'home', return_value=self.base):
            result=app.backup_library()
        self.assertEqual(result['copied'],3)
        self.assertTrue((self.destination/'Empty').is_dir())
        self.assertEqual((self.destination/'Series/Book.sdr/metadata.epub.lua').read_bytes(),b'progress')
        self.assertEqual((self.destination/'.hidden').read_bytes(),b'hidden')
    def test_skips_repeats_and_keeps_changed_versions(self):
        source=self.put('Series/Book.epub',b'first')
        self.assertEqual(app.backup_library(self.destination)['copied'],1)
        self.assertEqual(app.backup_library(self.destination)['skipped'],1)
        source.write_bytes(b'other')
        self.assertEqual(app.backup_library(self.destination)['versions'],1)
        self.assertEqual(app.backup_library(self.destination)['skipped'],1)
        self.assertEqual((self.destination/'Series/Book.epub').read_bytes(),b'first')
        self.assertEqual(sorted(p.read_bytes() for p in (self.destination/'Series').iterdir()),[b'first',b'other'])
    def test_does_not_delete_backup_when_source_goes_away(self):
        source=self.put('Book.epub',b'book')
        app.backup_library(self.destination)
        source.unlink()
        app.backup_library(self.destination)
        self.assertTrue((self.destination/'Book.epub').is_file())
    def test_rejects_overlapping_paths(self):
        for p in [self.root,self.root/'backup',self.base]:
            with self.assertRaisesRegex(ValueError,'separate'):app.backup_library(p)

    def test_rejects_case_alias_of_library_ancestor(self):
        alias = self.root.with_name(self.root.name.upper())
        if not alias.is_dir():
            self.skipTest('requires a case-insensitive volume')
        nested = alias / 'backup'
        with self.assertRaisesRegex(ValueError, 'separate'):
            app.backup_library(nested, preview=True)
        with self.assertRaisesRegex(ValueError, 'separate'):
            app.backup_library(self.base, preview=True, source=alias / '..' / alias.name)
        self.assertFalse(nested.exists())

    def test_alignment_rejects_absolute_paths_and_traversal(self):
        for relative in ('', '.', '../outside.epub', 'created/../outside.epub', '/outside.epub'):
            with self.subTest(relative=relative):
                with self.assertRaises(ValueError):
                    app.backup_alignment_path(self.root, relative, create_parent=True)
        self.assertEqual(list(self.root.iterdir()), [])
    def test_skips_source_links_and_preserves_conflicting_backup(self):
        self.put('Book.epub',b'book')
        (self.root/'link').symlink_to(self.base,target_is_directory=True)
        self.destination.mkdir()
        (self.destination/'Book.epub').symlink_to(self.base/'outside')
        result=app.backup_library(self.destination)
        self.assertEqual((result['ignored'],result['failed']),(1,1))
        self.assertFalse((self.base/'outside').exists())
    def test_failed_copy_leaves_no_partial_file(self):
        self.put('Book.epub',b'book')
        with patch.object(app,'rename_exclusive',side_effect=OSError('simulated failure')):
            self.assertEqual(app.backup_library(self.destination)['failed'],1)
        self.assertEqual(list(self.destination.iterdir()),[])

    def moved_book(self):
        book = self.put('Old/Book.epub', b'book')
        self.put('Old/Book.sdr/metadata.epub.lua', b'progress')
        app.backup_library(self.destination)
        with patch.object(app, 'JOURNAL', self.base/'moves.jsonl'):
            app.create_folder('', 'New')
            app.move_book('Old/Book.epub', 'New')
        review = app.backup_library(self.destination)
        self.assertEqual(review['count'], 1)
        self.assertEqual(review['examples'], [{'kindle': ['New/Book.epub'], 'backup': ['Old/Book.epub']}])
        self.assertFalse((self.destination/'New').exists())
        self.assertFalse(book.exists())
        return review['review']

    def apply_review(self, choice, review):
        with patch.object(app, 'JOURNAL', self.base/'moves.jsonl'):
            return app.backup_library(self.destination, choice, review)

    def test_match_kindle_moves_existing_backup_and_companion(self):
        review = self.moved_book()
        result = self.apply_review('kindle', review)
        self.assertEqual((result['moved'], result['copied'], result['failed']), (1, 0, 0))
        self.assertFalse((self.destination/'Old/Book.epub').exists())
        self.assertEqual((self.destination/'New/Book.sdr/metadata.epub.lua').read_bytes(), b'progress')
        self.assertEqual(app.backup_library(self.destination)['skipped'], 2)

    def test_match_backup_recreates_kindle_folder_and_moves_progress(self):
        review = self.moved_book()
        (self.root/'Old').rmdir()
        result = self.apply_review('backup', review)
        self.assertEqual((result['moved'], result['failed']), (1, 0))
        self.assertFalse((self.root/'New/Book.epub').exists())
        self.assertEqual((self.root/'Old/Book.sdr/metadata.epub.lua').read_bytes(), b'progress')

    def test_keep_both_preserves_copies_and_does_not_prompt_again(self):
        review = self.moved_book()
        self.assertEqual(self.apply_review('merge', review)['copied'], 2)
        for folder in ('Old', 'New'):
            self.assertEqual((self.destination/folder/'Book.epub').read_bytes(), b'book')
        self.assertEqual(app.backup_library(self.destination)['skipped'], 2)

    def test_stale_review_is_rejected(self):
        review = self.moved_book()
        self.put('New/Book.epub', b'edit')
        with self.assertRaisesRegex(ValueError, 'changed during review'):
            self.apply_review('backup', review)
        self.assertEqual((self.root/'New/Book.epub').read_bytes(), b'edit')

    def test_occupied_alignment_path_does_not_overwrite_or_copy(self):
        review = self.moved_book()
        self.put('Old/Book.epub', b'other')
        # This additional book has different contents, so the reviewed match is unchanged.
        result = self.apply_review('backup', review)
        self.assertEqual((result['moved'], result['failed']), (0, 1))
        self.assertEqual((self.root/'Old/Book.epub').read_bytes(), b'other')
        self.assertEqual((self.root/'New/Book.epub').read_bytes(), b'book')
        self.assertFalse((self.destination/'New/Book.epub').exists())

    def test_multiple_backup_copies_are_left_in_place(self):
        self.moved_book()
        extra = self.destination/'Extra/Book.epub'
        extra.parent.mkdir()
        extra.write_bytes(b'book')
        review = app.backup_library(self.destination)
        result = self.apply_review('kindle', review['review'])
        self.assertEqual((result['moved'], result['failed']), (0, 1))
        self.assertTrue(extra.is_file())
        self.assertTrue((self.destination/'Old/Book.epub').is_file())
        self.assertFalse((self.destination/'New/Book.epub').exists())

    def test_same_size_different_content_is_not_a_match(self):
        self.put('Old/Book.epub', b'book')
        app.backup_library(self.destination)
        (self.root/'Old/Book.epub').unlink()
        self.put('New/Book.epub', b'edit')
        self.assertEqual(app.backup_library(self.destination)['copied'], 1)

    def test_sync_preview_copies_only_after_confirmation_with_mac_selected(self):
        book = self.put('Series/Book.epub', b'kindle')
        self.destination.mkdir()
        local = self.destination/'Mac only.epub'
        local.write_bytes(b'local')
        app.select_library(self.destination)
        self.assertTrue(app.sync_needed(self.root, self.destination))
        preview = app.backup_library(self.destination, source=self.root, preview=True)
        self.assertEqual(preview['count'], 0)
        self.assertFalse((self.destination/'Series').exists())
        result = app.backup_library(self.destination, 'copy', preview['review'], source=self.root)
        self.assertEqual(result['copied'], 1)
        self.assertFalse(app.sync_needed(self.root, self.destination))
        self.assertEqual(app.ROOT, self.destination)
        self.assertEqual(local.read_bytes(), b'local')
        book.write_bytes(b'edited')
        self.assertTrue(app.sync_needed(self.root, self.destination))
        preview = app.backup_library(self.destination, source=self.root, preview=True)
        app.backup_library(self.destination, 'copy', preview['review'], source=self.root)
        self.assertFalse(app.sync_needed(self.root, self.destination))
        self.assertEqual((self.destination/'Series/Book.epub').read_bytes(), b'kindle')

    def test_sync_moved_book_uses_same_review_from_mac_view(self):
        self.moved_book()
        app.select_library(self.destination)
        self.assertTrue(app.sync_needed(self.root, self.destination))
        preview = app.backup_library(self.destination, source=self.root, preview=True)
        self.assertEqual(preview['count'], 1)
        with patch.object(app, 'JOURNAL', self.base/'moves.jsonl'):
            result = app.backup_library(self.destination, 'backup', preview['review'], source=self.root)
        self.assertEqual(result['moved'], 1)
        self.assertFalse(app.sync_needed(self.root, self.destination))
        self.assertEqual(app.ROOT, self.destination)
        self.assertTrue((self.root/'Old/Book.epub').is_file())

    def test_send_new_epubs_preserves_folders_and_existing_books(self):
        self.destination.mkdir()
        self.put('Existing.epub', b'kindle copy')
        self.put('Moved.epub', b'already here')
        for rel, content in {
            'New/Series/Book.epub': b'new book',
            'Existing.epub': b'different Mac copy',
            'Series/Moved.epub': b'already here',
            'Existing (backup 0123456789abcdef).epub': b'backup version',
        }.items():
            path = self.destination / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.send_to_kindle(self.root)
            self.assertEqual(preview['paths'], ['New/Series/Book.epub'])
            self.assertFalse((self.root / 'New').exists())
            result = app.send_to_kindle(self.root, preview['review'])
            self.assertEqual((result['copied'], result['failed']), (1, 0))
            self.assertEqual(app.send_to_kindle(self.root)['count'], 0)
        self.assertEqual((self.root / 'New/Series/Book.epub').read_bytes(), b'new book')
        self.assertEqual((self.root / 'Existing.epub').read_bytes(), b'kindle copy')

    def test_send_rejects_changes_after_preview(self):
        self.destination.mkdir()
        book = self.destination / 'New.epub'
        book.write_bytes(b'first')
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.send_to_kindle(self.root)
            book.write_bytes(b'second')
            with self.assertRaisesRegex(ValueError, 'changed'):
                app.send_to_kindle(self.root, preview['review'])
        self.assertFalse((self.root / 'New.epub').exists())

    def test_sync_history_records_and_undoes_a_replacement(self):
        self.destination.mkdir()
        kindle_book = self.put('Book.epub', b'original Kindle edition')
        mac_book = self.destination / 'Book.epub'
        mac_book.write_bytes(b'edited Mac edition')
        sidecar = self.put('Book.sdr/metadata.epub.lua', b'progress')
        with patch.object(app, 'mac_library', return_value=self.destination), \
                patch.object(app, 'kindle_library', return_value=self.root), \
                patch.object(app, 'JOURNAL', self.base / 'state' / 'moves.jsonl'):
            preview = app.sync_libraries(self.root)
            self.assertEqual(app.sync_history(), [])
            app.sync_libraries(self.root, 'copy', preview['review'], ['Book.epub'])
            run, = app.sync_history()
            replacement, = [entry for entry in run['actions'] if entry['kind'] == 'replaced']
            self.assertEqual(run['status'], 'Completed')
            self.assertEqual(kindle_book.read_bytes(), mac_book.read_bytes())
            kindle_book.write_bytes(b'new Kindle edit')
            with self.assertRaisesRegex(ValueError, 'changed'):
                app.undo_sync_action(run['id'], replacement['id'])
            self.assertEqual(kindle_book.read_bytes(), b'new Kindle edit')
            kindle_book.write_bytes(mac_book.read_bytes())
            app.undo_sync_action(run['id'], replacement['id'])
            self.assertEqual(kindle_book.read_bytes(), b'original Kindle edition')
            self.assertEqual(sidecar.read_bytes(), b'progress')
            self.assertTrue(next(entry for entry in app.sync_history()[0]['actions']
                                 if entry['id'] == replacement['id'])['undone'])

    def test_sync_history_undo_of_new_copy_preserves_a_recovery_file(self):
        self.destination.mkdir()
        self.put('Book.epub', b'Kindle edition')
        with patch.object(app, 'mac_library', return_value=self.destination), \
                patch.object(app, 'JOURNAL', self.base / 'state' / 'moves.jsonl'):
            preview = app.sync_libraries(self.root)
            app.sync_libraries(self.root, 'copy', preview['review'])
            run, = app.sync_history()
            action, = run['actions']
            app.undo_sync_action(run['id'], action['id'])
            self.assertFalse((self.destination / 'Book.epub').exists())
            saved = app.JOURNAL.parent / 'sync-recovery' / action['id'] / 'Book.epub'
            self.assertEqual(saved.read_bytes(), b'Kindle edition')

    def test_unified_sync_reviews_both_directions_and_preserves_conflicts(self):
        self.destination.mkdir()
        self.put('Incoming/Book.epub', b'from Kindle')
        self.put('Existing.epub', b'Kindle edition')
        self.put('Incoming/Book.sdr/metadata.epub.lua', b'reading progress')
        (self.destination / 'Outgoing').mkdir()
        (self.destination / 'Outgoing/Book.epub').write_bytes(b'from Mac')
        (self.destination / 'Existing.epub').write_bytes(b'Mac edition')
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['toKindlePaths'], ['Outgoing/Book.epub'])
            self.assertIn('Incoming/Book.epub', preview['toMacPaths'])
            self.assertEqual(preview['conflictPaths'], ['Existing.epub'])
            self.assertFalse((self.root / 'Outgoing').exists())
            self.assertFalse((self.destination / 'Incoming').exists())
            result = app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertEqual(result['kindle']['copied'], 1)
            self.assertEqual(result['mac']['versions'], 1)
            self.assertEqual(app.sync_libraries(self.root)['conflicts'], 0)
        self.assertEqual((self.destination / 'Existing.epub').read_bytes(), b'Mac edition')
        self.assertEqual((self.root / 'Existing.epub').read_bytes(), b'Kindle edition')
        self.assertEqual((self.destination / 'Incoming/Book.sdr/metadata.epub.lua').read_bytes(), b'reading progress')

    def test_unified_sync_uses_selected_mac_version_with_backup_and_sidecar(self):
        self.destination.mkdir()
        book = self.put('Book.epub', b'original Kindle version')
        sidecar = self.put('Book.sdr/metadata.epub.lua', b'reading progress')
        self.put('Other.epub', b'other Kindle version')
        (self.destination / 'Book.epub').write_bytes(b'corrected author on Mac')
        (self.destination / 'Other.epub').write_bytes(b'other Mac version')
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['epubConflictPaths'], ['Book.epub', 'Other.epub'])
            # The replacement is still offered after an earlier keep-both sync.
            app.sync_libraries(self.root, 'copy', preview['review'])
            preview = app.sync_libraries(self.root)
            self.assertIn('Book.epub', preview['epubConflictPaths'])
            result = app.sync_libraries(self.root, 'copy', preview['review'], ['Book.epub'])
            self.assertEqual(result['kindle']['replaced'], 1)
        self.assertEqual(book.read_bytes(), b'corrected author on Mac')
        self.assertEqual(sidecar.read_bytes(), b'reading progress')
        self.assertEqual((self.root / 'Other.epub').read_bytes(), b'other Kindle version')
        # Recovery copies stay out of the library, in KoShelf's private folder.
        self.assertEqual(list(self.destination.rglob('* (backup *)*')), [])
        archived = next((self.base / 'sync-versions').glob('Book (backup *).epub'))
        self.assertEqual(archived.read_bytes(), b'original Kindle version')
        self.assertEqual(archived.stat().st_mode & 0o077, 0)
        self.assertEqual((self.base / 'sync-versions').stat().st_mode & 0o077, 0)

    def test_sync_keeps_only_recent_copies_of_reading_files(self):
        self.destination.mkdir()
        self.put('Book.epub', b'Kindle edition')
        (self.destination / 'Book.epub').write_bytes(b'Mac edition')
        (self.destination / 'Book.sdr').mkdir()
        (self.destination / 'Book.sdr/metadata.epub.lua').write_bytes(b'Mac progress')
        with patch.object(app, 'mac_library', return_value=self.destination):
            for session in range(app.MAX_READING_VERSIONS + 3):
                self.put('Book.sdr/metadata.epub.lua', f'Kindle progress {session}'.encode())
                preview = app.sync_libraries(self.root)
                app.sync_libraries(self.root, 'copy', preview['review'])
            # The newest Kindle reading file is still saved, so sync is settled.
            self.assertEqual(app.sync_libraries(self.root)['conflicts'], 0)
        versions = self.base / 'sync-versions'
        kept = sorted(path.read_bytes() for path in (versions / 'Book.sdr').iterdir())
        self.assertEqual(len(kept), app.MAX_READING_VERSIONS)
        self.assertIn(f'Kindle progress {app.MAX_READING_VERSIONS + 2}'.encode(), kept)
        self.assertNotIn(b'Kindle progress 0', kept)
        # EPUB recovery copies are never pruned.
        self.assertEqual([path.read_bytes() for path in versions.glob('Book (backup *).epub')], [b'Kindle edition'])

    def test_pruning_keeps_the_version_the_kindle_returned_to(self):
        # Copies built up before pruning existed, and the Kindle is back on the oldest one.
        for session in range(app.MAX_READING_VERSIONS + 3):
            content = f'Kindle progress {session}'.encode()
            copy = app.sync_version_path('Book.sdr/metadata.epub.lua', hashlib.sha256(content).hexdigest(), create=True)
            copy.write_bytes(content)
        self.put('Book.sdr/metadata.epub.lua', b'Kindle progress 0')
        self.assertEqual(app.prune_sync_versions(self.root), 2)
        kept = [path.read_bytes() for path in (self.base / 'sync-versions/Book.sdr').iterdir()]
        self.assertIn(b'Kindle progress 0', kept)
        self.assertNotIn(b'Kindle progress 1', kept)

    def test_sync_finishes_when_pruning_fails(self):
        self.destination.mkdir()
        self.put('Book.epub', b'new Kindle book')
        (self.base / 'sync-history.jsonl').write_text('{"kind": "finish"}\n')
        with patch.object(app, 'mac_library', return_value=self.destination), \
                patch.object(app, 'prune_sync_versions', side_effect=KeyError('run')):
            preview = app.sync_libraries(self.root)
            result = app.sync_libraries(self.root, 'copy', preview['review'])
        self.assertEqual(result['mac']['copied'], 1)

    def test_sync_skips_macos_metadata_files_from_the_kindle(self):
        self.destination.mkdir()
        self.put('Book.epub', b'same book')
        self.put('._Book.epub', b'\x00\x05\x16\x07 Kindle attributes')
        self.put('.DS_Store', b'finder')
        self.put('.Trashes/501/old', b'trash')
        (self.destination / 'Book.epub').write_bytes(b'same book')
        (self.destination / '._Book.epub').write_bytes(b'\x00\x05\x16\x07 different attributes')
        with patch.object(app, 'mac_library', return_value=self.destination):
            self.assertFalse(app.sync_needed(self.root, self.destination))
            preview = app.sync_libraries(self.root)
            self.assertEqual((preview['toMac'], preview['conflicts']), (0, 0))
            app.sync_libraries(self.root, 'copy', preview['review'])
        self.assertFalse((self.destination / '.DS_Store').exists())
        self.assertFalse((self.destination / '.Trashes').exists())
        self.assertEqual(list(self.destination.glob('* (backup *)*')), [])
        self.assertFalse((self.base / 'sync-versions').exists())

    def test_book_edited_on_mac_updates_kindle_without_asking(self):
        self.destination.mkdir()
        book = self.put('Shelf/Book.epub', b'before edit')
        (self.destination / 'Shelf').mkdir()
        (self.destination / 'Shelf/Book.epub').write_bytes(b'after edit on Mac')
        before = hashlib.sha256(b'before edit').hexdigest()
        with open(self.base / 'moves.jsonl', 'w') as journal:
            journal.write(json.dumps({'operation': 'edit-metadata', 'library': str(self.destination),
                                      'source': 'Shelf/Book.epub', 'sha256': before, 'status': 'completed'}) + '\n')
        os.chmod(self.base / 'moves.jsonl', 0o600)
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['macNewerPaths'], ['Shelf/Book.epub'])
            self.assertEqual(preview['epubConflictPaths'], [])
            self.assertNotIn('Shelf/Book.epub', preview['versionDifferencePaths'])
            result = app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertEqual(result['kindle']['replaced'], 1)
            self.assertFalse(app.sync_needed(self.root, self.destination))
        self.assertEqual(book.read_bytes(), b'after edit on Mac')
        self.assertEqual(list(self.destination.rglob('* (backup *)*')), [])
        saved = next((self.base / 'sync-versions/Shelf').glob('Book (backup *).epub'))
        self.assertEqual(saved.read_bytes(), b'before edit')
        kept = [action for run in app.sync_history() for action in run['actions'] if action['kind'] == 'kept version']
        self.assertEqual(len(kept), 1)
        with self.assertRaisesRegex(ValueError, 'stored privately'):
            app.undo_sync_action(app.sync_history()[0]['id'], kept[0]['id'])

    def edited_on_mac(self, relative, before, after, *, mac_relative=None):
        book = self.put(relative, before)
        mac_book = self.destination / (mac_relative or relative)
        mac_book.parent.mkdir(parents=True, exist_ok=True)
        mac_book.write_bytes(after)
        with open(self.base / 'moves.jsonl', 'a') as journal:
            journal.write(json.dumps({'operation': 'edit-metadata', 'library': str(self.destination),
                                      'source': mac_relative or relative,
                                      'sha256': hashlib.sha256(before).hexdigest(), 'status': 'completed'}) + '\n')
        os.chmod(self.base / 'moves.jsonl', 0o600)
        return book

    def test_edited_book_review_has_no_empty_conflict_section(self):
        self.destination.mkdir()
        self.edited_on_mac('Book.epub', b'v1', b'v2')
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
        self.assertEqual((preview['conflicts'], preview['conflictPaths'], preview['versionDifferenceCount']), (0, [], 0))
        self.assertEqual(preview['macNewerPaths'], ['Book.epub'])

    def test_undone_kindle_update_is_offered_instead_of_reapplied(self):
        self.destination.mkdir()
        book = self.edited_on_mac('Book.epub', b'v1', b'v2')
        with patch.object(app, 'mac_library', return_value=self.destination), \
                patch.object(app, 'kindle_library', return_value=self.root):
            preview = app.sync_libraries(self.root)
            app.sync_libraries(self.root, 'copy', preview['review'])
            run = app.sync_history()[0]
            replaced = next(action for action in run['actions'] if action['kind'] == 'replaced')
            app.undo_sync_action(run['id'], replaced['id'])
            self.assertEqual(book.read_bytes(), b'v1')
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['macNewerPaths'], [])
            self.assertEqual(preview['epubConflictPaths'], ['Book.epub'])
            app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertFalse(app.sync_needed(self.root, self.destination))
        self.assertEqual(book.read_bytes(), b'v1')

    def test_status_reports_edited_book_still_waiting_for_kindle_update(self):
        self.destination.mkdir()
        self.edited_on_mac('Book.epub', b'v1', b'v2')
        # A sync that saved the Kindle version but failed before replacing it.
        saved = app.sync_version_path('Book.epub', hashlib.sha256(b'v1').hexdigest(), create=True)
        saved.write_bytes(b'v1')
        with patch.object(app, 'mac_library', return_value=self.destination):
            self.assertTrue(app.sync_needed(self.root, self.destination))
            self.assertEqual(app.sync_libraries(self.root)['macNewerPaths'], ['Book.epub'])

    def test_edited_book_matches_across_filename_case(self):
        self.destination.mkdir()
        self.edited_on_mac('shelf/book.epub', b'v1', b'v2', mac_relative='Shelf/Book.epub')
        if not (self.destination / 'shelf/book.epub').exists():
            self.skipTest('case-sensitive filesystem')
        with patch.object(app, 'mac_library', return_value=self.destination):
            self.assertEqual(app.sync_libraries(self.root)['macNewerPaths'], ['shelf/book.epub'])

    def test_kindle_folder_with_only_macos_files_counts_as_empty(self):
        self.destination.mkdir()
        self.put('Removed/.DS_Store', b'finder')
        self.put('Removed/._Gone.epub', b'attributes')
        with open(self.base / 'moves.jsonl', 'a') as journal:
            journal.write(json.dumps({'operation': 'remove-folder', 'library': str(self.destination),
                                      'source': 'Removed', 'entries': [], 'status': 'completed'}) + '\n')
        os.chmod(self.base / 'moves.jsonl', 0o600)
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['removeEmptyFolders'], ['Removed'])
            result = app.sync_libraries(self.root, 'copy', preview['review'])
        self.assertEqual(result['kindle']['removedEmptyFolders'], 1)
        self.assertFalse((self.root / 'Removed').exists())
        self.assertFalse((self.destination / 'Removed').exists())

    def test_copies_saved_in_library_by_earlier_releases_still_count(self):
        self.destination.mkdir()
        book = self.put('Book.epub', b'Kindle version')
        (self.destination / 'Book.epub').write_bytes(b'Mac version')
        digest = hashlib.sha256(b'Kindle version').hexdigest()
        legacy = self.destination / f'Book (backup {digest[:16]}).epub'
        legacy.write_bytes(b'Kindle version')
        with patch.object(app, 'mac_library', return_value=self.destination):
            self.assertFalse(app.sync_needed(self.root, self.destination))
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['conflicts'], 0)
            result = app.sync_libraries(self.root, 'copy', preview['review'], ['Book.epub'])
            self.assertEqual(result['kindle']['replaced'], 1)
        self.assertEqual(book.read_bytes(), b'Mac version')
        self.assertFalse((self.base / 'sync-versions').exists())

    def test_unified_sync_rejects_stale_review_before_either_transfer(self):
        self.destination.mkdir()
        incoming = self.put('Incoming.epub', b'first')
        (self.destination / 'Outgoing.epub').write_bytes(b'from Mac')
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            incoming.write_bytes(b'second')
            with self.assertRaisesRegex(ValueError, 'changed'):
                app.sync_libraries(self.root, 'copy', preview['review'])
        self.assertFalse((self.root / 'Outgoing.epub').exists())
        self.assertFalse((self.destination / 'Incoming.epub').exists())

    def test_unified_sync_rejects_new_or_replaced_file_during_copy_pass(self):
        self.destination.mkdir()
        incoming = self.put('Incoming.epub', b'reviewed')
        original_backup = app.backup_library

        def change_source(*args, **kwargs):
            if kwargs.get('reviewed_files') is not None:
                incoming.write_bytes(b'replaced after review')
                self.put('New.epub', b'added after review')
            return original_backup(*args, **kwargs)

        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            with patch.object(app, 'backup_library', side_effect=change_source):
                result = app.sync_libraries(self.root, 'copy', preview['review'])
        self.assertEqual(result['mac']['failed'], 2)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_unified_sync_does_not_hash_through_mac_folder_symlink(self):
        self.put('Linked/Book.epub', b'on Kindle')
        outside = self.base / 'private'
        outside.mkdir()
        (outside / 'Book.epub').write_bytes(b'private')
        self.destination.mkdir()
        (self.destination / 'Linked').symlink_to(outside, target_is_directory=True)
        original_digest = app.sync_digest

        def digest(path, info):
            self.assertFalse(path.resolve().is_relative_to(outside))
            return original_digest(path, info)

        with patch.object(app, 'mac_library', return_value=self.destination), \
                patch.object(app, 'sync_digest', side_effect=digest):
            result = app.sync_libraries(self.root)
        self.assertEqual(result['epubConflictPaths'], [])

    def test_unified_sync_keep_both_locations_in_both_libraries(self):
        self.moved_book()
        with patch.object(app, 'mac_library', return_value=self.destination):
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['count'], 1)
            result = app.sync_libraries(self.root, 'merge', preview['review'])
            self.assertEqual(result['kindle']['copied'], 1)
            self.assertEqual(app.sync_libraries(self.root)['count'], 0)
        self.assertTrue((self.root / 'Old/Book.epub').is_file())


    def test_sync_sends_mac_keeper_before_archiving_kindle_duplicate(self):
        removed = self.put('Old/Book.epub', b'identical EPUB')
        mac_keeper = self.destination / 'Chosen.epub'
        mac_copy = self.destination / 'Old/Book.epub'
        mac_copy.parent.mkdir(parents=True)
        mac_keeper.write_bytes(removed.read_bytes())
        mac_copy.write_bytes(removed.read_bytes())
        app.select_library(self.destination)

        with patch.object(app, 'mac_library', return_value=self.destination):
            app.archive_duplicate('Chosen.epub', 'Old/Book.epub')
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['toKindlePaths'], ['Chosen.epub'])
            self.assertEqual(preview['archiveOnKindle'], [{'remove': 'Old/Book.epub', 'keep': 'Chosen.epub'}])
            self.assertTrue(removed.exists())
            with patch.object(app, 'rename_exclusive', side_effect=OSError('Write failed')):
                failed = app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertEqual(failed['kindle']['archived'], 0)
            self.assertTrue(removed.exists())

            preview = app.sync_libraries(self.root)
            result = app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertEqual(result['kindle']['archived'], 1)
            self.assertFalse(removed.exists())
            self.assertEqual((self.root / 'Chosen.epub').read_bytes(), b'identical EPUB')
            self.assertEqual(app.sync_libraries(self.root)['archiveOnKindle'], [])

    def test_duplicate_archive_preserves_bytes_and_blocks_sync_resurrection(self):
        kept = self.put('Kept.epub', b'identical book')
        removed = self.put('Extras/Copy.epub', kept.read_bytes())
        mac_copy = self.destination / 'Extras/Copy.epub'
        mac_copy.parent.mkdir(parents=True)
        mac_copy.write_bytes(kept.read_bytes())

        with patch.object(app, 'mac_library', return_value=self.destination):
            with self.assertRaisesRegex(ValueError, 'different EPUBs'):
                app.archive_duplicate('Kept.epub', 'Kept.epub')
            app.archive_duplicate('Kept.epub', 'Extras/Copy.epub')
            entry, = app.archived_duplicates()
            self.assertEqual(Path(entry['archivePath']).read_bytes(), kept.read_bytes())
            self.assertFalse(removed.exists())
            self.assertTrue(mac_copy.exists())
            self.assertEqual(app.kindle_import_plan(self.destination, self.root), {})
            preview = app.sync_libraries(self.root)
            self.assertEqual(preview['count'], 0)
            self.assertEqual(preview['toKindle'], 0)
            app.sync_libraries(self.root, 'copy', preview['review'])
            self.assertFalse(removed.exists())


if __name__=='__main__':unittest.main()
