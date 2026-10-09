FROM python:3.11-slim
WORKDIR /app/services/root-cause-analysis
COPY services/root-cause-analysis/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/root-cause-analysis/app ./app
COPY mocks /app/mocks
ENV M3_PROVIDER_MODE=real
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
