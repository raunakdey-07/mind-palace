FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HOME=/tmp/huggingface \
    XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app

COPY requirements-runtime.txt requirements-docker.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements-docker.txt

COPY api ./api
COPY migrations ./migrations
COPY content ./content

EXPOSE 8000

USER 10001:10001

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
