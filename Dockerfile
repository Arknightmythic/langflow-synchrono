FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import duckdb; c = duckdb.connect(); [c.execute(f'INSTALL {e}') for e in ('httpfs', 'excel', 'mysql')]"

COPY engine ./engine
COPY app ./app
COPY worker ./worker
COPY schema ./schema
COPY tools ./tools

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1} $([ \"${ACCESS_LOG:-0}\" = 1 ] || echo --no-access-log)"]
