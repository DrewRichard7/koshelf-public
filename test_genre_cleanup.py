import unittest

from genre_cleanup import choose_changes, preview_genres, validate_mappings


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
        # A book whose tags simply have no rule is not a proposed change.
        self.assertEqual([row["path"] for row in result["books"]], ["Second.epub"])
        self.assertEqual(result["books"][0]["proposed"], ["award winner"])
        self.assertEqual(result["unmappedBooks"], 2)
        self.assertEqual(result["tags"][0], {
            "tag": "Award Winner", "key": "award winner", "count": 2, "replacement": None, "known": False,
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
        result = preview_genres([source])
        self.assertEqual(result["books"], [])
        self.assertEqual(result["unmappedBooks"], 1)

    def test_each_tag_change_can_be_accepted_on_its_own(self):
        source = book("Novel.epub", "Fiction", "Sci-Fi", "Pulitzer Prize")
        suggestion, = preview_genres([source])["books"]
        changes = suggestion["changes"]
        self.assertEqual([change["index"] for change in changes], [0, 1])
        self.assertEqual(suggestion["proposed"], ["Science Fiction", "Pulitzer Prize"])
        self.assertEqual(choose_changes(source["genres"], changes, [1]), ["Fiction", "Science Fiction", "Pulitzer Prize"])
        self.assertEqual(choose_changes(source["genres"], changes, [0]), ["Sci-Fi", "Pulitzer Prize"])
        self.assertEqual(choose_changes(source["genres"], changes, [0, 1]), suggestion["proposed"])
        with self.assertRaises(ValueError):
            choose_changes(source["genres"], changes, [2])

    def test_partial_choices_never_duplicate_or_drop_genres(self):
        source = book("Novel.epub", "Sci-Fi", "Science Fiction")
        suggestion, = preview_genres([source])["books"]
        self.assertEqual(choose_changes(source["genres"], suggestion["changes"], [0]), ["Science Fiction"])
        # Removing a repeated tag removes only the repeat.
        repeated = book("Other.epub", "Fantasy", "Fantasy")
        suggestion, = preview_genres([repeated])["books"]
        self.assertEqual([change["index"] for change in suggestion["changes"]], [1])
        self.assertEqual(choose_changes(repeated["genres"], suggestion["changes"], [1]), ["Fantasy"])

    def test_recognized_tag_covers_current_and_future_books(self):
        recognized = {"western": "Western"}
        result = preview_genres([book("First.epub", "Western"), book("Second.epub", "Western")], recognized)
        self.assertEqual((result["books"], result["unmappedBooks"]), ([], 0))
        self.assertIn("Western", result["genres"])
        # A later book with the same tag in other capitalization joins the genre.
        suggestion, = preview_genres([book("Later.epub", "WESTERN")], recognized)["books"]
        self.assertEqual(suggestion["proposed"], ["Western"])

    def test_conflicting_normalized_mappings_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_mappings({"SF": "Speculative Fiction", "sf": "Science Fiction"})

    def test_invalid_mapping_values_are_rejected(self):
        for value in [False, None, "Genre\0", "\ud800"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_mappings({"sf": value})


if __name__ == "__main__":
    unittest.main()
