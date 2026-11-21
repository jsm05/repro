# Small image for the Docker sandbox tests (docker-tests.yml): Python, pytest, git.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git procps \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir pytest
WORKDIR /testbed
