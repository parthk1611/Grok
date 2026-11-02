import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("secret_scan", Path(__file__).resolve().parents[2] / "scripts" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secret_scan)


class SecretScanTests(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(secret_scan.self_test(), 0)

    def test_detects_and_masks(self):
        token = "gh" + "p_" + "b" * 36
        hits = list(secret_scan.scan_text("f.py", f'x = "{token}"'))
        self.assertEqual([h[2] for h in hits], ["github-token"])
        self.assertNotIn(token, hits[0][3])

    def test_allow_marker_and_placeholders(self):
        token = "gh" + "p_" + "b" * 36
        self.assertEqual(list(secret_scan.scan_text("f", f"{token} # secret-scan: allow")), [])
        self.assertEqual(list(secret_scan.scan_text("f", 'password = "your-password-here-1234"')), [])

    def test_env_files_are_forbidden_but_example_is_not(self):
        self.assertTrue(secret_scan.BAD_FILENAMES.search(".env"))
        self.assertTrue(secret_scan.BAD_FILENAMES.search("backend/.env.local"))
        self.assertFalse(secret_scan.BAD_FILENAMES.search(".env.example"))


if __name__ == "__main__":
    unittest.main()
