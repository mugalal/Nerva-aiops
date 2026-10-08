FROM python:3.12-slim
WORKDIR /app

COPY services/finops-engine/requirements.txt services/finops-engine/requirements.txt
RUN pip install --no-cache-dir -r services/finops-engine/requirements.txt

COPY shared shared
COPY services/finops-engine services/finops-engine
COPY services/shared-nexus-api/app/db services/shared-nexus-api/app/db

WORKDIR /app/services/finops-engine
EXPOSE 8006
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]