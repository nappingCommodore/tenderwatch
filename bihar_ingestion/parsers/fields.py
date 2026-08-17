"""Helpers for reading values out of the portal's nested template field tree.

Both tender details and purchase orders expose their business data as flat
"fields" (each with a ``code``/``shortName`` and a ``value``) nested inside
``templateMap`` / ``templates`` / ``templateFieldList`` structures. This module
provides a single recursive collector used by the parsers.
"""

from __future__ import annotations

from typing import Any, Iterable


def collect_field_values(payload: Any, wanted: Iterable[str]) -> dict[str, Any]:
    """Return the first ``value`` for each requested field code.

    A field matches when its ``code`` or ``shortName`` equals a wanted name.
    The first occurrence wins (portal payloads repeat some fields across
    sections). Only keys that are found are present in the result.
    """

    targets = set(wanted)
    found: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if len(found) == len(targets):
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            key = node.get("code") or node.get("shortName")
            if key in targets and key not in found and "value" in node:
                found[key] = node.get("value")
            for value in node.values():
                walk(value)

    walk(payload)
    return found
