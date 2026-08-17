"""Pure helper functions shared across the pipeline (no I/O, easy to test)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

# Portal epochs are milliseconds. Values outside a sane range are treated as
# noise (the portal occasionally stores 0 or tiny sentinels).
_MIN_SANE_EPOCH_MS = 315_532_800_000        # 1980-01-01
_MAX_SANE_EPOCH_MS = 4_102_444_800_000       # 2100-01-01


def epoch_ms_to_iso(value: Any) -> str | None:
    """Convert an epoch-milliseconds value to an ISO-8601 UTC string."""

    epoch = coerce_int(value)
    if epoch is None or not (_MIN_SANE_EPOCH_MS <= epoch <= _MAX_SANE_EPOCH_MS):
        return None
    return datetime.fromtimestamp(epoch / 1000.0, tz=timezone.utc).isoformat()


def sane_epoch(value: Any) -> int | None:
    """Return the epoch-ms value if it falls in a plausible range, else None."""

    epoch = coerce_int(value)
    if epoch is None or not (_MIN_SANE_EPOCH_MS <= epoch <= _MAX_SANE_EPOCH_MS):
        return None
    return epoch


def coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Attachment field values look like one of:
#   "29679/1766756175102_NIT 17.pdf|NIT 17.pdf"                 -> path|name
#   "29692/1766756627574_NIT 17.pdf|8060784|NIT 17.pdf"         -> path|size|name
# The first segment (before the first '|') is the server-relative path used as
# the downloadDynamicAttachment `relativePath`; the last segment is the original
# file name; a numeric middle segment (when present) is the file size in bytes.
_PATH_RE = re.compile(r"^\d+/\d+_.+")
_FILE_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def parse_attachment_value(value: Any) -> dict[str, Any] | None:
    """Parse a templateMap attachment field value into document metadata.

    Returns ``None`` when the value is not a recognizable attachment reference.
    """

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if "|" not in raw or "/" not in raw:
        return None

    parts = [p.strip() for p in raw.split("|")]
    relative_path = parts[0]
    if not _PATH_RE.match(relative_path):
        return None

    file_size: int | None = None
    original_name: str | None = None
    if len(parts) == 2:
        original_name = parts[1]
    elif len(parts) >= 3:
        # middle segment is the size when it is purely numeric
        if parts[1].isdigit():
            file_size = int(parts[1])
            original_name = parts[2]
        else:
            original_name = parts[-1]

    if not original_name:
        # fall back to the filename embedded in the relative path
        original_name = relative_path.split("_", 1)[-1]

    if not _FILE_EXT_RE.search(original_name):
        return None

    template_group_id = coerce_int(relative_path.split("/", 1)[0])
    return {
        "relative_path": relative_path,
        "filename": original_name,
        "file_size_bytes": file_size,
        "template_group_id": template_group_id,
        "mime_type": guess_mime_type(original_name),
    }


_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "rar": "application/vnd.rar",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "txt": "text/plain",
    "csv": "text/csv",
}


def guess_mime_type(filename: str) -> str | None:
    match = _FILE_EXT_RE.search(filename or "")
    if not match:
        return None
    ext = match.group(0).lstrip(".").lower()
    return _MIME_BY_EXT.get(ext)


def parse_dept_path(query_string: Any) -> list[int]:
    """Parse a queryString like '538.1869.2254.2256.2273.' into ordered ids."""

    text = clean_text(query_string)
    if not text:
        return []
    ids = []
    for token in text.split("."):
        token = token.strip()
        if token.isdigit():
            ids.append(int(token))
    return ids
