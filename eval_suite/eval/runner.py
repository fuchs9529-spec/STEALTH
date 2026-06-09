"""
runner.py — Orchestrates the full evaluation pipeline.

Flow:
  1. Load test cases from data/ directory
  2. For each case, call the model under test (via ModelAdapter)
  3. Score with RubricScorer + LLMJudgeScorer
  4. Aggregate results with metrics.py
  5. Optionally generate an HTML report

ModelAdapter protocol:
  Any callable (case: dict) -> str  counts as a valid model adapter.
  Built-in adapters:
    - AnthropicAdapter   — calls Anthropic API directly
    - PreloadedAdapter   — returns responses from a pre-loaded JSON file (offline)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Any

from eval.scorer import (
    HeuristicJudgeScorer,
    LLMJudgeScorer,
    RubricScorer,
    ScoreResult,
    DOMAIN_DIMENSIONS,
)
from eval.metrics import compute_metrics, metrics_to_dict, EvalMetrics


# ---------------------------------------------------------------------------
# Model adapters
# ---------------------------------------------------------------------------

class AnthropicAdapter:
    """
    Calls the Anthropic Messages API for the model under test.
    Set ANTHROPIC_API_KEY in the environment.
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001", max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore
                self._client = anthropic.Anthropic()
            except ImportError:
                raise RuntimeError("pip install anthropic")
        return self._client

    def __call__(self, case: dict[str, Any]) -> str:
        client = self._get_client()
        messages = self._build_messages(case)
        system = case.get("system_prompt")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        return response.content[0].text

    def _build_messages(self, case: dict[str, Any]) -> list[dict]:
        domain = case["domain"]
        if domain == "support":
            return [{"role": "user", "content": case["user_input"]}]
        elif domain == "summarization":
            return [{"role": "user", "content": f"{case['instruction']}\n\n{case['source_text']}"}]
        else:  # coding
            return [{"role": "user", "content": case["prompt"]}]


class PreloadedAdapter:
    """
    Returns pre-generated responses from a JSON file.
    File format: {"case_id": "response text", ...}
    Useful for offline scoring of already-generated responses.
    """

    def __init__(self, responses_path: str | Path):
        with open(responses_path, encoding="utf-8") as f:
            self._responses: dict[str, str] = json.load(f)

    def __call__(self, case: dict[str, Any]) -> str:
        case_id = case["id"]
        if case_id not in self._responses:
            raise KeyError(f"No preloaded response for case_id={case_id!r}")
        return self._responses[case_id]


