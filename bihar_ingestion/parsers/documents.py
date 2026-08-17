"""Extract document metadata from a tender/PO detail payload.

Documents are embedded in the portal's ``templateMap`` / ``templates`` field
tree as attachment field values (see :func:`bihar_ingestion.utils.parse_attachment_value`).
This walker recursively finds those fields and pairs each with the most
meaningful human label available (falling back to the enclosing group name).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ..utils import clean_text, parse_attachment_value

_DOWNLOAD_PATH = "/vendor/downloadDynamicAttachment.action"

# Field/section names that are too generic to use as a document label.
_GENERIC_LABELS = {
    "attach file", "attachment", "attach_file", "file name", "file_name",
    "file", "document", "upload", "browse",
}


def _is_generic(label: str | None) -> bool:
    return label is None or label.strip().lower() in _GENERIC_LABELS


def _group_label(node: dict) -> str | None:
    """Return a label if this dict looks like a template group/section."""

    if any(k in node for k in ("fieldList", "subProcess", "templates")):
        return clean_text(node.get("longName")) or clean_text(node.get("description"))
    return None


def build_download_url(base_url: str, relative_path: str, original_file: str) -> str:
    query = urlencode({"relativePath": relative_path, "originalFile": original_file})
    return f"{base_url}{_DOWNLOAD_PATH}?{query}"


def extract_documents(payload: Any, base_url: str) -> list[dict[str, Any]]:
    """Return a de-duplicated list of document metadata dicts for a payload."""

    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any, inherited_label: str | None) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, inherited_label)
            return
        if not isinstance(node, dict):
            return

        current_label = _group_label(node) or inherited_label

        parsed = parse_attachment_value(node.get("value"))
        if parsed:
            field_label = clean_text(node.get("longName")) or clean_text(node.get("shortName"))
            label = field_label
            if _is_generic(field_label):
                label = current_label or field_label
            key = parsed["relative_path"]
            if key not in found:
                found[key] = {
                    "label": label,
                    "field_code": clean_text(node.get("code")),
                    "template_group_id": parsed["template_group_id"],
                    "filename": parsed["filename"],
                    "relative_path": parsed["relative_path"],
                    "file_size_bytes": parsed["file_size_bytes"],
                    "mime_type": parsed["mime_type"],
                    "download_url": build_download_url(
                        base_url, parsed["relative_path"], parsed["filename"]
                    ),
                }

        for value in node.values():
            walk(value, current_label)

    walk(payload, None)
    return list(found.values())
