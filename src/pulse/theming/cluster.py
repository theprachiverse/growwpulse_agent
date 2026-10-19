"""Task 3.1: Over-clustering & Stratified Sampling.

Segments review embeddings into k=6..8 clusters via KMeans and extracts stratified
exemplars per cluster, balancing core centroids, high-impact upvotes/severity,
and store source representation (Play Store vs App Store).
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans

from pulse.models import CleanReview
from pulse.observability.logging import get_logger

logger = get_logger("pulse.theming.cluster")


def truncate_preview(text: str, max_words: int = 50, max_chars: int = 300) -> str:
    """Truncate text to max_words or max_chars while preserving word boundaries."""
    words = text.strip().split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]) + "..."
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def cluster_and_sample(
    reviews: list[CleanReview],
    embeddings: list[list[float]],
    k: int = 7,
    random_state: int = 42,
    samples_per_cluster: int = 15,
) -> tuple[dict[int, list[CleanReview]], dict[int, list[float]]]:
    """Perform KMeans over-clustering on review embeddings and extract stratified exemplars.

    Args:
        reviews: List of cleaned, pseudonymized reviews.
        embeddings: Dense vector representations aligned with reviews.
        k: Number of clusters to form (target 6..8).
        random_state: Fixed seed for deterministic clustering.
        samples_per_cluster: Number of exemplar reviews to sample per cluster (~15).

    Returns:
        tuple[dict[int, list[CleanReview]], dict[int, list[float]]]:
            - mapping of cluster_id -> list of sampled CleanReview exemplars
            - mapping of cluster_id -> centroid vector
    """
    if not reviews:
        return {}, {}

    if len(reviews) != len(embeddings):
        raise ValueError(
            f"Review count ({len(reviews)}) does not match embeddings count ({len(embeddings)})"
        )

    n_samples = len(reviews)
    actual_k = max(1, min(k, n_samples))

    x = np.array(embeddings, dtype=np.float32)
    kmeans = KMeans(n_clusters=actual_k, random_state=random_state, n_init="auto")
    labels = kmeans.fit_predict(x)
    centroids = kmeans.cluster_centers_

    cluster_samples: dict[int, list[CleanReview]] = {}
    cluster_centroids: dict[int, list[float]] = {}

    for c_idx in range(actual_k):
        centroid = centroids[c_idx]
        cluster_centroids[c_idx] = centroid.tolist()

        indices = np.where(labels == c_idx)[0]
        if len(indices) == 0:
            cluster_samples[c_idx] = []
            continue

        cluster_revs = [reviews[i] for i in indices]
        cluster_vecs = x[indices]

        # Euclidean distances to centroid
        dists = np.linalg.norm(cluster_vecs - centroid, axis=1)

        # 1. Prototypical: nearest to centroid (up to 8)
        nearest_order = np.argsort(dists)
        n_proto = min(8, len(indices))
        proto_indices = set(nearest_order[:n_proto].tolist())

        # 2. Impact / Severity: highest helpful votes and 1-2 star reviews (up to 4)
        def impact_score(r: CleanReview) -> float:
            severity_bonus = 50.0 if r.rating <= 2 else 0.0
            return float(r.helpful_votes) + severity_bonus

        impact_order = sorted(
            range(len(cluster_revs)),
            key=lambda idx: impact_score(cluster_revs[idx]),
            reverse=True,
        )
        impact_indices: set[int] = set()
        for idx in impact_order:
            if idx not in proto_indices:
                impact_indices.add(idx)
                if len(impact_indices) >= 4:
                    break

        # 3. Source Stratification: guarantee 2-3 App Store reviews if present in cluster
        app_store_indices: set[int] = set()
        selected_so_far = proto_indices | impact_indices
        app_candidates = [
            i
            for i, r in enumerate(cluster_revs)
            if r.source == "app_store" and i not in selected_so_far
        ]
        # Sort app candidates by distance to centroid for relevance
        app_candidates.sort(key=lambda i: dists[i])
        for idx in app_candidates[:3]:
            app_store_indices.add(idx)

        # Combine selected indices
        combined_indices = list(proto_indices | impact_indices | app_store_indices)
        # If still below target, fill with nearest remaining
        if len(combined_indices) < min(samples_per_cluster, len(indices)):
            for idx in nearest_order:
                if idx not in combined_indices:
                    combined_indices.append(idx)
                    if len(combined_indices) >= min(samples_per_cluster, len(indices)):
                        break

        # Sort exemplars by distance to centroid
        combined_indices.sort(key=lambda idx: dists[idx])
        exemplars = [cluster_revs[i] for i in combined_indices]
        cluster_samples[c_idx] = exemplars

    logger.info(
        "clustering_complete",
        total_reviews=n_samples,
        k=actual_k,
        cluster_sizes=[int(np.sum(labels == i)) for i in range(actual_k)],
        sampled_counts=[len(s) for s in cluster_samples.values()],
    )

    return cluster_samples, cluster_centroids
