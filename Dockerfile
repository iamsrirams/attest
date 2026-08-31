# Agent container. Runtime-agnostic (PLAN §3): the same image serves the API
# locally, on Bedrock AgentCore Runtime, or behind API Gateway.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer caches across source changes.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install "."

COPY agent/    ./agent/
COPY tools/    ./tools/
COPY api/      ./api/
COPY packet/   ./packet/
COPY controls/ ./controls/
COPY scripts/  ./scripts/

# Never run as root; the agent only needs read access to its own code.
RUN useradd --create-home --uid 10001 attest && chown -R attest:attest /app
USER attest

# Redaction defaults on. Turning it off is a deliberate act, not an omission.
ENV ATTEST_REDACT=1 \
    AWS_REGION=us-east-1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health').read()"

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
