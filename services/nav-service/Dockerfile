# nav-service: container image.
# Slim official Python image, same major version as local development (3.11).
FROM python:3.11-slim

# No .pyc files in the image; log lines appear immediately (not buffered).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service

# 1. Dependencies first, in their own layer: rebuilt only when requirements.txt changes,
#    not on every code change, so rebuilds after a code edit take seconds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Then the code. Tests, .env and caches are excluded by .dockerignore.
COPY app ./app

# 3. Never run as root inside the container.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# --host 0.0.0.0: listen on all interfaces, otherwise nothing outside the container can connect.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
