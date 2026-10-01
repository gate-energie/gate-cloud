# Dagster user-code image: the code server and every run pod use it.
FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --uid 10001 --create-home gate \
 # The chart mounts only dagster.yaml (subPath) into DAGSTER_HOME, so the
 # directory itself comes from the image and must be writable for local
 # storage and temp dirs when running as non-root.
 && mkdir -p /opt/dagster/dagster_home \
 && chown 10001:10001 /opt/dagster/dagster_home /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DAGSTER_HOME=/opt/dagster/dagster_home
USER 10001
EXPOSE 3030
CMD ["dagster", "api", "grpc", "-h", "0.0.0.0", "-p", "3030", "--module-name", "gate_cloud.definitions"]
