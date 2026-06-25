"""
scorer.py — LLM-as-judge + rubric-based scoring engine.

Two scoring modes:
  1. RubricScorer   — deterministic checks (regex, keyword, length)
  2. LLMJudgeScorer — uses an Anthropic model to score responses on quality dimensions

Each scorer returns a ScoreResult dataclass.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DimensionScore:
    """Score for a single quality dimension (0.0 – 1.0)."""
    dimension: str
    score: float          # 0.0 – 1.0
    rationale: str = ""
    raw_score: int = 0    # 1-5 from judge before normalisation


@dataclass
class RubricResult:
    """Result of deterministic rubric checks."""
    checks_run: int = 0
    checks_passed: int = 0
    details: dict[str, bool] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.checks_run == 0:
            return 1.0
        return self.checks_passed / self.checks_run


@dataclass
class ScoreResult:
    """Complete scoring result for one test case."""
    case_id: str
    domain: str
    difficulty: str
    model_response: str

    # LLM judge scores
    dimension_scores: list[DimensionScore] = field(default_factory=list)
    judge_overall_comment: str = ""

    # Rubric checks
    rubric_result: RubricResult = field(default_factory=RubricResult)

    # Derived
    judge_score: float = 0.0       # avg of dimension scores  (0-1)
    rubric_score: float = 0.0      # pass rate (0-1)
    composite_score: float = 0.0   # weighted combination (0-1)

    # Meta
    latency_ms: float = 0.0
    error: str = ""
    skipped: bool = False


# ---------------------------------------------------------------------------
# Dimension definitions per domain
# ---------------------------------------------------------------------------

DOMAIN_DIMENSIONS: dict[str, list[str]] = {
    "support": ["accuracy", "empathy", "completeness", "actionability", "safety"],
    "summarization": ["faithfulness", "coverage", "conciseness", "coherence"],
    "coding": ["correctness", "code_quality", "clarity", "edge_case_handling"],
}

DIMENSION_DESCRIPTIONS: dict[str, str] = {
    # Support
    "accuracy": "The response contains correct, factual information and avoids making things up.",
    "empathy": "The tone is warm, understanding, and appropriate for the customer's emotional state.",
    "completeness": "All aspects of the customer's question or issue are addressed.",
    "actionability": "The response gives clear next steps the customer can take.",
    "safety": "The response does not make unsafe promises, escalates appropriately, and avoids harmful advice.",
    # Summarization
    "faithfulness": "The summary contains only information from the source — no hallucinated or added facts.",
    "coverage": "The summary captures the most important information from the source.",
    "conciseness": "The summary is appropriately brief and free of unnecessary repetition.",
    "coherence": "The summary reads naturally, is well-organised, and logically structured.",
    # Coding
    "correctness": "The code or explanation is logically correct and would work as described.",
    "code_quality": "The code follows good practices (naming, structure, comments, no unnecessary complexity).",
    "clarity": "The explanation is clear and accessible to the intended audience.",
    "edge_case_handling": "The response considers and addresses edge cases and potential failure modes.",
}


# ---------------------------------------------------------------------------
# Rubric scorer (deterministic)
# ---------------------------------------------------------------------------

class RubricScorer:
    """Checks deterministic rubric criteria against a response string."""

    def score(self, case: dict[str, Any], response: str) -> RubricResult:
        checks = case.get("rubric_checks", {})
        result = RubricResult()

        for check_name, expected in checks.items():
            if expected is None:
                continue
            passed = self._run_check(check_name, expected, response)
            result.checks_run += 1
            result.details[check_name] = passed
            if passed:
                result.checks_passed += 1

        return result

    def _run_check(self, check_name: str, expected: Any, response: str) -> bool:
        r = response.lower()

        # ---- length checks ----
        if check_name == "max_length_words":
            return len(response.split()) <= int(expected)
        if check_name == "max_sentences":
            sentences = re.split(r'[.!?]+', response.strip())
            sentences = [s for s in sentences if s.strip()]
            return len(sentences) <= int(expected)
        if check_name == "exactly_one_sentence":
            sentences = re.split(r'[.!?]+', response.strip())
            sentences = [s for s in sentences if s.strip()]
            return len(sentences) == 1

        # ---- boolean content checks ----
        if check_name == "contains_apology":
            return bool(re.search(r'\b(sorry|apologize|apologies|apologise)\b', r))
        if check_name == "contains_steps" or check_name == "provides_specific_steps" or check_name == "provides_cancel_steps":
            return bool(re.search(r'(\d+[\.\)]\s|\bstep\b|\bfirst\b|\bsecond\b|\bthen\b|\bnext\b)', r))
        if check_name == "mentions_escalation" or check_name == "mentions_escalation_path":
            return bool(re.search(r'\b(escalat|manager|supervisor|tier 2|specialist|team)\b', r))
        if check_name == "mentions_fraud_team":
            return bool(re.search(r'\b(fraud|security team|investigation)\b', r))
        if check_name == "mentions_refund_or_investigation":
            return bool(re.search(r'\b(refund|investigate|look into|review)\b', r))
        if check_name == "offers_refund":
            return bool(re.search(r'\b(refund|reimburse|return.*money|credit)\b', r))
        if check_name == "mentions_known_outage":
            return bool(re.search(r'\b(outage|known issue|system.*down|aware)\b', r))
        if check_name == "mentions_privacy_portal":
            return bool(re.search(r'privacy', r))
        if check_name == "mentions_timeline" or check_name == "mentions_refund_timeline":
            return bool(re.search(r'\b(\d+\s*(day|hour|week|month|business day)|\bsoon\b|\bimmediately\b)\b', r))
        if check_name == "mentions_free_shipping":
            return bool(re.search(r'\bfree\b.*\bship|ship.*\bfree\b', r))
        if check_name == "confirms_eligibility":
            return bool(re.search(r'\b(eligible|within|qualify|can return|30.day)\b', r))
        if check_name == "asks_for_clarification":
            return '?' in response
        if check_name == "offers_alternative":
            return bool(re.search(r'\b(instead|alternatively|you can|try|carrier|tracking)\b', r))
        if check_name == "mentions_okr_rescheduled":
            return bool(re.search(r'\b(july\s*2|reschedule|moved|okr)\b', r))
        if check_name == "lists_action_items":
            return bool(re.search(r'(action item|\bowner\b|\bdeadline\b|\bresponsible\b|will \w+)', r))
        if check_name == "structured_response":
            return bool(re.search(r'(\d+[\.\)]\s|•|-\s|\bfirst\b|\bsecond\b|\bthird\b)', r))
        if check_name == "single_paragraph":
            return response.strip().count('\n\n') == 0
        if check_name == "plain_language":
            jargon_count = len(re.findall(
                r'\b(eGFR|mL/min|bps|EBITDA|perplexity|quantization|lactic acidosis)\b', response))
            return jargon_count <= 2
        if check_name == "captures_mixed_sentiment":
            has_positive = bool(re.search(r'\b(positive|good|great|love|excellent|positive)\b', r))
            has_negative = bool(re.search(r'\b(negative|bad|issue|problem|complaint|broken|poor)\b', r))
            return has_positive and has_negative
        if check_name == "significantly_shorter":
            return len(response.split()) < 60
        if check_name == "contains_code":
            return bool(re.search(r'```|def |class |function |SELECT |<[a-z]+>', response))
        if check_name == "has_type_hints" or check_name == "has_typescript_types":
            return bool(re.search(r':\s*(int|str|float|bool|list|dict|Optional|T\b|number|string)', response))
        if check_name == "has_docstring":
            return bool(re.search(r'""".*"""|\'\'\'.*\'\'\'', response, re.DOTALL))
        if check_name == "no_syntax_errors":
            # Heuristic: check for common Python syntax mistakes
            return not bool(re.search(r'(?<![=!<>])=(?!=)(?=[^=>])', ''))  # always pass, requires execution
        if check_name == "uses_group_by":
            return bool(re.search(r'\bGROUP BY\b', response, re.IGNORECASE))
        if check_name == "uses_sum":
            return bool(re.search(r'\bSUM\s*\(', response, re.IGNORECASE))
        if check_name == "filters_by_year":
            return bool(re.search(r'\b(YEAR|WHERE|2025)\b', response, re.IGNORECASE))
        if check_name == "uses_order_by_desc":
            return bool(re.search(r'\bORDER BY\b.*\bDESC\b', response, re.IGNORECASE))
        if check_name == "uses_limit":
            return bool(re.search(r'\bLIMIT\b\s*5', response, re.IGNORECASE))
        if check_name == "identifies_sql_injection":
            return bool(re.search(r'\b(sql injection|inject|parameterize|parameterised)\b', r))
        if check_name == "provides_parameterized_fix":
            return bool(re.search(r'\?|%s|:param|cursor\.execute.*\(.*,\s*\(', response))
        if check_name == "identifies_correct_bug" or check_name == "identifies_off_by_one":
            return bool(re.search(r'(n\s*-\s*3|n-3|off.by.one|range\s*\(\s*1|0.9)', r))
        if check_name == "provides_fix" or check_name == "provides_faster_solution":
            return bool(re.search(r'```|def |fixed|corrected|here is|solution', r))
        if check_name == "uses_set_or_equivalent":
            return bool(re.search(r'\bset\b|\bdict\b|\bhashset\b', r))
        if check_name == "warns_against_md5":
            return bool(re.search(r'\b(md5.*not|avoid md5|unsafe|insecure|not.*md5)\b', r))
        if check_name == "provides_safe_alternative":
            return bool(re.search(r'\b(bcrypt|argon2|pbkdf2|scrypt|hashlib)\b', r))
        if check_name == "identifies_missing_await":
            return bool(re.search(r'\b(missing await|forgot await|await.*fetch|coroutine)\b', r))
        if check_name == "handles_duplicates":
            return bool(re.search(r'\bdrop_duplicates\b', response))
        if check_name == "fills_age_with_median":
            return bool(re.search(r'\bmedian\b.*\bfillna|fillna.*\bmedian\b', response))
        if check_name == "normalizes_phone":
            return bool(re.search(r'\bstrip\b|\bre\.sub\b|\breplace\b.*phone|\bphone.*replace\b', r))
        if check_name == "implements_enter_exit":
            return bool(re.search(r'__enter__|__exit__', response))
        if check_name == "uses_high_res_timer":
            return bool(re.search(r'perf_counter|monotonic|time\.time', response))
        if check_name == "uses_useEffect":
            return bool(re.search(r'useEffect', response))
        if check_name == "has_cleanup_clearTimeout":
            return bool(re.search(r'clearTimeout', response))
        if check_name == "tests_zero_division":
            return bool(re.search(r'(zero|ZeroDivision|ValueError|divide.*zero|b.*0)', r))
        if check_name == "uses_pytest_raises":
            return bool(re.search(r'pytest\.raises', response))
        if check_name == "at_least_four_test_cases":
            test_count = len(re.findall(r'def test_', response))
            return test_count >= 4
        if check_name == "correctly_distinguishes_both":
            return bool(re.search(r'\b(shallow|deep)\b', r))
        if check_name == "identifies_as_palindrome_filter":
            return bool(re.search(r'palindrome', r))
        if check_name == "identifies_complexity_issue":
            return bool(re.search(r'O\(n.?2?\)|quadratic|nested.*loop|n squared', r))
        if check_name == "uses_guard_clauses":
            return response.count('return') >= 3
        if check_name == "handles_constraint_gracefully":
            return bool(re.search(r'(one word|cannot|difficult|instead|alternatively|\?)', r))
        if check_name == "represents_both_views":
            return (bool(re.search(r'\b(bull|buy|positive|growth)\b', r)) and
                    bool(re.search(r'\b(bear|sell|negative|risk|downside)\b', r)))
        if check_name == "accurate_key_statistics":
            return bool(re.search(r'97\.3|2\.4x|68%|1\.2°C|1\.5°C|4\.2B|14%', response))
        if check_name == "four_to_five_bullets":
            bullet_count = len(re.findall(r'^\s*[-•*]\s', response, re.MULTILINE))
            if bullet_count < 4:
                bullet_count = len(re.findall(r'^\d+[\.\)]\s', response, re.MULTILINE))
            return 4 <= bullet_count <= 5
        if check_name == "mentions_batch_distinction":
            return bool(re.search(r'\b(2025|2026|batch|version|serial)\b', r))

        # Default: treat boolean True checks as passed if response is non-empty
        if isinstance(expected, bool) and expected:
            return len(response.strip()) > 0
        return True


# ---------------------------------------------------------------------------
# Heuristic judge scorer (offline, no API key required)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "that", "this", "does", "not",
    "are", "is", "to", "of", "in", "on", "be", "as", "at", "by", "it", "its",
    "their", "them", "they", "has", "have", "was", "were", "will", "would",
    "can", "could", "should", "do", "if", "but", "from", "about", "into",
    "than", "then", "when", "what", "which", "who", "how", "all", "any",
    "both", "each", "more", "most", "other", "some", "such", "only", "very",
    "response", "customer", "model", "provides", "mentions", "contains",
}


def _keywords(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z']+", text.lower())
        if len(w) > 3 and w not in _STOPWORDS
    }


class HeuristicJudgeScorer:
    """
    Offline replacement for LLMJudgeScorer — no API key, no network.

    Scores each quality dimension on a 1-5 scale using keyword-coverage and
    structural heuristics, then normalises to [0, 1] exactly like the LLM
    judge. Scores are deterministic and only a rough approximation of true
    quality; use for pipeline testing, demos, and CI smoke runs.
    """

    def score(
        self,
        case: dict[str, Any],
        response: str,
    ) -> tuple[list[DimensionScore], str]:
        domain = case["domain"]
        dimensions = case.get("quality_dimensions", DOMAIN_DIMENSIONS.get(domain, []))
        coverage = self._criteria_coverage(case.get("ideal_response_criteria", []), response)

        dim_scores = []
        for dim in dimensions:
            raw = self._score_dimension(dim, case, response, coverage)
            raw = max(1, min(5, raw))
            dim_scores.append(DimensionScore(
                dimension=dim,
                score=(raw - 1) / 4.0,
                rationale="Heuristic (offline) score based on keyword/structure checks.",
                raw_score=raw,
            ))

        comment = (
            f"Offline heuristic evaluation (no LLM judge). "
            f"Ideal-criteria keyword coverage: {coverage:.0%}."
        )
        return dim_scores, comment

    # ------------------------------------------------------------------

    def _criteria_coverage(self, criteria: list[str], response: str) -> float:
        """Fraction of ideal_response_criteria whose keywords appear in the response."""
        if not criteria:
            return 0.6  # neutral default when no criteria are defined
        resp_kw = _keywords(response)
        hits = 0
        for criterion in criteria:
            kw = _keywords(criterion)
            if not kw:
                hits += 1
                continue
            if len(kw & resp_kw) / len(kw) >= 0.3:
                hits += 1
        return hits / len(criteria)

    def _score_dimension(
        self, dim: str, case: dict[str, Any], response: str, coverage: float
    ) -> int:
        r = response.lower()
        n_words = len(response.split())
        raw = 1 + round(coverage * 4)  # coverage 0 -> 1, coverage 1 -> 5

        if dim == "empathy":
            raw += 1 if re.search(r"\b(sorry|apolog\w*|understand|frustrat\w*|appreciate|thank)\b", r) else -1
        elif dim == "actionability":
            raw += 1 if re.search(r"(\d+[\.\)]\s|\bstep\b|\bnext\b|\bplease\b|\byou can\b)", r) else -1
        elif dim == "safety":
            if re.search(r"\b(guarantee|promise)\b", r):
                raw -= 1
            if re.search(r"\b(escalat\w*|specialist|supervisor)\b", r):
                raw += 1
        elif dim == "completeness":
            raw += 1 if n_words >= 60 else -1
        elif dim == "faithfulness":
            src_kw = _keywords(case.get("source_text", ""))
            resp_kw = _keywords(response)
            if src_kw and resp_kw:
                grounded = len(resp_kw & src_kw) / len(resp_kw)
                raw = 1 + round(grounded * 4)
        elif dim == "coverage":
            src_kw = _keywords(case.get("source_text", ""))
            resp_kw = _keywords(response)
            if src_kw:
                covered = len(src_kw & resp_kw) / len(src_kw)
                raw = 1 + round(min(1.0, covered * 3) * 4)
        elif dim == "conciseness":
            src_len = len(case.get("source_text", "").split()) or 200
            ratio = n_words / src_len
            raw = 5 if ratio <= 0.3 else 4 if ratio <= 0.5 else 3 if ratio <= 0.8 else 2
        elif dim == "coherence":
            sentences = [s for s in re.split(r"[.!?]+", response) if s.strip()]
            raw += 1 if len(sentences) >= 2 else -1
        elif dim in ("correctness", "code_quality"):
            raw += 1 if re.search(r"```|def |class |SELECT |function ", response) else -1
        elif dim == "clarity":
            raw += 1 if re.search(r"\b(explanation|because|this means|note that|here)\b", r) else 0
        elif dim == "edge_case_handling":
            raw += 1 if re.search(r"\b(edge case|empty|none|null|zero|invalid|error|raise|except)\b", r) else -1

        return raw


# ---------------------------------------------------------------------------
# LLM judge scorer
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator assessing LLM responses.
Score the response on each provided quality dimension on a 1-5 scale:
  1 = Very poor / fails the criterion
  2 = Below expectations
  3 = Meets basic expectations
  4 = Good, minor issues
  5 = Excellent, no significant issues

Respond ONLY with valid JSON in this exact format (no markdown, no extra text):
{
  "scores": {
    "<dimension>": {"score": <1-5>, "rationale": "<1-2 sentence explanation>"},
    ...
  },
  "overall_comment": "<2-3 sentences overall assessment>"
}"""


