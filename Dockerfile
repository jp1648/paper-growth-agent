FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
COPY --from=ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb /uv /usr/local/bin/uv
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
