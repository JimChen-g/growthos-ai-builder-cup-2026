FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system growthos && adduser --system --ingroup growthos growthos

COPY pyproject.toml ./
COPY agents ./agents
COPY backend ./backend
COPY integrations ./integrations
COPY data ./data
RUN python -m pip install .

USER growthos
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn backend.api.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
