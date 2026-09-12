ARG PYTHON_VERSION=3.13
ARG PYTHON_IMAGE=python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

# Stage 1: Builder
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.lock .
RUN pip install --only-binary :all: --no-cache-dir --require-hashes -r requirements.lock

# Stage 2: Production
FROM ${PYTHON_IMAGE} AS production
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG VERSION=development
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/cavack/WFH-ORG" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${VERSION}"
RUN groupadd -r waterfall && useradd -r -g waterfall -d /app waterfall
WORKDIR /app

COPY --from=builder --chown=waterfall:waterfall /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    SOURCE_REVISION="${VCS_REF}" \
    HOME="/tmp" \
    TMPDIR="/tmp" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

RUN mkdir -p /app/data && chown -R waterfall:waterfall /app/data
COPY --chown=waterfall:waterfall ./src /app/src

USER waterfall
EXPOSE 8000
CMD ["uvicorn", "waterfallhunter.main:app", "--host", "0.0.0.0", "--port", "8000"]
