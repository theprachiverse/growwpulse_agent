"""Tests for the scikit-learn TF-IDF/SVD embedder and its corpus-fitting integration."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from pulse.llm.client import GroqLocalEmbedClient
from pulse.llm.embedder import TfidfSvdEmbedder
from pulse.models import CleanReview
from pulse.preprocess.embeddings import embed_reviews

CORPUS = [
    "app crashes on login and keeps freezing",
    "login otp never arrives, cannot sign in",
    "great mutual fund sip experience, smooth investing",
    "sip mutual fund returns are tracked well",
    "options chart lags during market open for f&o trading",
    "f&o trading charts freeze at market open",
    "customer support never replies to my ticket",
    "support ticket unresolved, no reply from customer care",
]


def test_transform_before_fit_raises() -> None:
    with pytest.raises(RuntimeError):
        TfidfSvdEmbedder().transform(["hello"])


def test_vectors_are_unit_length_and_semantically_ordered() -> None:
    emb = TfidfSvdEmbedder(n_components=4)
    emb.fit(CORPUS)
    vecs = np.array(emb.transform(CORPUS))

    assert vecs.shape[0] == len(CORPUS)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-6)

    sim = vecs @ vecs.T
    # Reviews about the same topic are closer than reviews about different topics.
    assert sim[2, 3] > sim[2, 6]
    assert sim[4, 5] > sim[4, 0]


def test_new_texts_land_in_the_fitted_space() -> None:
    emb = TfidfSvdEmbedder(n_components=4)
    emb.fit(CORPUS)
    review_vecs = np.array(emb.transform(CORPUS))
    theme_vec = np.array(emb.transform(["Support: customer support ticket reply"])[0])

    assert theme_vec.shape[0] == review_vecs.shape[1]
    assert int(np.argmax(review_vecs @ theme_vec)) in (6, 7)


def test_out_of_vocabulary_text_is_zero_vector() -> None:
    emb = TfidfSvdEmbedder(n_components=4)
    emb.fit(CORPUS)
    assert not any(emb.transform(["zzzz qqqq"])[0])


def test_tiny_corpus_does_not_crash() -> None:
    emb = TfidfSvdEmbedder()
    emb.fit(["only one review here"])
    assert len(emb.transform(["only one review here"])) == 1


def test_embed_reviews_fits_client_and_skips_cache(tmp_path: Path) -> None:
    client = GroqLocalEmbedClient(api_key="unused", embed_dim=4)
    reviews = [
        CleanReview(
            rid=f"r{i}",
            source="play_store",
            rating=3,
            clean_title=None,
            clean_text=text,
            date=date(2026, 10, 14),
            iso_week="2026-W42",
        )
        for i, text in enumerate(CORPUS)
    ]

    vectors = embed_reviews(reviews, client, cache_dir=tmp_path / "cache")

    assert len(vectors) == len(reviews)
    assert client.embedder.fitted
    assert not (tmp_path / "cache" / "embeddings.npz").exists()
    # Theme-definition embeddings share the review space.
    assert len(client.embed(["login problems"])[0]) == len(vectors[0])
