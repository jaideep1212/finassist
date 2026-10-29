# Excel loader: runs ONCE as a Kubernetes Job to fill an empty database from a workbook.
# The workbook and schema.sql are not in the image; the Job mounts them from a ConfigMap.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /loader

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY load_excel.py .

RUN useradd --create-home --uid 10001 appuser
USER appuser

ENTRYPOINT ["python", "load_excel.py"]
