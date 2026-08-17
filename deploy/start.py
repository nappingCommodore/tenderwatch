#!/usr/bin/env python
"""Container entrypoint — cross-platform (no shell line-ending pitfalls).

* Binds $PORT if the platform injects one (Render / Railway), else 7860 (HF / Fly).
* Downloads the DB from $DB_URL at boot if it isn't baked into the image
  (e.g. the copy already hosted on your Hugging Face Space).
"""

import os
import sys
import urllib.request

db = os.environ.get("BIHAR_DB", "")
if db and not os.path.exists(db) and os.environ.get("DB_URL"):
    print(f"Database not found at {db} — downloading from $DB_URL ...", flush=True)
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    urllib.request.urlretrieve(os.environ["DB_URL"], db)
    print("Download complete.", flush=True)

port = os.environ.get("PORT") or "7860"
os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "web.app:app",
                           "--host", "0.0.0.0", "--port", port])
