"""Pure metric functions for the offline eval suite (no I/O, no LLM)."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

OTHER = "other"


def map_themes_to_golden(
    pred: Mapping[str, str],
    gold: Mapping[str, str],
) -> dict[str, str]:
    """One-to-one map of discovered theme ids -> golden labels (Hungarian, max agreement).

    `pred`/`gold` are rid -> theme_id / golden label over the same rids. Discovered `other`
    always maps to golden `other`; unmatched discovered themes map to `other`.
    """
    themes = sorted({t for t in pred.values() if t != OTHER})
    labels = sorted({g for g in gold.values() if g != OTHER})
    mapping: dict[str, str] = {OTHER: OTHER}
    if not themes or not labels:
        return {**mapping, **{t: OTHER for t in themes}}
    counts: Counter[tuple[str, str]] = Counter(
        (pred[r], gold[r]) for r in gold if r in pred and pred[r] != OTHER
    )
    matrix = np.array([[counts[(t, g)] for g in labels] for t in themes], dtype=float)
    rows, cols = linear_sum_assignment(-matrix)
    for i, j in zip(rows, cols, strict=True):
        if matrix[i, j] > 0:
            mapping[themes[i]] = labels[j]
    for t in themes:
        mapping.setdefault(t, OTHER)
    return mapping


def macro_f1(
    pred: Mapping[str, str],
    gold: Mapping[str, str],
) -> tuple[float, dict[str, dict[str, float]]]:
    """Macro-F1 over golden labels excluding `other`, after Hungarian theme mapping.

    A golden label with no matched theme scores F1 = 0. Returns (macro_f1, per_label metrics).
    """
    mapping = map_themes_to_golden(pred, gold)
    mapped = {r: mapping.get(pred.get(r, OTHER), OTHER) for r in gold}
    labels = sorted({g for g in gold.values() if g != OTHER})
    per_label: dict[str, dict[str, float]] = {}
    for lab in labels:
        tp = sum(1 for r, g in gold.items() if g == lab and mapped[r] == lab)
        fp = sum(1 for r, g in gold.items() if g != lab and mapped[r] == lab)
        fn = sum(1 for r, g in gold.items() if g == lab and mapped[r] != lab)
        p = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * rec / (p + rec) if p + rec else 0.0
        per_label[lab] = {"precision": p, "recall": rec, "f1": f1, "support": tp + fn}
    if not labels:
        return 0.0, per_label
    return statistics.fmean(v["f1"] for v in per_label.values()), per_label


def stability_jaccard(run_a: Mapping[str, str], run_b: Mapping[str, str]) -> float:
    """Size-weighted mean Jaccard of matched themes between two runs (Hungarian on overlap).

    `run_*` map rid -> theme id. `other` is ignored. Unmatched themes in A count as 0.
    """
    a_ids = sorted({t for t in run_a.values() if t != OTHER})
    b_ids = sorted({t for t in run_b.values() if t != OTHER})
    if not a_ids:
        return 0.0
    members_a = {t: {r for r, x in run_a.items() if x == t} for t in a_ids}
    members_b = {t: {r for r, x in run_b.items() if x == t} for t in b_ids}
    if not b_ids:
        return 0.0
    jac = np.array(
        [
            [len(members_a[a] & members_b[b]) / len(members_a[a] | members_b[b]) for b in b_ids]
            for a in a_ids
        ]
    )
    rows, cols = linear_sum_assignment(-jac)
    matched = {a_ids[i]: jac[i, j] for i, j in zip(rows, cols, strict=True)}
    total = sum(len(members_a[a]) for a in a_ids)
    return float(sum(len(members_a[a]) * matched.get(a, 0.0) for a in a_ids) / total)


def pct(n: int, d: int) -> float:
    return n / d if d else 0.0


def median_with_spread(scores: Sequence[int | float]) -> tuple[float, float]:
    """Median score and spread (max-min) for repeated judge calls."""
    return float(statistics.median(scores)), float(max(scores) - min(scores))


def recall_by_category(results: Iterable[tuple[str, bool]]) -> dict[str, float]:
    tot: Counter[str] = Counter()
    ok: Counter[str] = Counter()
    for cat, passed in results:
        tot[cat] += 1
        ok[cat] += int(passed)
    return {c: ok[c] / tot[c] for c in tot}
