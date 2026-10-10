FROM python:3.11-slim

WORKDIR /app/services/shared-nexus-api

COPY services/shared-nexus-api/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY services/shared-nexus-api/install_kubectl.py /tmp/install_kubectl.py
RUN python /tmp/install_kubectl.py
COPY services/shared-nexus-api/app ./app
COPY shared /app/shared
ENV PYTHONPATH=/app
COPY mocks /app/mocks

RUN useradd --uid 10001 --create-home nexus && mkdir -p /data && chown -R nexus:nexus /data /app
USER nexus

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
