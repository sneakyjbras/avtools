# syntax=docker/dockerfile:1
#
# avtools container image for the CERN Magnum (Kubernetes-on-OpenStack) deploy.
# Build context MUST be the repo root (COPYs pyproject/ + src/).
#
#   docker build -f deploy/k8s/Dockerfile \
#     --build-arg AVTOOLS_INDEX_URL=https://qa-pypi-itdcim.cern.ch/simple \
#     -t registry.cern.ch/itdcim/avtools:qa .
#
# CI passes the QA index for the :qa tag and the PROD index for :prod.

# ---- build stage: produce the avtools wheel ---------------------------------
FROM registry.cern.ch/docker.io/library/python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 POETRY_VIRTUALENVS_CREATE=false
RUN pip install "poetry>=2,<3"
WORKDIR /build
COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
RUN poetry build -f wheel

# ---- runtime stage ----------------------------------------------------------
FROM registry.cern.ch/docker.io/library/python:3.11-slim AS runtime

# CERN deps (eam_rest_client, landb_rest_client, cern_oauthlib) live on the
# ITDCIM index. QA vs PROD is chosen here so a QA image pulls QA packages.
ARG AVTOOLS_INDEX_URL=https://pypi-itdcim.cern.ch/simple
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AVTOOLS_LOG_FILE=/tmp/avtools/avtools.jsonl

# libpq for psycopg; iputils-ping for the ICMP probes (needs CAP_NET_RAW at runtime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/dist/*.whl /tmp/
# [sentry] extra installs sentry-sdk; init is a no-op unless SENTRY_DSN is set.
RUN pip install --extra-index-url "${AVTOOLS_INDEX_URL}" "$(echo /tmp/*.whl)[sentry]" \
    && rm -f /tmp/*.whl

# Non-root. The chart mounts an emptyDir at /tmp/avtools (readOnlyRootFilesystem).
RUN useradd --uid 1001 --create-home --shell /sbin/nologin avtools \
    && mkdir -p /tmp/avtools && chown -R 1001:1001 /tmp/avtools
USER 1001
ENTRYPOINT ["avtools"]
CMD ["--logs", "snmp-timeseries"]
