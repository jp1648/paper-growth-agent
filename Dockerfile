FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/paper-agent
COPY pyproject.toml uv.lock ./
COPY trader ./trader
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 \
    && uv sync --locked --no-dev --no-editable
ENV PATH="/opt/venv/bin:$PATH"
ENTRYPOINT ["uv", "run", "--no-project", "--env-file", "/run/secrets/trader_env", "/opt/venv/bin/paper-agent", "--config", "/run/config.toml"]
CMD ["listen"]
