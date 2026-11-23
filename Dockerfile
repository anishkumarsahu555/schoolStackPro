ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.5
ARG PYTHON_IMAGE=python:3.14-slim-bookworm
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS dependencies
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config default-libmysqlclient-dev libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

RUN find /app/.venv -path '*/playwright/driver/node' -type f -exec strip --strip-unneeded {} +

FROM dependencies AS browser
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
# Strip the Linux browser before copying it into the final image layer.
RUN /app/.venv/bin/playwright install --only-shell chromium \
    && find /opt/playwright -type f \( -name headless_shell -o -name chrome-headless-shell \) \
       -exec strip --strip-unneeded {} + \
    && chmod -R a+rX /opt/playwright

FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright STATIC_ROOT=/srv/static \
    MEDIA_ROOT=/srv/media WRITE_LOG_ON_FILE=false SCHOOLSTACK_CONTAINER=1
# Certificates use the headless shell. Keep its runtime libraries and fonts;
# the Xvfb display server pulls in a software OpenGL stack we do not use.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libmariadb3 libcairo2 libasound2 libatk-bridge2.0-0 libatk1.0-0 \
    libatspi2.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 \
    libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
    libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    libfontconfig1 libfreetype6 fonts-dejavu-core fonts-freefont-ttf \
    fonts-ipafont-gothic fonts-liberation fonts-noto-color-emoji \
    fonts-tlwg-loma-otf fonts-unifont fonts-wqy-zenhei xfonts-scalable \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=browser /opt/playwright /opt/playwright
COPY . .
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && install -d -o app -g app /srv/media /app/logs \
    && SECRET_KEY=build-only db=sqlite SQLITE_PATH=/tmp/build.sqlite3 python manage.py makemigrations --noinput \
    && SECRET_KEY=build-only db=sqlite python manage.py collectstatic --noinput --verbosity 0
USER app
EXPOSE 8000
CMD ["./start.sh", "--prod", "--check-migrations"]
