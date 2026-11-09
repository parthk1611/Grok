# RELAY backend (mock mode today). Build from the REPO ROOT so contracts/ is included:
#   docker build -f backend/Dockerfile -t comp385-backend .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RELAY_HOST=0.0.0.0 \
    RELAY_PORT=8000 \
    RELAY_DATA_MODE=mock

RUN useradd --system --uid 10001 --no-create-home relay
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/__init__.py backend/__init__.py
COPY backend/app backend/app
COPY contracts contracts

USER relay
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('RELAY_PORT','8000')+'/healthz',timeout=2)"
CMD ["sh", "-c", "exec uvicorn backend.app.main:get_app --factory --host \"$RELAY_HOST\" --port \"$RELAY_PORT\" --no-server-header --no-access-log"]
