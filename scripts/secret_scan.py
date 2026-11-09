#!/usr/bin/env python3
"""Offline secret scanner (stdlib only). Part of T0.6 CI; also runnable locally:

    scripts/secret_scan.sh               # scan tracked + untracked-not-ignored files
    scripts/secret_scan.sh --history     # also scan added lines in all git history
    scripts/secret_scan.sh --self-test   # prove the patterns detect known fake samples

Exit 0 = clean, 1 = findings (values are masked in output), 2 = usage/self-test error.
Add `secret-scan: allow` on a line to suppress a reviewed false positive.
This is a simple regex scanner, not a replacement for a full tool such as gitleaks;
it catches the common token shapes, private keys and committed .env/key files.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 2_000_000
ALLOW_MARK = "secret-scan: allow"

# Samples are assembled at runtime so this file never contains a literal match.
PATTERNS: dict[str, "re.Pattern[str]"] = {
    "aws-access-key-id": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "openai-style-key": re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_\-]{32,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "url-with-password": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s:/@]{6,}@[^\s/]+"),
    "generic-assignment": re.compile(
        r"""(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)\b"""
        r"""\s*[:=]\s*['"][A-Za-z0-9_\-/+=.]{16,}['"]"""),
}
BAD_FILENAMES = re.compile(r"(^|/)(\.env(\.(?!example$)[^/]+)?|id_rsa|id_ed25519|.*\.pem|.*\.p12|.*\.pfx|.*\.key)$")
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|changeme|your[_-]|xxxx|<[^>]+>|\$\{|\$\()")


def mask(s: str) -> str:
    return s[:4] + "…" + f"({len(s)} chars)"


def scan_text(name: str, text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for kind, rx in PATTERNS.items():
            m = rx.search(line)
            if m and not (kind in ("generic-assignment", "url-with-password") and PLACEHOLDER.search(m.group(0))):
                yield name, lineno, kind, mask(m.group(0))


def files(root: Path):
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                         capture_output=True, check=True).stdout.decode("utf-8", "replace")
    for rel in filter(None, out.split("\0")):
        p = root / rel
        if p.is_file() and not p.is_symlink():
            yield rel, p


def scan_tree(root: Path):
    findings = []
    for rel, p in files(root):
        if BAD_FILENAMES.search(rel):
            findings.append((rel, 0, "forbidden-filename", rel))
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        findings.extend(scan_text(rel, data.decode("utf-8", "replace")))
    return findings


def scan_history(root: Path):
    proc = subprocess.run(["git", "log", "--all", "-p", "--no-color", "--diff-filter=AM", "-U0", "--format=commit %h"],
                          cwd=root, capture_output=True, check=True)
    findings, commit, path = [], "?", "?"
    for raw in proc.stdout.decode("utf-8", "replace").splitlines():
        if raw.startswith("commit "):
            commit = raw[7:]
        elif raw.startswith("+++ b/"):
            path = raw[6:]
        elif raw.startswith("+") and not raw.startswith("+++") and len(raw) < 2000:
            findings.extend(scan_text(f"{commit}:{path}", raw[1:]))
    return findings


def self_test() -> int:
    samples = {
        "aws-access-key-id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "private-key-block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "github-token": "gh" + "p_" + "a" * 36,
        "slack-token": "xo" + "xb-" + "1234567890-abcdef",
        "google-api-key": "AI" + "za" + "A" * 35,
        "openai-style-key": "sk" + "-" + "a1" * 20,
        "jwt": "ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 12,
        "url-with-password": "postgres" + "://user:hunter22xx@db.internal/x",
        "generic-assignment": 'api_' + 'key = "' + "Zx9" * 8 + '"',
    }
    bad = 0
    for kind, sample in samples.items():
        got = {k for _, _, k, _ in scan_text("t", sample)}
        if kind not in got:
            print(f"SELF-TEST FAIL: {kind} not detected", file=sys.stderr)
            bad += 1
    clean = ['password = "changeme-changeme-1234"', "RELAY_PORT=8000", "api_key = os.environ['X']",
             'token = "<your-token-goes-here-123456>"', "see https://example.com/path", 'x = "' + "a" * 40 + '"  # secret-scan: allow']
    for line in clean:
        if list(scan_text("t", line)):
            print(f"SELF-TEST FAIL: false positive on {line!r}", file=sys.stderr)
            bad += 1
    for name in (".env", "keys/server.pem", "id_rsa"):
        if not BAD_FILENAMES.search(name):
            print(f"SELF-TEST FAIL: filename {name} not flagged", file=sys.stderr)
            bad += 1
    if BAD_FILENAMES.search(".env.example"):
        print("SELF-TEST FAIL: .env.example must be allowed", file=sys.stderr)
        bad += 1
    print("self-test:", "FAILED" if bad else f"ok ({len(samples)} patterns, {len(clean)} clean lines)")
    return 2 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", action="store_true", help="also scan lines added in all git history")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    root = Path(a.root).resolve()
    findings = scan_tree(root) + (scan_history(root) if a.history else [])
    for name, lineno, kind, shown in findings:
        print(f"{name}:{lineno}: {kind}: {shown}")
    n_files = sum(1 for _ in files(root))
    print(f"secret scan: {len(findings)} finding(s) in {n_files} files" + (" + history" if a.history else ""))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
