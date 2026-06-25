# LLM Evaluation Suite — Methodology

## Overview

This eval suite measures the quality of LLM responses across three high-value use cases: **customer support**, **summarization**, and **coding**. It combines two complementary scoring mechanisms — deterministic rubric checks and LLM-as-judge — to produce a composite score per case and aggregate metrics per domain.

---

## 1. Use Case Selection and Rationale

| Domain | Business Value | Primary Risk |
|---|---|---|
| **Support** | High-volume, customer-facing; tone and accuracy errors damage trust | False promises, dismissive tone, safety mishandling |
| **Summarization** | Reduces information overload; feeds downstream decisions | Hallucination, coverage gaps, distortion of nuance |
| **Coding** | Developer productivity multiplier | Logic errors, insecure code, missing edge cases |

Each domain was chosen because it (a) has a clear quality bar, (b) is common in production LLM deployments, and (c) benefits from different evaluation dimensions.

---

## 2. Test Case Design

### 2.1 Coverage Matrix

The suite contains **55 test cases** distributed as follows:

| Domain | Total | Easy | Medium | Hard | Edge Case |
|---|---|---|---|---|---|
| Support | 20 | 4 | 8 | 6 | 2 |
| Summarization | 17 | 2 | 9 | 5 | 1 |
| Coding | 18 | 4 | 8 | 5 | 1 |
| **Total** | **55** | **10** | **25** | **16** | **4** |

### 2.2 Scenario Types

**Support:** standard complaint, billing dispute, FAQ, escalation request, system outage, refund/return, privacy/data deletion, competitor comparison, subscription cancel, technical troubleshooting, wrong item, and the following edge cases: hostile customer, emotional distress (bereavement), safety incident (child choking hazard), false accusation of fraud, ambiguous request, policy exception.

**Summarization:** news article, meeting transcript → action items, legal clause, technical paper abstract, email chain, one-sentence constraint, bullet format, financial earnings, medical leaflet, social media thread, and edge cases: contradictory source material, very short/degenerate input, impossible constraint (one word), multi-document synthesis.

**Coding:** function generation, debugging (logic error, off-by-one, missing `await`), code explanation, SQL generation, React hook, refactoring (guard clauses), unit test writing, regex, binary search, context manager, Bash script, pandas data cleaning, and edge cases: SQL injection vulnerability review, MD5 password hashing (safe alternative required).

### 2.3 Edge Case Principles

Edge cases target model failure modes that are hard to spot in typical benchmarks:

- **Degenerate inputs** — tests graceful handling of empty or minimal source material.
- **Hostile/emotional users** — tests tone regulation under adversarial conditions.
- **Impossible constraints** — tests honest acknowledgement vs. silent violation.
- **Safety-sensitive requests** — tests whether the model redirects harmful patterns (e.g., MD5 for passwords, SQL injection) rather than complying.
- **Contradictory information** — tests whether the model presents both sides or cherry-picks.

---

## 3. Quality Dimensions

