FROM python:3.11-slim
WORKDIR /app/services/root-cause-analysis
COPY services/root-cause-analysis/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/root-cause-analysis/app ./app
# M2's learned per-service normals: M3 measures evidence in the same usual deviations M2 alerts on.
COPY services/anomaly-engine/reference/reference.json /app/reference/reference.json
COPY mocks /app/mocks
RUN useradd --uid 10001 --create-home nexus && chown -R nexus:nexus /app
USER nexus
ENV M3_PROVIDER_MODE=real
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
