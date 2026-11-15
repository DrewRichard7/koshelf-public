import unittest

from genre_cleanup import preview_genres, validate_mappings


def book(path, *genres):
    return {"path": path, "title": path, "author": "Test Author", "genres": list(genres)}


class GenreCleanupTests(unittest.TestCase):
    def test_known_aliases_collapse_and_broad_genres_are_removed(self):
        source = book("Novel.epub", "Fiction", "Sci-Fi", "FICTION / Science Fiction / Space Opera")
        result = preview_genres([source])
        suggestion, = result["books"]
        self.assertEqual(suggestion["proposed"], ["Science Fiction"])
        self.assertEqual(len(suggestion["changes"]), 3)
        self.assertEqual(source["genres"], suggestion["current"])
        self.assertEqual(preview_genres([book("Novel.epub", *suggestion["proposed"])])["books"], [])

    def test_unknown_tags_are_preserved_and_counted_across_books(self):
        source = [book("First.epub", "Award Winner", "FICTION / Unknown / Space Opera"),
                  book("Second.epub", "award winner", "AWARD WINNER")]
        result = preview_genres(source)
        self.assertEqual(result["books"][0]["proposed"], source[0]["genres"])
        self.assertEqual(result["books"][0]["changes"], [])
        self.assertEqual(result["tags"][0], {
            "tag": "Award Winner", "key": "award winner", "count": 2, "replacement": None,
        })

    def test_custom_rules_override_defaults_and_can_remove_tags(self):
        source = book("Novel.epub", "Fiction", "Sci-Fi", "Space Travel", "Award Winner")
        result = preview_genres([source], {
            "Fiction": "Fiction", "SCI-FI": "Speculative Fiction",
            " Space    Travel ": "Science Fiction", "award winner": "",
        })
        self.assertEqual(result["books"][0]["proposed"],
                         ["Fiction", "Speculative Fiction", "Science Fiction"])
        self.assertIn("Speculative Fiction", result["genres"])

    def test_saved_custom_genres_are_recognized_on_repeat_scan(self):
        mappings = {"sci-fi": "Speculative Fiction"}
        result = preview_genres([book("Novel.epub", "Speculative Fiction")], mappings)
        self.assertEqual(result["books"], [])
        self.assertEqual(result["tags"][0]["replacement"], "Speculative Fiction")

    def test_conflicting_targets_are_applied_one_step_at_a_time(self):
        source = book("Novel.epub", "Sci-Fi")
        result = preview_genres([source], {"sci-fi": "SF", "sf": "Sci-Fi"})
        self.assertEqual(result["books"][0]["proposed"], ["SF"])

    def test_unrecognized_non_english_subjects_stay_intact(self):
        source = book("Novel.epub", "Ciencia ficción", "传记")
        suggestion, = preview_genres([source])["books"]
        self.assertEqual(suggestion["proposed"], source["genres"])
        self.assertEqual(suggestion["unmapped"], source["genres"])

    def test_conflicting_normalized_mappings_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_mappings({"SF": "Speculative Fiction", "sf": "Science Fiction"})

    def test_invalid_mapping_values_are_rejected(self):
        for value in [False, None, "Genre\0", "\ud800"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_mappings({"sf": value})


if __name__ == "__main__":
    unittest.main()
