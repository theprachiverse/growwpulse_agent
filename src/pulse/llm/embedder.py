"""Local text embeddings via scikit-learn: TF-IDF followed by truncated SVD (LSA).

No model download, no PyTorch, no API key. The vector space is fitted on a corpus
(`fit`) and every later `transform` projects into that same space, so review vectors and
short theme-definition vectors stay comparable. Rows are L2-normalized, so a dot product
is the cosine similarity.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from pulse.observability.logging import get_logger

logger = get_logger("pulse.llm.embedder")

DEFAULT_COMPONENTS = 128
_MIN_DF_CORPUS_SIZE = 20  # below this, keep rare terms: the corpus is too small to prune


class TfidfSvdEmbedder:
    """Corpus-fitted TF-IDF + truncated-SVD embedder producing unit-length dense vectors."""

    def __init__(self, n_components: int = DEFAULT_COMPONENTS, random_state: int = 42) -> None:
        self.n_components = n_components
        self.random_state = random_state
        self._tfidf: Any = None
        self._svd: Any = None

    @property
    def fitted(self) -> bool:
        return self._tfidf is not None

    def fit(self, texts: list[str]) -> None:
        """Learn vocabulary, IDF weights and the SVD projection from `texts`."""
        min_df = 2 if len(texts) >= _MIN_DF_CORPUS_SIZE else 1
        try:
            tfidf = self._new_vectorizer(min_df, stop_words="english")
            matrix = tfidf.fit_transform(texts)
        except ValueError:  # every term was a stop word or pruned: retry unfiltered
            tfidf = self._new_vectorizer(1, stop_words=None)
            matrix = tfidf.fit_transform(texts)

        n_samples, n_features = matrix.shape
        k = min(self.n_components, n_samples - 1, n_features - 1)
        svd = None
        if k >= 2:
            svd = TruncatedSVD(n_components=k, random_state=self.random_state)
            svd.fit(matrix)

        self._tfidf, self._svd = tfidf, svd
        logger.info(
            "embedder_fitted",
            docs=n_samples,
            vocab=n_features,
            dims=k if svd is not None else n_features,
        )

    def transform(self, texts: list[str]) -> list[list[float]]:
        """Project `texts` into the fitted space. Out-of-vocabulary texts become zero vectors."""
        if not self.fitted:
            raise RuntimeError("TfidfSvdEmbedder.transform called before fit")
        if not texts:
            return []
        matrix = self._tfidf.transform(texts)
        dense = self._svd.transform(matrix) if self._svd is not None else matrix.toarray()
        normed = normalize(np.asarray(dense, dtype=np.float64))
        return [[float(x) for x in row] for row in normed]

    @staticmethod
    def _new_vectorizer(min_df: int, stop_words: str | None) -> TfidfVectorizer:
        return TfidfVectorizer(
            lowercase=True,
            stop_words=stop_words,
            ngram_range=(1, 2),
            min_df=min_df,
            max_features=20000,
            sublinear_tf=True,
        )
