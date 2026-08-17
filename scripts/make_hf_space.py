#!/usr/bin/env python
"""Assemble a ready-to-push Hugging Face Space folder (Docker SDK) for the web app.

Stages only what the Space needs into ./hf_space/:
  Dockerfile, requirements.txt, README.md, .gitattributes, .dockerignore,
  web/, bihar_ingestion/{__init__,geo}.py, data/bihar_web.db, data/geo/*.geojson

Run scripts/build_web_db.py first (creates data/bihar_web.db). Preserves an
existing hf_space/.git so you can re-run to update an already-pushed Space.

Usage:  python scripts/make_hf_space.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=IGNORE)


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage a deploy bundle for the web app.")
    ap.add_argument("out", nargs="?", default="hf_space", help="output folder (default: hf_space)")
    ap.add_argument("--no-db", action="store_true",
                    help="omit the 484MB DB; the container downloads it at boot via $DB_URL "
                         "(use for a GitHub/Render repo)")
    args = ap.parse_args()
    out = ROOT / args.out

    db = ROOT / "data" / "bihar_web.db"
    if not args.no_db and not db.exists():
        sys.exit("data/bihar_web.db missing — run: python scripts/build_web_db.py")

    out.mkdir(parents=True, exist_ok=True)
    # deploy scaffolding (Dockerfile, entrypoint, platform configs, dotfiles)
    for name in ("Dockerfile", "start.py", "requirements.txt", "README.md",
                 "fly.toml", "render.yaml",
                 ".gitattributes", ".dockerignore", ".gitignore"):
        copy_file(ROOT / "deploy" / name, out / name)

    # application code
    copy_tree(ROOT / "web", out / "web")

    # only the standalone geo helper from the pipeline package is used at runtime
    (out / "bihar_ingestion").mkdir(parents=True, exist_ok=True)
    copy_file(ROOT / "bihar_ingestion" / "__init__.py", out / "bihar_ingestion" / "__init__.py")
    copy_file(ROOT / "bihar_ingestion" / "geo.py", out / "bihar_ingestion" / "geo.py")
    copy_file(ROOT / "data" / "geo" / "bihar_districts.geojson",
              out / "data" / "geo" / "bihar_districts.geojson")

    if args.no_db:
        # keep the big DB out of this (GitHub) repo — fetched at boot via $DB_URL
        gi = out / ".gitignore"
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if "bihar_web.db" not in text:
            gi.write_text(text.rstrip() +
                          "\n# DB downloaded at boot via $DB_URL — never committed\ndata/bihar_web.db\n",
                          encoding="utf-8")
    else:
        copy_file(db, out / "data" / "bihar_web.db")

    # tidy any bytecode caches (e.g. left by a prior self-test run)
    for cache in out.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    print(f"Staged bundle in: {out}")
    if args.no_db:
        print("  (code only — the DB is fetched at boot from $DB_URL set in render.yaml)\n")
        print("Push to GitHub, then connect it on Render:")
        print(f"  cd {args.out}")
        print("  git init -b main")
        print('  git add . && git commit -m "Bihar Procurement Integrity Monitor"')
        print("  git remote add origin https://github.com/<you>/<repo>.git")
        print("  git push -u origin main")
        print("  Then on Render: New -> Blueprint -> pick the repo (it reads render.yaml).")
    else:
        mb = db.stat().st_size / 1024 / 1024
        print(f"  data/bihar_web.db  {mb:,.1f} MB (Git-LFS tracked)\n")
        print("Next steps (Hugging Face / Fly):")
        print("  cd hf_space && git init -b main && git lfs install")
        print('  git add . && git commit -m "Bihar Procurement Integrity Monitor"')
        print("  # HF:  git remote add origin https://huggingface.co/spaces/<user>/<space> ; git push -u origin main")
        print("  # Fly: fly launch")


if __name__ == "__main__":
    main()
