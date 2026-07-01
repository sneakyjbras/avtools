# syntax=docker/dockerfile:1
#
# avtools container image for the CERN OpenShift (PaaS) deployment.
# Two stages: build the wheel with poetry, then install it into a slim runtime.
# Build context MUST be the repo root (this Dockerfile COPYs pyproject/ + src/).

# ---- build stage: produce the avtools wheel ---------------------------------
# CERN's Docker Hub mirror avoids Docker Hub rate limits and is an allowed
# registry. If your build environment can't reach it, plain
# `python:3.11-slim` works too.
FROM registry.cern.ch/docker.io/library/python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false
RUN pip install "poetry>=1.8,<2.0"

WORKDIR /build
COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
RUN poetry build -f wheel

# ---- runtime stage ----------------------------------------------------------
FROM registry.cern.ch/docker.io/library/python:3.11-slim AS runtime

# avtools' CERN deps (eam_rest_client, landb_rest_client, cern_oauthlib) live on
# the ITDCIM internal index, supplemental to public PyPI. Overridable at build.
ARG PIP_EXTRA_INDEX_URL=https://pypi-itdcim.cern.ch/simple
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The app logs to console (captured by `oc logs`) AND a JSON file; point the
    # file at a writable path so it does not try to mkdir /var/log/avtools.
    AVTOOLS_LOG_FILE=/tmp/avtools/avtools.jsonl

# libpq for psycopg (harmless if the binary wheel is used); iputils-ping for the
# ICMP ping probes (see the CAP_NET_RAW note in README.md).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --extra-index-url "${PIP_EXTRA_INDEX_URL}" /tmp/*.whl \
    && rm -f /tmp/*.whl

# OpenShift runs containers as a random non-root UID in group 0. Make the log
# dir group-writable so it works under any injected UID.
RUN mkdir -p /tmp/avtools \
    && chgrp -R 0 /tmp/avtools \
    && chmod -R g=u /tmp/avtools

USER 1001
ENTRYPOINT ["avtools"]
# Default to the sharded SNMP sweep; --logs sends structured output to stdout.
CMD ["--logs", "snmp-timeseries"]
