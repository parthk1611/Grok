#!/usr/bin/env sh
# Offline secret scan (no network, stdlib python). See scripts/secret_scan.py for options.
set -eu
cd "$(dirname "$0")/.."
python3 scripts/secret_scan.py --self-test
exec python3 scripts/secret_scan.py "$@"
