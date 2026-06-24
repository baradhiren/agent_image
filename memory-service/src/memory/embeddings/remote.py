import httpx


class RemoteEmbeddingProvider:
    def __init__(self, base_url: str, dim: int, timeout: float = 60.0) -> None:
        self._url = base_url.rstrip("/")
        self._dim = dim
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = httpx.post(f"{self._url}/embed", json={"inputs": texts}, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()
