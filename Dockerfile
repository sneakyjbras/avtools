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

# ---- build stage: produce the avtools wheel + a locked constraints file -----
FROM registry.cern.ch/docker.io/library/python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 POETRY_VIRTUALENVS_CREATE=false
# poetry-plugin-export: `poetry export` was split out of core Poetry (>=2) into
# this plugin. Needed below to turn poetry.lock into a pip constraints file.
RUN pip install "poetry>=2,<3" "poetry-plugin-export>=1.8,<2"
WORKDIR /build
COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
# `poetry build` only packages avtools itself — it never touches poetry.lock,
# so it must be paired with `poetry export` (below) for the runtime install to
# actually be reproducible. `--only main` mirrors runtime reality (no lint/dev
# groups); `--extras sentry` covers the optional sentry-sdk pulled in below;
# `--without-urls` drops the CERN index URL poetry would otherwise embed in the
# file, so AVTOOLS_INDEX_URL (picked at runtime-stage build time) stays the
# only thing that decides QA vs PROD index — the constraints file only pins
# versions, it does not choose a source.
RUN poetry build -f wheel \
    && poetry export --format constraints.txt --output constraints.txt \
         --only main --extras sentry --without-hashes --without-urls

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

COPY --from=builder /build/dist/*.whl /build/constraints.txt /tmp/
# [sentry] extra installs sentry-sdk; init is a no-op unless SENTRY_DSN is set.
# -c constraints.txt forces pip to honour the versions poetry.lock resolved
# instead of re-resolving runtime deps from scratch (which previously let
# uncapped deps like eam/landb-rest-client drift silently between builds).
RUN pip install --extra-index-url "${AVTOOLS_INDEX_URL}" \
      -c /tmp/constraints.txt "$(echo /tmp/*.whl)[sentry]" \
    && rm -f /tmp/*.whl /tmp/constraints.txt

# Non-root. The chart mounts an emptyDir at /tmp/avtools (readOnlyRootFilesystem).
RUN useradd --uid 1001 --create-home --shell /sbin/nologin avtools \
    && mkdir -p /tmp/avtools && chown -R 1001:1001 /tmp/avtools
USER 1001
ENTRYPOINT ["avtools"]
CMD ["--logs", "snmp-timeseries"]
