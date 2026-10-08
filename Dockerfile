FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 gsoi
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY alembic.ini ./
COPY migrations ./migrations
USER gsoi
EXPOSE 8000
CMD ["uvicorn", "gsoi_assistant.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
