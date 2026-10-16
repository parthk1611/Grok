"""Frozen local-date partitions for the v1 ignition hindcast; no fitted state."""

import numpy as np


PARTITION_CODES = {"development": 0, "calibration": 1, "threshold": 2, "test": 3}
# First training year, last training year, validation year.
FOLD_YEARS = tuple((2010, year - 1, year) for year in range(2012, 2017))
LABEL_TIMEZONE = "America/Toronto"
PURGE_GAP_DAYS = 0


def split_boundaries():
    """Return inclusive observed-date bounds; only May–October dates exist."""
    return {
        "development": {"start": "2010-05-01", "end": "2016-10-31"},
        "calibration": {"start": "2017-05-01", "end": "2017-10-31"},
        "threshold": {"start": "2018-05-01", "end": "2018-10-31"},
        "test": {"start": "2019-05-01", "end": "2019-10-31"},
    }


def _years(dates):
    values = np.asarray(dates)
    if values.ndim != 1:
        raise ValueError("ignition_date must be a one-dimensional sequence")
    if values.dtype.kind != "M":
        values = values.astype(str)
        if np.any(np.char.str_len(values) != 10):
            raise ValueError("Use local YYYY-MM-DD ignition dates, not timestamps")
    try:
        days = values.astype("datetime64[D]")
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid ignition_date") from error
    if np.any(np.isnat(days)):
        raise ValueError("ignition_date cannot be missing")
    if values.dtype.kind == "M" and np.any(values != days):
        raise ValueError("Use local calendar dates, not intraday timestamps")
    if np.any((days < np.datetime64("2010-05-01")) | (days > np.datetime64("2019-10-31"))):
        raise ValueError("Dates fall outside the frozen 2010–2019 panel")
    years = days.astype("datetime64[Y]").astype(np.int16) + 1970
    months = days.astype("datetime64[M]").astype(np.int64) % 12 + 1
    if np.any((years < 2010) | (years > 2019) | (months < 5) | (months > 10)):
        raise ValueError("Dates must belong to the frozen May–October 2010–2019 panel")
    return years


def get_partition(dates):
    """Assign uint8 codes 0=development, 1=calibration, 2=threshold, 3=test.

    Assignment depends only on the local ignition date, so every cell on the
    same date receives the same partition regardless of row order or labels.
    """
    return np.clip(_years(dates) - 2016, 0, 3).astype(np.uint8)


def development_folds(dates=None):
    """Yield forward-only fold bounds, optionally adding aligned Boolean masks.

    Omit dates to use bounds with contiguous disk-backed year slices. When
    dates are supplied, each yielded fold holds only its two current masks.
    Labels cover one local calendar day; past-input rolling windows do not
    overlap future label windows and therefore require no extra purge gap.
    """
    years = None if dates is None else _years(dates)
    for first, last, validation in FOLD_YEARS:
        fold = {
            "name": f"validate_{validation}",
            "train_start": f"{first}-05-01",
            "train_end": f"{last}-10-31",
            "validation_start": f"{validation}-05-01",
            "validation_end": f"{validation}-10-31",
        }
        if years is not None:
            fold.update(
                train_mask=(years >= first) & (years <= last),
                validation_mask=years == validation,
            )
        yield fold
