FROM python:3.13-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PACKS_DIR=/srv/data/packs

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY data/ data/
COPY web/ web/

WORKDIR /srv/backend
# Load the country packs into the database, then start the API.
CMD python -m app.packs && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
