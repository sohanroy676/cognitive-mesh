"""Embedding models (ADR-0014). The only module that imports `sentence_transformers`."""

from collections.abc import Sequence
from typing import Any, Final, Protocol

from cognitive_mesh.memory.documents import TEXT_FORMAT_VERSION
from cognitive_mesh.memory.models import MemoryIndexError, ModelKey

MODEL_NAME: Final = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION: Final = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_DIMENSION: Final = 384
BATCH_SIZE: Final = 32


class Embedder(Protocol):
    @property
    def key(self) -> ModelKey: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One normalised vector per text, each of length `key.dimension`."""
        ...


class SentenceTransformerEmbedder:
    """The pinned MiniLM model. Loaded on first use, never at import."""

    def __init__(self) -> None:
        self._model: Any = None
        self._key = ModelKey(
            name=MODEL_NAME,
            revision=MODEL_REVISION,
            dimension=MODEL_DIMENSION,
            text_format=TEXT_FORMAT_VERSION,
        )

    @property
    def key(self) -> ModelKey:
        return self._key

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION)
                dimension = model.get_embedding_dimension()
            except Exception:
                raise MemoryIndexError(
                    "cannot load the embedding model (is it downloaded, or is the network "
                    "reachable?)"
                ) from None
            if dimension != MODEL_DIMENSION:
                raise MemoryIndexError("the embedding model has an unexpected vector size")
            self._model = model
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=BATCH_SIZE,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [[float(x) for x in row] for row in vectors]
        except Exception:
            raise MemoryIndexError("the embedding model failed to embed the text") from None
