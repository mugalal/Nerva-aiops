FROM python:3.11-slim
WORKDIR /app
COPY services/anomaly-engine/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY shared /app/shared
COPY contracts /app/contracts
COPY mocks /app/mocks
COPY services/anomaly-engine /app/services/anomaly-engine
RUN useradd --uid 10001 --create-home nexus && mkdir -p /data && chown nexus:nexus /data
USER nexus
WORKDIR /app/services/anomaly-engine
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
