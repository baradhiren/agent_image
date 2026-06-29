CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS files (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    language     TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    qualname   TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    start_line INT NOT NULL,
    end_line   INT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id       BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    src_qualname  TEXT NOT NULL,
    dst_name      TEXT NOT NULL,
    dst_symbol_id BIGINT REFERENCES symbols(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL,
    resolution    TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_key    TEXT NOT NULL,
    qualname     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(:CODE_DIM) NOT NULL,
    UNIQUE (file_id, chunk_key)
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path         TEXT NOT NULL,
    chunk_key    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(:DOC_DIM) NOT NULL,
    UNIQUE (path, chunk_key)
);

CREATE TABLE IF NOT EXISTS spec_links (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_path       TEXT NOT NULL,
    symbol_qualname TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_queue (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    commit_sha  TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embedding_config (
    collection TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INT  NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_ref      TEXT NOT NULL,
    title         TEXT NOT NULL,
    assignee_role TEXT NOT NULL,
    branch        TEXT,
    status        TEXT NOT NULL DEFAULT 'in_progress',
    round         INT  NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending',
    review_notes  TEXT,
    summary       TEXT,
    artifacts     JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS code_chunks_embedding_idx ON code_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols (name);
CREATE INDEX IF NOT EXISTS symbols_qualname_idx ON symbols (qualname);
CREATE INDEX IF NOT EXISTS edges_dst_name_idx ON edges (dst_name);
CREATE INDEX IF NOT EXISTS edges_dst_symbol_idx ON edges (dst_symbol_id);
CREATE INDEX IF NOT EXISTS ingest_queue_status_idx ON ingest_queue (status);
