import tempfile
import unittest
from pathlib import Path

from app import rename_exclusive
from cloud_sync import apply, preview


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.mac, self.cloud, self.state = (Path(temp.name).resolve() / name
                                            for name in ("mac", "cloud", "state"))
        self.mac.mkdir()
        self.cloud.mkdir()

    def preview(self):
        return preview(self.mac, self.cloud, self.state)

    def apply(self, choices=None, token=None):
        return apply(self.mac, self.cloud, self.state, token or self.preview()["token"],
                     choices or {}, rename=rename_exclusive)

    def test_first_sync_copies_both_ways_and_preserves_empty_folders(self):
        (self.mac / "One.epub").write_bytes(b"one")
        (self.cloud / "Two.epub").write_bytes(b"two")
        (self.cloud / "Empty").mkdir()
        (self.cloud / "Ignore.sdr").mkdir()
        result = self.apply()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["applied"], 3)
        self.assertEqual((self.mac / "Two.epub").read_bytes(), b"two")
        self.assertEqual((self.cloud / "One.epub").read_bytes(), b"one")
        self.assertTrue((self.mac / "Empty").is_dir())
        self.assertFalse((self.mac / "Ignore.sdr").exists())

    def test_identical_initial_preview_records_baseline_for_reviewed_delete(self):
        for root in (self.mac, self.cloud):
            (root / "One.epub").write_bytes(b"original")
        self.assertEqual(self.preview()["status"], "in-sync")
        (self.cloud / "One.epub").unlink()
        action, = self.preview()["actions"]
        self.assertEqual(action["kind"], "delete")
        self.assertEqual(self.apply()["skipped"], 1)
        self.assertTrue((self.mac / "One.epub").exists())
        change, = self.apply({"One.epub": "icloud"})["changes"]
        self.assertEqual(Path(change["recovery"]).read_bytes(), b"original")
        self.assertFalse((self.mac / "One.epub").exists())

    def test_conflicting_revisions_require_choice_and_preserve_loser(self):
        (self.mac / "One.epub").write_bytes(b"mac")
        (self.cloud / "One.epub").write_bytes(b"cloud")
        self.assertEqual(self.preview()["actions"][0]["kind"], "conflict")
        self.assertEqual(self.apply()["applied"], 0)
        result = self.apply({"One.epub": "mac"})
        self.assertEqual(result["errors"], [])
        self.assertEqual((self.cloud / "One.epub").read_bytes(), b"mac")
        self.assertEqual(Path(result["changes"][0]["recovery"]).read_bytes(), b"cloud")

    def test_stale_review_token_blocks_writes(self):
        book = self.mac / "One.epub"
        book.write_bytes(b"first")
        token = self.preview()["token"]
        book.write_bytes(b"revised")
        with self.assertRaisesRegex(ValueError, "libraries changed"):
            self.apply(token=token)
        self.assertFalse((self.cloud / book.name).exists())

    def test_missing_cloud_root_refuses_to_scan(self):
        self.cloud.rmdir()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.preview()

    def test_detects_placeholder_and_symlink(self):
        placeholder = self.cloud / ".One.epub.icloud"
        placeholder.touch()
        with self.assertRaisesRegex(ValueError, "Download"):
            self.preview()
        placeholder.unlink()
        (self.cloud / "One.epub").symlink_to(self.mac / "One.epub")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.preview()

    def test_declining_move_leaves_both_replicas_unchanged(self):
        original = self.mac / "One.epub"
        original.write_bytes(b"move")
        self.apply()
        moved = self.mac / "Two.epub"
        original.rename(moved)
        actions = {action["path"]: action for action in self.preview()["actions"]}
        self.assertEqual(actions["Two.epub"]["moveFrom"], "One.epub")
        self.assertEqual(self.apply()["skipped"], 2)
        self.assertFalse((self.cloud / "Two.epub").exists())
        result = self.apply({"One.epub": "mac"})
        self.assertEqual(result["errors"], [])
        self.assertEqual([change["kind"] for change in result["changes"]], ["copy", "delete"])
        self.assertFalse((self.cloud / "One.epub").exists())
        self.assertEqual((self.cloud / "Two.epub").read_bytes(), b"move")

    def test_removing_folder_with_ignored_content_refuses_and_preserves_data(self):
        local = self.mac / "Folder"
        local.mkdir()
        self.apply()
        (local / "untouched.txt").touch()
        (self.cloud / "Folder").rmdir()
        result = self.apply({"Folder": "icloud"})
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["errors"][0]["path"], "Folder")
        self.assertTrue((local / "untouched.txt").exists())


if __name__ == "__main__":
    unittest.main()
