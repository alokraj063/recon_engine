# syntax=docker/dockerfile:1
#
# Recon Alpha: one image serving the React UI at / and the API at /api.
#
#   docker build -t recon-engine .                      (from the repo root)
#
# Runtime configuration is environment only; nothing secret is baked in.
# See docs/configuration.md. On ECS the task definition sets the DB_* /
# S3_* settings (DB_USER / DB_PASSWORD from Secrets Manager) and a one-off
# task runs `python -m db.migrate` before each release.

########################################################################
# 1. frontend build
########################################################################
FROM node:24-slim AS frontend

WORKDIR /build
# lockfile first: the npm ci layer is reused until dependencies change
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


########################################################################
# 2. runtime
########################################################################
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend_dist

# Unprivileged user. The only writable path the app needs is scratch space
# (temp files during ingest/reconcile, and local storage if STORAGE_BACKEND
# is left at its default), so RECON_DATA_DIR points under /tmp: container
# disk is ephemeral anyway, and real deployments use RDS + S3.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin recon \
 && mkdir -p /tmp/recon-data \
 && chown recon /tmp/recon-data
USER 10001

# Container defaults; every one can be overridden by the task definition.
#   RUN_MIGRATIONS_ON_STARTUP=false  migrations are a separate one-off task
#   LOG_TO_FILE=false / LOG_FORMAT=json  stdout only, as JSON, for CloudWatch
ENV FRONTEND_DIST=/app/frontend_dist \
    RECON_DATA_DIR=/tmp/recon-data \
    RUN_MIGRATIONS_ON_STARTUP=false \
    LOG_TO_FILE=false \
    LOG_FORMAT=json \
    AWS_REGION=ap-south-1

EXPOSE 8080

# One worker per container: runs execute in the request threadpool (see
# CLAUDE.md); scale with more ECS tasks, not more workers.
# --proxy-headers: trust X-Forwarded-* from the ALB (the task only accepts
# traffic from the ALB security group).
# --timeout-keep-alive 310: longer than the ALB idle timeout (set that to
# 300s), so the ALB, not uvicorn, closes idle connections — otherwise the
# ALB can reuse a connection uvicorn just closed and return a 502.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-keep-alive", "310"]
