ARG PYTHON_VERSION=3.13
ARG PYTHON_IMAGE=python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
FROM ${PYTHON_IMAGE}
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG VERSION=development
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/cavack/WFH-ORG" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${VERSION}"
RUN addgroup -S watchdog && adduser -S watchdog -G watchdog
WORKDIR /app

ENV HOME="/tmp" \
    TMPDIR="/tmp" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.lock .
RUN pip install --only-binary :all: --no-cache-dir --require-hashes -r requirements.lock

COPY main.py .
RUN chown -R watchdog:watchdog /app

USER watchdog
CMD ["python", "main.py"]
