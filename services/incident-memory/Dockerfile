FROM python:3.13-slim
WORKDIR /nexus
COPY services/incident-memory/requirements.txt /nexus/requirements.txt
RUN pip install --no-cache-dir -r /nexus/requirements.txt
COPY shared /nexus/shared
COPY contracts /nexus/contracts
COPY mocks /nexus/mocks
COPY ui /nexus/ui
COPY services/incident-memory /nexus/services/incident-memory
RUN useradd --uid 10001 --create-home nexus && mkdir -p /nexus/data && chown nexus:nexus /nexus/data
USER nexus
WORKDIR /nexus/services/incident-memory
ENV DATABASE_URL=sqlite:////nexus/data/memory.db
EXPOSE 8005
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
