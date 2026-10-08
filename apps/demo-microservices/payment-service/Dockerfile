FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ARG VERSION=v1
ENV VERSION=${VERSION} \
    SERVICE_NAME=payment-service \
    ENVIRONMENT=development \
    LOG_LEVEL=INFO \
    CPU_LIMIT_CORES=1 \
    MEMORY_LIMIT_BYTES=536870912
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
