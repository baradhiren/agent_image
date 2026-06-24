from fastembed import TextEmbedding

_MODEL_DIMS = {"BAAI/bge-small-en-v1.5": 384}


class LocalEmbeddingProvider:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model = TextEmbedding(model_name=model_name)
        self._dim = _MODEL_DIMS.get(model_name, len(next(iter(self._model.embed(["probe"])))))

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
