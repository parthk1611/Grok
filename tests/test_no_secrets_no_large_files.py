"""Repository hygiene: no obvious secrets and no oversized files in tracked/new files."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 50 * 1024 * 1024        # GitHub warns at 50 MB and blocks at 100 MB
MAX_SCANNED_BYTES = 5 * 1024 * 1024      # secret scan covers text files up to this size
SKIP_DIRS = {".git", "__pycache__", ".venv", ".venv-modeling", "node_modules", "artifacts"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".gz", ".bz2", ".zip", ".tif", ".tiff", ".parquet",
                   ".joblib", ".pdf", ".ico", ".xlsx", ".pyc"}

# Patterns are assembled from fragments so this file never matches its own scan.
SECRET_PATTERNS = {
    "AWS access key id": re.compile("AK" + "IA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_|github_" + r"pat_)[A-Za-z0-9_]{20,}"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile("AI" + r"za[0-9A-Za-z_\-]{35}"),
    "private key block": re.compile("-----BEGIN [A-Z ]*PRIV" + "ATE KEY-----"),
    "credential assignment": re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|passw(?:or)?d|auth[_-]?token|access[_-]?token)\b\s*[:=]\s*['"][A-Za-z0-9/+_\-]{16,}['"]"""
    ),
}


def candidate_files() -> list[Path]:
    """Tracked plus untracked-but-not-ignored files, so new files are checked before commit."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8", "surrogateescape")
        paths = [PROJECT_ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            paths.extend(Path(root) / f for f in files)
    return [p for p in paths if p.is_file()]


class RepositoryHygieneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = candidate_files()

    def test_files_were_found(self):
        self.assertGreater(len(self.files), 10)

    def test_no_file_exceeds_the_size_limit(self):
        big = [(str(p.relative_to(PROJECT_ROOT)), p.stat().st_size) for p in self.files if p.stat().st_size > MAX_FILE_BYTES]
        self.assertEqual(big, [], f"files larger than {MAX_FILE_BYTES} bytes")

    def test_no_secret_patterns_in_text_files(self):
        hits = []
        for path in self.files:
            if path.suffix.lower() in BINARY_SUFFIXES or path.stat().st_size > MAX_SCANNED_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    hits.append(f"{path.relative_to(PROJECT_ROOT)}: {label}")
        self.assertEqual(hits, [], "possible secrets found (values intentionally not printed)")

    def test_env_and_key_files_are_not_committed(self):
        names = {p.name for p in self.files}
        for forbidden in (".env", "id_rsa", "id_ed25519", "credentials.json", ".git-credentials"):
            self.assertNotIn(forbidden, names)
        self.assertFalse([p for p in self.files if p.suffix in {".pem", ".key", ".p12", ".pfx"}])

    def test_scanner_detects_planted_secrets(self):
        planted = {
            "AWS access key id": "AK" + "IA" + "A" * 16,
            "GitHub token": "gh" + "p_" + "a" * 30,
            "private key block": "-----BEGIN RSA PRIV" + "ATE KEY-----",
            "credential assignment": 'api_key = "' + "x1" * 10 + '"',
        }
        for label, sample in planted.items():
            with self.subTest(label):
                self.assertRegex(sample, SECRET_PATTERNS[label])


if __name__ == "__main__":
    unittest.main()