class MockAdapter:
    """
    Deterministic offline "model" — generates simulated responses from case
    metadata. No API key, no network. Use to test/demo the full pipeline.
    """

    def __call__(self, case: dict[str, Any]) -> str:
        domain = case["domain"]
        if domain == "support":
            return self._support(case)
        if domain == "summarization":
            return self._summarization(case)
        return self._coding(case)

    @staticmethod
    def _support(case: dict[str, Any]) -> str:
        criteria = case.get("ideal_response_criteria", [])
        body = " ".join(c.rstrip(".") + "." for c in criteria[:4])
        return (
            "I'm so sorry to hear about this, and I completely understand your "
            "frustration. Thank you for bringing it to our attention. "
            f"{body} "
            "First, I will look into your account details right away. "
            "Next, I will follow up with you within 1 business day with an update. "
            "If needed, I can escalate this to a specialist team. "
            "Please let me know if there is anything else I can help with."
        )

    @staticmethod
    def _summarization(case: dict[str, Any]) -> str:
        source = case.get("source_text", "").strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip()]
        summary = " ".join(sentences[:3])
        words = summary.split()
        if len(words) > 60:
            summary = " ".join(words[:60]).rstrip(",;:") + "."
        return summary or "The source text is too short to summarize meaningfully."

    @staticmethod
    def _coding(case: dict[str, Any]) -> str:
        prompt = case.get("prompt", "").strip().replace("\n", " ")
        return (
            "Here is a solution:\n\n"
            "```python\n"
            "def solution(*args, **kwargs):\n"
            '    """Simulated offline response — replace with a real model."""\n'
            "    ...\n"
            "```\n\n"
            f"Explanation: this addresses the request ({prompt[:120]}...). "
            "Note that edge cases such as empty input, zero, and invalid values "
            "should be handled, raising an error where appropriate."
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

JUDGE_WEIGHT = 0.7   # weight for LLM judge score in composite
RUBRIC_WEIGHT = 0.3  # weight for rubric pass rate in composite


class EvalRunner:
    """
    Main evaluation runner.

    Parameters
    ----------
    model_adapter : callable (case dict -> str)
        Produces a response for each test case.
    judge_model : str
        Anthropic model used as the LLM judge.
    data_dir : Path
        Directory containing *_cases.json files.
    domains : list[str] | None
        Filter to specific domains. None = all.
    difficulty_filter : list[str] | None
        Filter to specific difficulties. None = all.
    max_cases : int | None
        Limit total number of cases (useful for quick smoke tests).
    verbose : bool
        Print progress to stdout.
    judge_weight : float
    rubric_weight : float
    """

    def __init__(
        self,
        model_adapter: Callable[[dict], str],
        judge_model: str = "claude-haiku-4-5-20251001",
        data_dir: str | Path | None = None,
        domains: list[str] | None = None,
        difficulty_filter: list[str] | None = None,
        max_cases: int | None = None,
        verbose: bool = True,
        judge_weight: float = JUDGE_WEIGHT,
        rubric_weight: float = RUBRIC_WEIGHT,
    ):
        self.model_adapter = model_adapter
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.domains = domains
        self.difficulty_filter = difficulty_filter
        self.max_cases = max_cases
        self.verbose = verbose
        self.judge_weight = judge_weight
        self.rubric_weight = rubric_weight

        self._rubric_scorer = RubricScorer()
        if judge_model == "heuristic":
            self._judge_scorer = HeuristicJudgeScorer()
        else:
            self._judge_scorer = LLMJudgeScorer(model=judge_model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> tuple[list[ScoreResult], EvalMetrics]:
        """Run the full evaluation. Returns (results, metrics)."""
        cases = self._load_cases()
        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  LLM EVAL SUITE  —  {len(cases)} cases")
            print(f"{'='*60}")

        results: list[ScoreResult] = []
        for i, case in enumerate(cases, 1):
            if self.verbose:
                print(f"[{i:>3}/{len(cases)}] {case['id']:<12} domain={case['domain']:<15} "
                      f"diff={case['difficulty']}", end=" ", flush=True)
            result = self._eval_one(case)
            results.append(result)
            if self.verbose:
                if result.error:
                    print(f"ERROR: {result.error[:60]}")
                elif result.skipped:
                    print("SKIP")
                else:
                    print(f"composite={result.composite_score:.2f}  "
                          f"judge={result.judge_score:.2f}  "
                          f"rubric={result.rubric_score:.2f}")

        metrics = compute_metrics(results)
        if self.verbose:
            self._print_summary(metrics)

        return results, metrics

    def save_results(
        self,
        results: list[ScoreResult],
        metrics: EvalMetrics,
        output_dir: str | Path,
    ) -> Path:
        """Save raw results + metrics JSON to output_dir."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Full results
        results_path = out / "results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump([self._result_to_dict(r) for r in results], f, indent=2)

        # Metrics summary
        metrics_path = out / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics_to_dict(metrics), f, indent=2)

        if self.verbose:
            print(f"\nResults saved to {results_path}")
            print(f"Metrics  saved to {metrics_path}")

        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_cases(self) -> list[dict[str, Any]]:
        cases: list[dict] = []
        domain_files = {
            "support": "support_cases.json",
            "summarization": "summarization_cases.json",
            "coding": "coding_cases.json",
        }
        for domain, filename in domain_files.items():
            if self.domains and domain not in self.domains:
                continue
            path = self.data_dir / filename
            if not path.exists():
                if self.verbose:
                    print(f"  WARNING: {path} not found, skipping.")
                continue
            with open(path, encoding="utf-8") as f:
                domain_cases = json.load(f)
            for c in domain_cases:
                c.setdefault("domain", domain)
            cases.extend(domain_cases)

        if self.difficulty_filter:
            cases = [c for c in cases if c.get("difficulty") in self.difficulty_filter]

        if self.max_cases:
            cases = cases[:self.max_cases]

        return cases

    def _eval_one(self, case: dict[str, Any]) -> ScoreResult:
        result = ScoreResult(
            case_id=case["id"],
            domain=case["domain"],
            difficulty=case.get("difficulty", "unknown"),
            model_response="",
        )

        # Step 1: Get model response
        t0 = time.perf_counter()
        try:
            response = self.model_adapter(case)
            result.model_response = response
            result.latency_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            result.error = f"model_error: {e}"
            result.skipped = True
            return result

        # Step 2: Rubric scoring
        try:
            rubric_result = self._rubric_scorer.score(case, response)
            result.rubric_result = rubric_result
            result.rubric_score = rubric_result.pass_rate
        except Exception as e:
            result.rubric_score = 0.0
            if self.verbose:
                print(f"  rubric_error: {e}", end=" ")

        # Step 3: LLM judge scoring
        try:
            dim_scores, overall_comment = self._judge_scorer.score(case, response)
            result.dimension_scores = dim_scores
            result.judge_overall_comment = overall_comment
            if dim_scores:
                result.judge_score = sum(d.score for d in dim_scores) / len(dim_scores)
        except Exception as e:
            result.error = f"judge_error: {e}"
            result.judge_score = 0.0

        # Step 4: Composite score
        result.composite_score = (
            self.judge_weight * result.judge_score
            + self.rubric_weight * result.rubric_score
        )

        return result

    def _print_summary(self, metrics: EvalMetrics) -> None:
        print(f"\n{'='*60}")
        print(f"  RESULTS SUMMARY")
        print(f"{'='*60}")
        print(f"  Total cases   : {metrics.total_cases}")
        print(f"  Errors        : {metrics.total_errors}")
        print(f"  Avg composite : {metrics.avg_composite_score:.3f}")
        print(f"  Avg judge     : {metrics.avg_judge_score:.3f}")
        print(f"  Avg rubric    : {metrics.avg_rubric_score:.3f}")
        print()
        for domain, dm in metrics.by_domain.items():
            print(f"  [{domain.upper()}]  n={dm.n_cases}  "
                  f"composite={dm.avg_composite_score:.3f}  "
                  f"judge={dm.avg_judge_score:.3f}")
            for dim, avg in dm.dimension_averages.items():
                print(f"     {dim:<25} {avg:.3f}")
        print(f"{'='*60}\n")

    @staticmethod
    def _result_to_dict(r: ScoreResult) -> dict[str, Any]:
        d = asdict(r)
        # Flatten rubric_result for readability
        d["rubric_pass_rate"] = r.rubric_result.pass_rate
        d["rubric_details"] = r.rubric_result.details
        del d["rubric_result"]
        return d
