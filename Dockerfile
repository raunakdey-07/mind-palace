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

# Binds loopback, not every interface.
#
# It used to be `0.0.0.0`, which combined with `VERILORE_AUTH_MODE=off` being
# the default to mean that `docker run` published an unauthenticated API to
# whatever network the container landed on. `VERILORE_DEPLOYMENT` defaults to
# `local`, and a local profile answering on a public interface is exactly the
# accident the profile is meant to make loud.
#
# Exposing it is now a deliberate act, and the two halves go together:
#
#   docker run -p 8000:8000 \
#     -e VERILORE_DEPLOYMENT=server \
#     -e VERILORE_AUTH_MODE=required \
#     -e VERILORE_INGEST_ROOTS=/content \
#     -e DATABASE_URL=... verilore \
#     verilore uvicorn api.main:app --host 0.0.0.0 --port 8000
#
# `VERILORE_DEPLOYMENT=server` alone refuses to start unless authentication is
# on and the database role enforces row-level security, so the host argument and
# the profile cannot drift apart unnoticed.
CMD ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"]
