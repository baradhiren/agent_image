import pytest

from memory.config import Settings
from memory.db import apply_schema, connect

TABLES = [
    "files", "symbols", "edges", "code_chunks", "doc_chunks",
    "spec_links", "ingest_queue", "embedding_config",
]


@pytest.fixture()
def conn():
    connection = connect(Settings.from_env())
    apply_schema(connection)  # default 384/384 for tests
    for table in TABLES:
        connection.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
    yield connection
    connection.close()


@pytest.fixture()
def anyio_backend():
    return "asyncio"
