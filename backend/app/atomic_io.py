"""Atomic JSON write for cache files (temp file + fsync + os.replace).

Pattern ported (re-implemented, not copied) from `atomic_json` in
prototype_iteration_1/scripts/workflow_sources.py on branch
parth/iteration-1-prototype (not merged, per Team Lead). A failed write never
leaves a partial file or overwrites a good cache entry (cache_fallback.md).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".",
                                         suffix=".partial", delete=False, encoding="utf-8") as fh:
            tmp = Path(fh.name)
            json.dump(value, fh, ensure_ascii=False, allow_nan=False, indent=2)  # NaN/Inf rejected
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
