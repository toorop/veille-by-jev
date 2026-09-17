"""Data layout, URL fingerprints and file writing.

Pipeline stages talk to each other through files: this module centralises where
those files live and how they are written, so that no stage invents its own paths.
V1 has no database.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DIGEST_DIR = ROOT / "digest"
SEEN_PATH = ROOT / "seen.jsonl"


def data_dir(day: date) -> Path:
    """Return the per-day data directory, for example `data/2026-09-16/`."""
    return DATA_DIR / day.isoformat()


def enriched_dir(day: date) -> Path:
    """Return the directory holding the enriched items of `day`."""
    return data_dir(day) / "enriched"


def enriched_path(day: date, url: str) -> Path:
    """Return the enriched cache file of one URL for `day`."""
    return enriched_dir(day) / f"{url_fingerprint(url)}.json"


def items_path(day: date) -> Path:
    """Return the path of the collection output for `day`."""
    return data_dir(day) / "items.json"


def scores_path(day: date) -> Path:
    """Return the path of the triage output for `day`."""
    return data_dir(day) / "scores.json"


def digest_path(day: date) -> Path:
    """Return the path of the Markdown digest for `day`."""
    return DIGEST_DIR / f"{day.isoformat()}.md"


def url_fingerprint(url: str) -> str:
    """Return a stable fingerprint of a URL, used as a cache file name.

    Sixteen hexadecimal characters (64 bits): enough for a local cache, and
    readable in a tree that gets inspected by hand.
    """
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def iso_utc(moment: datetime) -> str:
    """Format an instant as an explicit UTC ISO 8601 string ending in `Z`.

    Every timestamp written to `data/` goes through here, so two files of the same run
    cannot disagree on the format.
    """
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically: temporary file first, then rename.

    An interrupted run therefore never leaves a truncated `items.json` behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    """Read and decode a UTF-8 JSON file."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
