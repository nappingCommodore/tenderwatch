"""Best-effort mapping of a department name to a Bihar district (backend copy).

Most department names embed their district / HQ town (e.g. "ROAD DIVISION
KATIHAR"). We scan for any of the 38 districts or a common HQ alias, longest
keyword first so specific names win over short/ambiguous ones.
"""

from __future__ import annotations

import re

_DISTRICT_KEYWORDS: dict[str, list[str]] = {
    "Patna": ["patna"],
    "Gaya": ["gaya"],
    "Nalanda": ["nalanda", "bihar sharif", "biharsharif", "bihar-sharif"],
    "Nawada": ["nawada"],
    "Aurangabad": ["aurangabad"],
    "Jehanabad": ["jehanabad"],
    "Arwal": ["arwal"],
    "Rohtas": ["rohtas", "sasaram"],
    "Kaimur": ["kaimur", "bhabhua", "bhabua"],
    "Bhojpur": ["bhojpur", "arrah", "ara"],
    "Buxar": ["buxar"],
    "Bhagalpur": ["bhagalpur"],
    "Banka": ["banka"],
    "Munger": ["munger"],
    "Jamui": ["jamui"],
    "Lakhisarai": ["lakhisarai"],
    "Sheikhpura": ["sheikhpura"],
    "Khagaria": ["khagaria"],
    "Begusarai": ["begusarai"],
    "Saharsa": ["saharsa"],
    "Madhepura": ["madhepura"],
    "Supaul": ["supaul"],
    "Purnia": ["purnia", "purnea"],
    "Kishanganj": ["kishanganj"],
    "Araria": ["araria"],
    "Katihar": ["katihar"],
    "Darbhanga": ["darbhanga"],
    "Madhubani": ["madhubani"],
    "Samastipur": ["samastipur"],
    "Muzaffarpur": ["muzaffarpur"],
    "Sitamarhi": ["sitamarhi"],
    "Sheohar": ["sheohar"],
    "Vaishali": ["vaishali", "hajipur"],
    "East Champaran": ["east champaran", "purbi champaran", "motihari"],
    "West Champaran": ["west champaran", "pashchim champaran", "paschim champaran", "bettiah"],
    "Gopalganj": ["gopalganj"],
    "Siwan": ["siwan"],
    "Saran": ["saran", "chapra", "chhapra"],
}

_KEYWORDS: list[tuple[str, str]] = sorted(
    ((kw, dist) for dist, kws in _DISTRICT_KEYWORDS.items() for kw in kws),
    key=lambda x: len(x[0]),
    reverse=True,
)
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE), dist)
    for kw, dist in _KEYWORDS
]

DISTRICTS: list[str] = sorted(_DISTRICT_KEYWORDS)


def district_of(name: str | None):
    """Return the Bihar district embedded in a department name, or None."""
    if not name:
        return None
    for pat, dist in _PATTERNS:
        if pat.search(name):
            return dist
    return None
