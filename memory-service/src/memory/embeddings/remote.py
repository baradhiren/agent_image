import httpx


class RemoteEmbeddingProvider:
    def __init__(
        self, base_url: str, dim: int, timeout: float = 60.0, batch_size: int = 128
    ) -> None:
        self._url = base_url.rstrip("/")
        self._dim = dim
        self._timeout = timeout
        self._batch_size = batch_size

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Send fixed-size batches so a single huge file (e.g. a 16k-line
        # generated source) cannot produce one unbounded request that blows
        # past the timeout.
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            resp = httpx.post(
                f"{self._url}/embed", json={"inputs": batch}, timeout=self._timeout
            )
            resp.raise_for_status()
            out.extend(resp.json())
        return out
