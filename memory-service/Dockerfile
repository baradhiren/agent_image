FROM python:3.12-slim

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY sql ./sql
RUN uv sync

ENV DATABASE_URL=postgresql://postgres:postgres@db:5432/memory
CMD ["uv", "run", "python", "-m", "memory.mcp_server"]
