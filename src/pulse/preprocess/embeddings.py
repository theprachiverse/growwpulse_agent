"""Embeddings generation and disk caching (Task 2.5).

Embeds: `clean_title + ". " + clean_text` (or just `clean_text` if title is None).
Clients with a fixed embedding space cache by SHA-256 text hash in `data/cache/embeddings.npz`.
Corpus-fitted clients (TF-IDF/SVD) are refitted on every call instead, which is cheap.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

from pulse.models import CleanReview
from pulse.observability.logging import get_logger

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.embeddings")


@runtime_checkable
class CorpusEmbedder(Protocol):
    """A client whose embedding space must be fitted on the review corpus before `embed`."""

    def fit_embeddings(self, texts: list[str]) -> None: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def text_to_embed(clean_title: str | None, clean_text: str) -> str:
    """Compose the canonical text representation to be embedded."""
    title = clean_title.strip() if clean_title else ""
    body = clean_text.strip()
    if title:
        return f"{title}. {body}"
    return body


def compute_text_hash(text: str) -> str:
    """Compute 16-character hex digest of canonical text for cache keying."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class EmbeddingCache:
    """Persistent on-disk cache for dense review vectors."""

    def __init__(self, cache_dir: Path | str = "data/cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "embeddings.npz"
        self._cache: dict[str, list[float]] = {}
        self._load()

    def _load(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            with np.load(self.cache_file, allow_pickle=False) as data:
                hashes = data["hashes"]
                vectors = data["vectors"]
                for h, vec in zip(hashes, vectors, strict=False):
                    self._cache[str(h)] = vec.tolist()
            logger.debug("embedding_cache_loaded", size=len(self._cache))
        except Exception as exc:
            logger.warning("embedding_cache_load_failed", error=str(exc))
            self._cache.clear()

    def _save(self) -> None:
        if not self._cache:
            return
        try:
            hashes = np.array(list(self._cache.keys()))
            vectors = np.array(list(self._cache.values()), dtype=np.float64)
            tmp_path = self.cache_file.with_suffix(".tmp.npz")
            np.savez_compressed(tmp_path, hashes=hashes, vectors=vectors)
            tmp_path.replace(self.cache_file)
            logger.debug("embedding_cache_saved", size=len(self._cache))
        except Exception as exc:
            logger.warning("embedding_cache_save_failed", error=str(exc))

    def get(self, text_hash: str) -> list[float] | None:
        return self._cache.get(text_hash)

    def set(self, text_hash: str, vector: list[float]) -> None:
        self._cache[text_hash] = vector

    def save(self) -> None:
        self._save()

    def __len__(self) -> int:
        return len(self._cache)


def embed_reviews(
    reviews: list[CleanReview],
    client: LLMClient,
    cache_dir: Path | str = "data/cache",
    batch_size: int = 64,
) -> list[list[float]]:
    """Compute dense embeddings for clean reviews with caching.

    Returns:
        list[list[float]]: Dense vector embeddings in the exact order of input reviews.
    """
    if not reviews:
        return []

    canonical_texts = [text_to_embed(r.clean_title, r.clean_text) for r in reviews]

    if isinstance(client, CorpusEmbedder):
        # Corpus-fitted spaces (TF-IDF/SVD): a vector is only meaningful relative to the
        # corpus it was fitted on, so per-text caching would mix incompatible spaces.
        logger.info("fitting_embeddings", total=len(reviews))
        client.fit_embeddings(canonical_texts)
        return client.embed(canonical_texts)

    cache = EmbeddingCache(cache_dir=cache_dir)
    hashes = [compute_text_hash(t) for t in canonical_texts]

    # Identify cache misses
    missing_indices: list[int] = []
    missing_texts: list[str] = []
    for idx, (h, t) in enumerate(zip(hashes, canonical_texts, strict=True)):
        if cache.get(h) is None:
            missing_indices.append(idx)
            missing_texts.append(t)

    if missing_texts:
        logger.info(
            "computing_embeddings",
            total=len(reviews),
            cached=len(reviews) - len(missing_texts),
            missing=len(missing_texts),
        )
        newly_computed: list[list[float]] = []
        for i in range(0, len(missing_texts), batch_size):
            batch = missing_texts[i : i + batch_size]
            batch_vectors = client.embed(batch)
            newly_computed.extend(batch_vectors)

        for idx, vec in zip(missing_indices, newly_computed, strict=True):
            cache.set(hashes[idx], vec)

        cache.save()

    return [cache.get(h) for h in hashes]  # type: ignore[misc]
