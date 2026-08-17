"""Extract JSON API responses from the captured HAR into fixture files.

This is a development helper. It reads the recorded HAR, pulls every REST/JSON
response body, and writes each one to ``tests/fixtures/har/`` so the parsers can
be designed and unit-tested against the portal's real payloads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
HAR_PATH = ROOT / "eproc2.bihar.gov.in_updated.har"
OUT_DIR = ROOT / "tests" / "fixtures" / "har"


def slugify(url: str, index: int) -> str:
    parsed = urlparse(url)
    path = parsed.path.replace("/EPSV2Web/", "").strip("/")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{path}_{parsed.query}").strip("_")
    return f"{index:02d}_{slug[:80]}"


def main() -> None:
    har = json.loads(HAR_PATH.read_text(encoding="utf-8"))
    entries = har["log"]["entries"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = []
    index = 0
    for entry in entries:
        request = entry["request"]
        response = entry["response"]
        content = response.get("content", {})
        mime = content.get("mimeType", "")
        text = content.get("text")
        if "json" not in mime or not text:
            continue
        # Only keep the REST API calls, skip static assets that happen to be json.
        url = request["url"]
        if "/rest/" not in url:
            continue
        try:
            parsed_body = json.loads(text)
        except json.JSONDecodeError:
            continue
        index += 1
        name = slugify(url, index)
        out_path = OUT_DIR / f"{name}.json"
        out_path.write_text(json.dumps(parsed_body, indent=2), encoding="utf-8")

        if isinstance(parsed_body, dict):
            top_level_type = "object"
            top_level_keys = sorted(parsed_body.keys())
        elif isinstance(parsed_body, list):
            top_level_type = f"list[{len(parsed_body)}]"
            first = parsed_body[0] if parsed_body else None
            top_level_keys = sorted(first.keys()) if isinstance(first, dict) else []
        else:
            top_level_type = type(parsed_body).__name__
            top_level_keys = []

        manifest.append(
            {
                "file": out_path.name,
                "method": request["method"],
                "url": url,
                "status": response["status"],
                "top_level_type": top_level_type,
                "top_level_keys": top_level_keys,
            }
        )

    (OUT_DIR / "_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Extracted {len(manifest)} JSON responses to {OUT_DIR}")
    for item in manifest:
        print(f"  {item['file']:45s} {item['method']:5s} {item['url']}")


if __name__ == "__main__":
    main()
