from starlette.testclient import TestClient

from memory.embeddings_server import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200


def test_embed_returns_one_vector_per_input():
    r = client.post("/embed", json={"inputs": ["hello world", "def foo(): pass"]})
    assert r.status_code == 200
    vectors = r.json()
    assert len(vectors) == 2
    assert all(len(v) == 384 for v in vectors)


def test_embed_coerces_single_string():
    r = client.post("/embed", json={"inputs": "single"})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_embed_empty_list():
    r = client.post("/embed", json={"inputs": []})
    assert r.status_code == 200
    assert r.json() == []
