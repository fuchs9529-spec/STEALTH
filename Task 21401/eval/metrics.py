"""
metrics.py — Aggregate metrics computed from a list of ScoreResult objects.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from eval.scorer import ScoreResult


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

@dataclass
class DomainMetrics:
    domain: str
    n_cases: int = 0
    n_errors: int = 0
    avg_judge_score: float = 0.0
    avg_rubric_score: float = 0.0
    avg_composite_score: float = 0.0
    dimension_averages: dict[str, float] = field(default_factory=dict)
    by_difficulty: dict[str, float] = field(default_factory=dict)   # difficulty → avg composite
    score_distribution: dict[str, int] = field(default_factory=dict)  # bucket → count


@dataclass
class EvalMetrics:
    total_cases: int = 0
    total_errors: int = 0
    avg_judge_score: float = 0.0
    avg_rubric_score: float = 0.0
    avg_composite_score: float = 0.0
    by_domain: dict[str, DomainMetrics] = field(default_factory=dict)
    worst_cases: list[dict[str, Any]] = field(default_factory=list)
    best_cases: list[dict[str, Any]] = field(default_factory=list)


def compute_metrics(results: list[ScoreResult]) -> EvalMetrics:
    """Compute aggregate metrics from a list of ScoreResult objects."""
    metrics = EvalMetrics()
    metrics.total_cases = len(results)

    valid = [r for r in results if not r.skipped and not r.error]
    error_results = [r for r in results if r.error]
    metrics.total_errors = len(error_results)

    if not valid:
        return metrics

    metrics.avg_judge_score = _mean([r.judge_score for r in valid])
    metrics.avg_rubric_score = _mean([r.rubric_score for r in valid])
    metrics.avg_composite_score = _mean([r.composite_score for r in valid])

    # Group by domain
    by_domain: dict[str, list[ScoreResult]] = defaultdict(list)
    for r in valid:
        by_domain[r.domain].append(r)

    for domain, domain_results in by_domain.items():
        dm = _compute_domain_metrics(domain, domain_results)
        metrics.by_domain[domain] = dm

    # Best / worst cases
    sorted_results = sorted(valid, key=lambda r: r.composite_score)
    metrics.worst_cases = [_result_summary(r) for r in sorted_results[:5]]
    metrics.best_cases = [_result_summary(r) for r in sorted_results[-5:]]

    return metrics


def _compute_domain_metrics(domain: str, results: list[ScoreResult]) -> DomainMetrics:
    dm = DomainMetrics(domain=domain)
    dm.n_cases = len(results)

    dm.avg_judge_score = _mean([r.judge_score for r in results])
    dm.avg_rubric_score = _mean([r.rubric_score for r in results])
    dm.avg_composite_score = _mean([r.composite_score for r in results])

    # Dimension averages
    dim_scores: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for ds in r.dimension_scores:
            dim_scores[ds.dimension].append(ds.score)
    dm.dimension_averages = {d: _mean(scores) for d, scores in dim_scores.items()}

    # By difficulty
    diff_scores: dict[str, list[float]] = defaultdict(list)
    for r in results:
        diff_scores[r.difficulty].append(r.composite_score)
    dm.by_difficulty = {d: _mean(scores) for d, scores in diff_scores.items()}

    # Score distribution buckets: 0-20, 20-40, 40-60, 60-80, 80-100
    buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    for r in results:
        pct = r.composite_score * 100
        if pct < 20:
            buckets["0-20"] += 1
        elif pct < 40:
            buckets["20-40"] += 1
        elif pct < 60:
            buckets["40-60"] += 1
        elif pct < 80:
            buckets["60-80"] += 1
        else:
            buckets["80-100"] += 1
    dm.score_distribution = buckets

    return dm


def _result_summary(r: ScoreResult) -> dict[str, Any]:
    return {
        "case_id": r.case_id,
        "domain": r.domain,
        "difficulty": r.difficulty,
        "composite_score": round(r.composite_score, 3),
        "judge_score": round(r.judge_score, 3),
        "rubric_score": round(r.rubric_score, 3),
        "judge_comment": r.judge_overall_comment[:200] if r.judge_overall_comment else "",
    }


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def metrics_to_dict(m: EvalMetrics) -> dict[str, Any]:
    """Serialise EvalMetrics to a plain dict for JSON output."""
    return {
        "total_cases": m.total_cases,
        "total_errors": m.total_errors,
        "avg_judge_score": round(m.avg_judge_score, 4),
        "avg_rubric_score": round(m.avg_rubric_score, 4),
        "avg_composite_score": round(m.avg_composite_score, 4),
        "by_domain": {
            domain: {
                "n_cases": dm.n_cases,
                "n_errors": dm.n_errors,
                "avg_judge_score": round(dm.avg_judge_score, 4),
                "avg_rubric_score": round(dm.avg_rubric_score, 4),
                "avg_composite_score": round(dm.avg_composite_score, 4),
                "dimension_averages": {k: round(v, 4) for k, v in dm.dimension_averages.items()},
                "by_difficulty": {k: round(v, 4) for k, v in dm.by_difficulty.items()},
                "score_distribution": dm.score_distribution,
            }
            for domain, dm in m.by_domain.items()
        },
        "worst_cases": m.worst_cases,
        "best_cases": m.best_cases,
    }
