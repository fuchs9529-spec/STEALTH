#!/usr/bin/env python3
"""
run_eval.py — CLI entry point for the LLM Eval Suite.

Usage examples:

  # Run against Claude Haiku (requires ANTHROPIC_API_KEY)
  python run_eval.py --model claude-haiku-4-5-20251001

  # Use a different model under test
  python run_eval.py --model claude-sonnet-4-6 --judge claude-haiku-4-5-20251001

  # Fully offline run — NO API key needed (mock model + heuristic judge)
  python run_eval.py --offline

  # Score pre-generated responses from a JSON file
  python run_eval.py --preloaded responses.json

  # Score pre-generated responses fully offline (heuristic judge, no API key)
  python run_eval.py --preloaded responses.json --judge heuristic

  # Run only coding domain, hard cases, limit 5
  python run_eval.py --domains coding --difficulty hard --max-cases 5

  # Load baseline results (no API calls) and regenerate report
  python run_eval.py --from-results results/results.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from eval.runner import EvalRunner, AnthropicAdapter, PreloadedAdapter, MockAdapter
from eval.metrics import compute_metrics, metrics_to_dict
from eval.report import generate_html_report
from eval.scorer import ScoreResult


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM Evaluation Suite")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--model", default="claude-haiku-4-5-20251001",
                       help="Anthropic model to test (requires ANTHROPIC_API_KEY)")
    group.add_argument("--preloaded", metavar="FILE",
                       help="JSON file of {case_id: response} for offline scoring")
    group.add_argument("--from-results", metavar="FILE",
                       help="Re-score / re-report from an existing results.json")
    group.add_argument("--offline", action="store_true",
                       help="Fully offline run: mock model + heuristic judge (no API key)")

    p.add_argument("--judge", default="claude-haiku-4-5-20251001",
                   help="Anthropic model to use as the LLM judge, "
                        "or 'heuristic' for an offline rule-based judge (no API key)")
    p.add_argument("--domains", nargs="+", choices=["support", "summarization", "coding"],
                   help="Limit to specific domains")
    p.add_argument("--difficulty", nargs="+",
                   choices=["easy", "medium", "hard", "edge_case"],
                   help="Limit to specific difficulty levels")
    p.add_argument("--max-cases", type=int, metavar="N",
                   help="Run at most N cases (for quick smoke tests)")
    p.add_argument("--output-dir", default="results",
                   help="Directory to write results to (default: results/)")
    p.add_argument("--no-report", action="store_true",
                   help="Skip HTML report generation")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-case progress output")
    return p.parse_args()


def load_results_from_file(path: str) -> tuple[list[dict], dict]:
    """Load previously saved results.json and recompute metrics."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    # Reconstruct lightweight ScoreResult-like objects for metrics
    results = []
    for r in raw:
        sr = ScoreResult(
            case_id=r["case_id"],
            domain=r["domain"],
            difficulty=r["difficulty"],
            model_response=r.get("model_response", ""),
            judge_score=r.get("judge_score", 0.0),
            rubric_score=r.get("rubric_pass_rate", r.get("rubric_score", 0.0)),
            composite_score=r.get("composite_score", 0.0),
            judge_overall_comment=r.get("judge_overall_comment", ""),
            error=r.get("error", ""),
            skipped=r.get("skipped", False),
        )
        results.append(sr)

    metrics = compute_metrics(results)
    return raw, metrics_to_dict(metrics)


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Mode: re-report from existing results ----
    if args.from_results:
        print(f"Loading results from {args.from_results} ...")
        raw_results, metrics = load_results_from_file(args.from_results)
        if not args.no_report:
            report_path = output_dir / "report.html"
            generate_html_report(raw_results, metrics, report_path)
            print(f"Report: {report_path}")
        print(json.dumps(metrics, indent=2))
        return

    # ---- Resolve judge ----
    judge = "heuristic" if args.offline else args.judge

    # ---- Check API key ----
    needs_model_key = not args.preloaded and not args.offline
    needs_judge_key = judge != "heuristic"
    if (needs_model_key or needs_judge_key) and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("       Set it, or run fully offline with:  python run_eval.py --offline")
        print("       Or score your own responses offline: python run_eval.py --preloaded responses.json --judge heuristic")
        sys.exit(1)

    # ---- Build adapter ----
    if args.offline:
        print("OFFLINE MODE: mock model + heuristic judge (no API calls)")
        adapter = MockAdapter()
    elif args.preloaded:
        print(f"Using preloaded responses from {args.preloaded}")
        adapter = PreloadedAdapter(args.preloaded)
    else:
        print(f"Model under test : {args.model}")
        adapter = AnthropicAdapter(model=args.model)

    print(f"Judge model      : {judge}")

    # ---- Run ----
    runner = EvalRunner(
        model_adapter=adapter,
        judge_model=judge,
        data_dir=Path(__file__).parent / "data",
        domains=args.domains,
        difficulty_filter=args.difficulty,
        max_cases=args.max_cases,
        verbose=not args.quiet,
    )

    results, metrics = runner.run()

    # ---- Save ----
    runner.save_results(results, metrics, output_dir)

    # ---- Report ----
    if not args.no_report:
        raw = [runner._result_to_dict(r) for r in results]
        report_path = output_dir / "report.html"
        generate_html_report(raw, metrics_to_dict(metrics), report_path)
        print(f"\nHTML report: {report_path}")

    print(f"\nComposite: {metrics.avg_composite_score:.3f}  "
          f"Judge: {metrics.avg_judge_score:.3f}  "
          f"Rubric: {metrics.avg_rubric_score:.3f}")


if __name__ == "__main__":
    main()