Each domain is evaluated on 4-5 dimensions. Dimensions were chosen to be orthogonal (one dimension failing doesn't automatically cause another to fail) and actionable (a low score clearly indicates what to improve).

### Support (5 dimensions)
| Dimension | What it measures |
|---|---|
| **Accuracy** | Factual correctness; no fabricated policies or information |
| **Empathy** | Tone is warm, appropriately calibrated to customer's emotional state |
| **Completeness** | All aspects of the customer's issue are addressed |
| **Actionability** | Clear next steps are provided for the customer |
| **Safety** | No unsafe promises; escalation triggered when appropriate; sensitive situations handled carefully |

### Summarization (4 dimensions)
| Dimension | What it measures |
|---|---|
| **Faithfulness** | All claims in the summary are grounded in the source text; no hallucinations |
| **Coverage** | The most important information is captured |
| **Conciseness** | The summary is appropriately short; no unnecessary repetition |
| **Coherence** | The summary reads naturally and is logically organised |

### Coding (4 dimensions)
| Dimension | What it measures |
|---|---|
| **Correctness** | The code or explanation is logically correct |
| **Code Quality** | Good practices: naming, structure, no unnecessary complexity |
| **Clarity** | Explanation is clear and accessible to the intended audience |
| **Edge Case Handling** | Edge cases, error conditions, and failure modes are considered |

---

## 4. Scoring Methodology

### 4.1 Dual Scoring Architecture

Each response receives two independent scores that are combined into a composite:

```
Composite = 0.7 × Judge Score + 0.3 × Rubric Pass Rate
```

The 70/30 weighting reflects that LLM judge scores capture nuanced quality (tone, logic, faithfulness) better than deterministic checks, while rubric checks provide a fast, reliable floor for structural requirements.

### 4.2 Rubric Scoring (Deterministic)

Each test case defines a `rubric_checks` dict mapping check names to expected values. The rubric scorer evaluates each check using regex patterns and heuristics. Examples:

| Check | Method |
|---|---|
| `contains_apology` | Regex: `sorry\|apologize\|apologies` |
| `max_length_words` | `len(response.split()) <= N` |
| `max_sentences` | Split on `.!?`, count non-empty segments |
| `uses_group_by` | Regex: `GROUP BY` (case-insensitive) |
| `warns_against_md5` | Regex: `md5.*not\|unsafe\|insecure` |
| `represents_both_views` | Requires both positive AND negative sentiment keywords |

Pass rate = checks passed / checks run. A case with no rubric checks receives a pass rate of 1.0 (neutral).

**Limitations:** Rubric checks are heuristic approximations. A response could pass all checks while being poor quality (e.g., saying "sorry" in an otherwise unhelpful message). They are best used as a quick sanity filter, not a substitute for the judge.

### 4.3 LLM-as-Judge Scoring

The judge model (default: `claude-haiku-4-5-20251001`) receives:
1. The original task (system prompt + user input, or instruction + source text, or code prompt)
2. The model's response
3. The ideal response criteria for the case
4. The list of quality dimensions to score

The judge scores each dimension on a 1-5 scale with a rationale, plus an overall comment. Scores are normalised to [0, 1] via `(score - 1) / 4`.

**Judge system prompt design principles:**
- Instructed to respond in strict JSON only (prevents reasoning leaking into scores)
- Given explicit descriptions of each dimension to reduce ambiguity
- Provided ideal criteria to anchor scoring against the case's specific expectations
- Instructed to score 1-5 to provide more granularity than pass/fail

**Known limitations of LLM-as-judge:**
- **Self-preference bias:** Claude judging Claude responses may be more lenient than a human rater or a different model family judge. Mitigate by using the smallest/cheapest judge (Haiku) rather than the model being tested, or by rotating judge models.
- **Criteria sensitivity:** Judge scores vary with prompt wording. The judge system prompt and dimension descriptions are versioned in `scorer.py` and should be frozen across a benchmark run.
- **No ground truth:** For tasks without a single correct answer (support, most summarization), judge scores represent calibrated opinion, not ground truth. Correlation with human raters is ~0.8 in published research on similar setups.

---

## 5. Composite Score Interpretation

| Score Range | Interpretation |
|---|---|
| 0.85 – 1.00 | Excellent — production-ready for this use case |
| 0.70 – 0.85 | Good — minor issues, suitable with light supervision |
| 0.55 – 0.70 | Acceptable — notable gaps, requires human review in production |
| 0.40 – 0.55 | Poor — significant failures, not suitable for unsupervised deployment |
| 0.00 – 0.40 | Failing — fundamental capability gaps |

---

## 6. Baseline Evaluation

Baseline results are pre-computed in `results/baseline_results.json` using simulated responses and scores representative of a capable mid-tier LLM. These serve as a fixed comparison point when evaluating new models or prompt changes.

To run a live evaluation against a real model:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run_eval.py --model claude-haiku-4-5-20251001
```

To score pre-generated responses without API calls:

```bash
python run_eval.py --preloaded my_responses.json
```

The `my_responses.json` format is:
```json
{
  "sup_001": "Thank you for reaching out about your delayed order...",
  "sum_003": "This clause limits both parties' liability...",
  ...
}
```

---

## 7. Adding New Test Cases

1. Add a new entry to the appropriate `data/*.json` file following the schema.
2. Required fields: `id`, `domain`, `scenario`, `difficulty`, `quality_dimensions`.
3. At least one of `rubric_checks` or `ideal_response_criteria` should be non-empty.
4. Use unique `id` values with the domain prefix (`sup_`, `sum_`, `cod_`).
5. Run `python run_eval.py --max-cases 1 --domains <domain>` to verify the new case loads correctly.

---

## 8. Known Limitations and Future Work

- **No execution-based coding eval:** Code correctness is judged by the LLM, not executed. Adding a sandboxed Python executor (e.g., `subprocess` with timeout) for `cod_*` cases would improve coding accuracy scores significantly.
- **English-only:** All test cases are in English. Multilingual coverage is a planned extension.
- **Single-turn only:** The eval tests single-turn responses. Multi-turn conversation quality (e.g., whether an agent follows up correctly) is not measured.
- **No inter-rater reliability:** No human baseline scores are provided. Adding a small set of human-rated cases (e.g., 10 per domain) would allow IAA computation and judge calibration.
- **Judge bias not quantified:** Running the same cases through multiple judge models (GPT-4o, Claude, Gemini) and measuring agreement would quantify cross-model judge variance.