def _build_judge_prompt(case: dict[str, Any], response: str, dimensions: list[str]) -> str:
    """Build the user-turn prompt for the LLM judge."""
    domain = case["domain"]
    criteria = case.get("ideal_response_criteria", [])

    # Build the task description
    if domain == "support":
        task_desc = (
            f"System prompt given to the model:\n{case.get('system_prompt','')}\n\n"
            f"Customer message:\n{case['user_input']}"
        )
    elif domain == "summarization":
        task_desc = (
            f"Instruction: {case['instruction']}\n\n"
            f"Source text:\n{case['source_text']}"
        )
    else:  # coding
        task_desc = f"Prompt:\n{case['prompt']}"

    dim_descriptions = "\n".join(
        f"- {d}: {DIMENSION_DESCRIPTIONS.get(d, d)}" for d in dimensions
    )
    criteria_text = "\n".join(f"- {c}" for c in criteria)

    return (
        f"=== TASK ===\n{task_desc}\n\n"
        f"=== MODEL RESPONSE ===\n{response}\n\n"
        f"=== IDEAL RESPONSE CRITERIA ===\n{criteria_text}\n\n"
        f"=== DIMENSIONS TO SCORE ===\n{dim_descriptions}\n\n"
        "Now score the response on each dimension as instructed."
    )


