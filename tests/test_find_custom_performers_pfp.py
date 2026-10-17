import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "find_custom_performers_pfp.py"
SPEC = importlib.util.spec_from_file_location("performer_loader", SCRIPT)
loader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loader)


class MatchingTests(unittest.TestCase):
    def test_exact_normalized_match_wins(self):
        result, reason = loader.find_best_match(
            {"jane doe"}, ["someone else", "jane doe"]
        )
        self.assertIsNone(reason)
        self.assertEqual(result[1], 1)
        self.assertEqual(result[0], 100.0)

    def test_short_partial_name_is_rejected(self):
        result, reason = loader.find_best_match(
            {"ava"}, ["ava taylor", "ava jones"]
        )
        self.assertIsNone(result)
        self.assertIn("shorter", reason)

    def test_tied_fuzzy_matches_are_rejected(self):
        result, reason = loader.find_best_match(
            {"emily"}, ["angel emily", "emily rose"]
        )
        self.assertIsNone(result)
        self.assertIn("ambiguous", reason)


class CreateMissingTests(unittest.TestCase):
    def test_only_missing_exact_name_is_planned(self):
        original_img_dir = loader.IMG_DIR
        original_dry_run = loader.DRY_RUN
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                loader.IMG_DIR = temp_dir
                loader.DRY_RUN = True
                images = ["Jane_Doe.jpg", "New Person.png"]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = loader.run_create_missing(
                        images, [("Jane Doe", "existing-id")]
                    )

            self.assertEqual(result, 0)
            text = output.getvalue()
            self.assertIn("[EXISTS] Jane_Doe.jpg", text)
            self.assertIn("[DRY-CREATE] New Person.png", text)
            self.assertNotIn("[DRY-CREATE] Jane_Doe.jpg", text)
        finally:
            loader.IMG_DIR = original_img_dir
            loader.DRY_RUN = original_dry_run


if __name__ == "__main__":
    unittest.main()
