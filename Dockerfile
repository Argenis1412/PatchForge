FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 patchforge \
    && useradd --uid 1000 --gid 1000 --create-home patchforge

RUN mkdir -p /repo /workspace \
    && chown patchforge:patchforge /repo /workspace \
    && chmod 777 /repo /workspace

COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY src/ src/
RUN pip install --no-cache-dir ".[dev]"

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATCHFORGE_WORKSPACE=/workspace \
    PATCHFORGE_DATA_DIR=/workspace/stores \
    HOME=/home/patchforge

WORKDIR /repo
USER patchforge

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["patchforge", "--help"]
