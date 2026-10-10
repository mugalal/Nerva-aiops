FROM python:3.11-slim

WORKDIR /app/services/telemetry-intelligence

COPY services/telemetry-intelligence/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY services/telemetry-intelligence/app ./app

RUN useradd --uid 10001 --create-home nexus && chown -R nexus:nexus /app
USER nexus

ENV PYTHONPATH=/app \
    SERVICE_NAME=telemetry-intelligence \
    SERVICE_VERSION=0.1.0 \
    ENVIRONMENT=development \
    LOG_LEVEL=INFO

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
