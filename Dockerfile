# Render-friendly image for the Instagram Dork Bot.
#
# Render auto-detects this file in the repo root and builds from it
# instead of using the native Python runtime. To force the Docker
# path in render.yaml, set `runtime: docker`.
#
# Local sanity check:
#   docker build -t salaz-bot .
#   docker run --rm -p 10000:10000 \
#     -e TELEGRAM_BOT_TOKEN=... -e SERPER_API_KEY=... \
#     -v $PWD/data:/var/data salaz-bot

FROM python:3.11-slim

# System deps: curl is used by the in-container healthcheck.
# build-essential is only needed if a wheel isn't available for a
# particular platform; slim + manylinux covers almost all of our deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the project source and register the package in site-packages.
COPY src ./src
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps . \
    && python -c "import instagram_dork_bot; print('import OK')"

# Persistent data directory — Render mounts a disk here when the
# service is configured with `disk.mountPath: /var/data`. Create the
# directory and give the `bot` user ownership so it can write there
# even when the disk isn't mounted (e.g. local Docker without -v).
RUN useradd --create-home --shell /bin/bash bot \
    && mkdir -p /var/data \
    && chown -R bot:bot /var/data
USER bot

ENV HISTORY_DB_PATH=/var/data/history.db \
    PORT=10000 \
    PYTHONUNBUFFERED=1

EXPOSE 10000

# Healthcheck for plain `docker run` (Render uses its own external
# probe against /health).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:10000/health || exit 1

CMD ["python", "-m", "instagram_dork_bot"]