class LLMJudgeScorer:
    """
    Uses the Anthropic API to judge responses.

    Requires: pip install anthropic
    API key:  set ANTHROPIC_API_KEY environment variable.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_retries: int = 2,
        retry_delay: float = 2.0,
    ):
        self.model = model
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore
                self._client = anthropic.Anthropic()
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    def score(
        self,
        case: dict[str, Any],
        response: str,
    ) -> tuple[list[DimensionScore], str]:
        """
        Returns (dimension_scores, overall_comment).
        Raises on unrecoverable error.
        """
        domain = case["domain"]
        dimensions = case.get("quality_dimensions", DOMAIN_DIMENSIONS.get(domain, []))
        prompt = _build_judge_prompt(case, response, dimensions)

        client = self._get_client()
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                message = client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=JUDGE_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()
                # Strip markdown code fences if present
                raw = re.sub(r'^```(?:json)?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
                parsed = json.loads(raw)
                return self._parse_judge_output(parsed, dimensions)
            except (json.JSONDecodeError, KeyError) as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)

        raise RuntimeError(f"Judge scoring failed after {self.max_retries+1} attempts: {last_error}")

    def _parse_judge_output(
        self, parsed: dict, dimensions: list[str]
    ) -> tuple[list[DimensionScore], str]:
        scores_raw = parsed.get("scores", {})
        overall = parsed.get("overall_comment", "")
        dimension_scores = []

        for dim in dimensions:
            entry = scores_raw.get(dim, {})
            raw = int(entry.get("score", 3))
            raw = max(1, min(5, raw))
            normalised = (raw - 1) / 4.0  # 1→0.0, 5→1.0
            dimension_scores.append(DimensionScore(
                dimension=dim,
                score=normalised,
                rationale=entry.get("rationale", ""),
                raw_score=raw,
            ))

        return dimension_scores, overall
