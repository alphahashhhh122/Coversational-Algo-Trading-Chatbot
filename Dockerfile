FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system iimc && adduser --system --ingroup iimc iimc

COPY pyproject.toml README.md ./
COPY iimc_trading_platform ./iimc_trading_platform
RUN python -m pip install --no-cache-dir .

COPY docs ./docs
COPY PROJECT_PLAN.md ./

RUN mkdir -p /app/data /app/artifacts && chown -R iimc:iimc /app
USER iimc

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=3)"

CMD ["python", "-m", "uvicorn", "iimc_trading_platform.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
