FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Docker Desktop Images → Run 해도 compose DB(5433) 또는 맥 Postgres(5432)에 붙게 한다.
ENV DATABASE_URL=postgresql+psycopg://postgres:1234@host.docker.internal:5433/mediscan_note

COPY docker/app-entrypoint.sh /usr/local/bin/brain_fast_entrypoint.sh
RUN chmod +x /usr/local/bin/brain_fast_entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/brain_fast_entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]