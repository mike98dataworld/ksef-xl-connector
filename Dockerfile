# Usługa dla panelu BOK (Cloud Run). Dane firmy: KSEF_FIRMA=/secrets/firma.toml (sekret). Kod w /app, nie instalowany jako pakiet: walidacja szuka schemas/ obok pakietu.
FROM python:3.11-slim

WORKDIR /app
COPY requirements-serwis.txt .
RUN pip install --no-cache-dir -r requirements-serwis.txt

COPY schemas ./schemas
COPY ksef_connector ./ksef_connector

ENV PYTHONUNBUFFERED=1
CMD exec uvicorn ksef_connector.serwis:app --host 0.0.0.0 --port ${PORT:-8080}
