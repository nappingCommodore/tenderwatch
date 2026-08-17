# Hugging Face Space (Docker SDK) — Bihar Procurement Integrity Monitor
# Serves the FastAPI web app (web.app:app) against the slim data/bihar_web.db.
FROM python:3.11-slim

# HF Spaces run the container as UID 1000. Install + run as that user so the
# app directory (and the SQLite journal / casebook writes) stays writable.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BIHAR_DB=/home/user/app/data/bihar_web.db

WORKDIR /home/user/app

COPY --chown=user requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt

COPY --chown=user web/ ./web/
COPY --chown=user bihar_ingestion/ ./bihar_ingestion/
COPY --chown=user data/ ./data/
COPY --chown=user start.py ./start.py

# start.py binds $PORT when the platform injects one (Render/Railway), else 7860
# (HF/Fly), and fetches the DB from $DB_URL at boot if it isn't baked in.
EXPOSE 7860
CMD ["python", "start.py"]
