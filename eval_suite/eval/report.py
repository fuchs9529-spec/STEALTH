"""
report.py — Generates a self-contained HTML report from eval results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_html_report(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    output_path: str | Path,
    title: str = "LLM Eval Suite — Results Report",
) -> Path:
    """Generate a standalone HTML report and write it to output_path."""
    html = _build_html(results, metrics, title)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _color(v: float) -> str:
    """Map 0-1 score to a colour class."""
    if v >= 0.8:
        return "good"
    if v >= 0.6:
        return "ok"
    if v >= 0.4:
        return "warn"
    return "bad"


def _build_html(results: list[dict], metrics: dict, title: str) -> str:
    domain_colors = {
        "support": "#4A90D9",
        "summarization": "#7B68EE",
        "coding": "#50C878",
    }

    # ---- Domain summary cards ----
    domain_cards = ""
    for domain, dm in metrics.get("by_domain", {}).items():
        color = domain_colors.get(domain, "#888")
        dim_rows = "".join(
            f"<tr><td>{d}</td><td class='{_color(v)}'>{_pct(v)}</td></tr>"
            for d, v in sorted(dm["dimension_averages"].items())
        )
        diff_rows = "".join(
            f"<tr><td>{d}</td><td class='{_color(v)}'>{_pct(v)}</td></tr>"
            for d, v in sorted(dm["by_difficulty"].items())
        )
        dist_bars = ""
        total_cases = dm["n_cases"] or 1
        for bucket, count in dm["score_distribution"].items():
            width = int(count / total_cases * 100)
            dist_bars += (
                f"<div class='dist-row'>"
                f"<span class='dist-label'>{bucket}</span>"
                f"<div class='dist-bar-bg'>"
                f"<div class='dist-bar' style='width:{width}%;background:{color}'></div>"
                f"</div>"
                f"<span class='dist-count'>{count}</span>"
                f"</div>"
            )
        domain_cards += f"""
        <div class="domain-card">
          <div class="domain-header" style="background:{color}">
            <h2>{domain.title()}</h2>
            <span class="badge">{dm['n_cases']} cases</span>
          </div>
          <div class="domain-body">
            <div class="score-trio">
              <div class="score-box">
                <div class="score-val {_color(dm['avg_composite_score'])}">{_pct(dm['avg_composite_score'])}</div>
                <div class="score-lbl">Composite</div>
              </div>
              <div class="score-box">
                <div class="score-val {_color(dm['avg_judge_score'])}">{_pct(dm['avg_judge_score'])}</div>
                <div class="score-lbl">Judge</div>
              </div>
              <div class="score-box">
                <div class="score-val {_color(dm['avg_rubric_score'])}">{_pct(dm['avg_rubric_score'])}</div>
                <div class="score-lbl">Rubric</div>
              </div>
            </div>
            <div class="sub-tables">
              <div>
                <h4>By Dimension</h4>
                <table class="dim-table"><tbody>{dim_rows}</tbody></table>
              </div>
              <div>
                <h4>By Difficulty</h4>
                <table class="dim-table"><tbody>{diff_rows}</tbody></table>
              </div>
            </div>
            <div>
              <h4>Score Distribution</h4>
              {dist_bars}
            </div>
          </div>
        </div>
        """

    # ---- Case rows ----
    case_rows = ""
    for r in sorted(results, key=lambda x: x.get("composite_score", 0)):
        case_id = r.get("case_id", "")
        domain = r.get("domain", "")
        diff = r.get("difficulty", "")
        comp = r.get("composite_score", 0)
        judge = r.get("judge_score", 0)
        rubric = r.get("rubric_pass_rate", r.get("rubric_score", 0))
        err = r.get("error", "")
        comment = r.get("judge_overall_comment", "")[:150]
        response_preview = r.get("model_response", "")[:200].replace("<", "&lt;").replace(">", "&gt;")

        dim_cells = ""
        for ds in r.get("dimension_scores", []):
            ds_score = ds["score"]
            ds_dim = ds["dimension"][:4]
            ds_color = _color(ds_score)
            ds_pct = _pct(ds_score)
            dim_cells += f"<span class='dim-chip {ds_color}'>{ds_dim}: {ds_pct}</span>"

        status = "&#10004;" if not err else "&#10060;"
        row_class = "error-row" if err else ""
        domain_bg = domain_colors.get(domain, '#888')
        comp_color = _color(comp)
        judge_color = _color(judge)
        rubric_color = _color(rubric)
        judge_div = (f"<div class='judge-comment'><b>Judge:</b> {comment}...</div>") if comment else ""
        response_div = (f"<div class='response-preview'><b>Preview:</b> {response_preview}...</div>") if response_preview else ""
        error_div = (f"<div class='error-msg'>Error: {err}</div>") if err else ""
        case_rows += (
            f"<tr class='{row_class}' onclick='toggleDetail(this)'>"
            f"<td>{status} {case_id}</td>"
            f"<td><span class='tag' style='background:{domain_bg}'>{domain}</span></td>"
            f"<td>{diff}</td>"
            f"<td class='{comp_color}'>{_pct(comp)}</td>"
            f"<td class='{judge_color}'>{_pct(judge)}</td>"
            f"<td class='{rubric_color}'>{_pct(rubric)}</td>"
            f"</tr>"
            f"<tr class='detail-row' style='display:none'>"
            f"<td colspan='6'>"
            f"<div class='detail-box'>"
            f"<div class='detail-dims'>{dim_cells}</div>"
            f"{judge_div}{response_div}{error_div}"
            f"</div></td></tr>"
        )

    # ---- Best / worst ----
    def case_list_html(cases: list[dict]) -> str:
        items = ""
        for c in cases:
            items += (
                f"<li><code>{c['case_id']}</code> "
                f"<span class='{_color(c['composite_score'])}'>{_pct(c['composite_score'])}</span> "
                f"— {c.get('judge_comment','')[:100]}</li>"
            )
        return f"<ul>{items}</ul>"

    best_html = case_list_html(metrics.get("best_cases", []))
    worst_html = case_list_html(metrics.get("worst_cases", []))

    overall_composite = metrics.get("avg_composite_score", 0)
    overall_judge = metrics.get("avg_judge_score", 0)
    overall_rubric = metrics.get("avg_rubric_score", 0)
    total = metrics.get("total_cases", 0)
    errors = metrics.get("total_errors", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --surface2: #222536;
    --border: #2e3148; --text: #e2e8f0; --muted: #8892a4;
    --good: #4ade80; --ok: #facc15; --warn: #fb923c; --bad: #f87171;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.1rem; }}
  h3 {{ font-size: 1rem; margin-bottom: 12px; color: var(--muted); }}
  h4 {{ font-size: 0.85rem; color: var(--muted); margin-bottom: 8px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; font-size: 0.9rem; }}
  .overall {{ display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }}
  .overall-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 24px; min-width: 140px; text-align: center; }}
  .overall-val {{ font-size: 2rem; font-weight: 700; }}
  .overall-lbl {{ font-size: 0.8rem; color: var(--muted); margin-top: 4px; }}
  .good {{ color: var(--good); }} .ok {{ color: var(--ok); }}
  .warn {{ color: var(--warn); }} .bad {{ color: var(--bad); }}
  .domain-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 20px; margin-bottom: 32px; }}
  .domain-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
  .domain-header {{ padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; }}
  .domain-header h2 {{ color: #fff; }}
  .badge {{ background: rgba(255,255,255,0.2); color:#fff; padding: 2px 10px; border-radius: 20px; font-size: 0.8rem; }}
  .domain-body {{ padding: 16px 20px; }}
  .score-trio {{ display: flex; gap: 12px; margin-bottom: 16px; }}
  .score-box {{ flex: 1; background: var(--surface2); border-radius: 8px; padding: 12px; text-align: center; }}
  .score-val {{ font-size: 1.4rem; font-weight: 700; }}
  .score-lbl {{ font-size: 0.75rem; color: var(--muted); margin-top: 2px; }}
  .sub-tables {{ display: flex; gap: 16px; margin-bottom: 16px; }}
  .sub-tables > div {{ flex: 1; }}
  .dim-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  .dim-table td {{ padding: 3px 6px; border-bottom: 1px solid var(--border); }}
  .dim-table td:last-child {{ text-align: right; font-weight: 600; }}
  .dist-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 0.8rem; }}
  .dist-label {{ width: 56px; color: var(--muted); flex-shrink: 0; }}
  .dist-bar-bg {{ flex: 1; height: 12px; background: var(--surface2); border-radius: 6px; overflow: hidden; }}
  .dist-bar {{ height: 100%; border-radius: 6px; transition: width 0.3s; }}
  .dist-count {{ width: 20px; text-align: right; color: var(--muted); }}
  .section-header {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; margin-top: 8px; }}
  .bw-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 32px; }}
  .bw-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; }}
  .bw-card h3 {{ margin-bottom: 10px; font-size: 0.9rem; }}
  .bw-card ul {{ list-style: none; }}
  .bw-card li {{ font-size: 0.82rem; padding: 4px 0; border-bottom: 1px solid var(--border); }}
  .bw-card li:last-child {{ border-bottom: none; }}
  .bw-card code {{ background: var(--surface2); padding: 1px 5px; border-radius: 4px; font-size: 0.78rem; }}
  table.results {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; background: var(--surface);
    border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 32px; }}
  table.results th {{ background: var(--surface2); padding: 10px 12px; text-align: left; color: var(--muted);
    font-weight: 600; border-bottom: 1px solid var(--border); }}
  table.results td {{ padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  table.results tr:last-child td {{ border-bottom: none; }}
  table.results tr:hover td {{ background: var(--surface2); cursor: pointer; }}
  .tag {{ color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 0.76rem; }}
  .detail-row {{ display: none; }}
  .detail-row.visible {{ display: table-row; }}
  .detail-box {{ padding: 10px 12px; background: var(--surface2); border-radius: 6px; }}
  .detail-dims {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
  .dim-chip {{ font-size: 0.75rem; padding: 2px 8px; border-radius: 12px; background: var(--bg); }}
  .judge-comment {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 6px; }}
  .response-preview {{ font-size: 0.78rem; color: var(--muted); font-family: monospace; background: var(--bg);
    padding: 8px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }}
  .error-msg {{ color: var(--bad); font-size: 0.82rem; }}
  .error-row td {{ opacity: 0.6; }}
  @media (max-width: 700px) {{
    .bw-grid, .sub-tables {{ grid-template-columns: 1fr; flex-direction: column; }}
    .score-trio {{ flex-direction: column; }}
  }}
</style>
</head>
<body>

<h1>{title}</h1>
<p class="subtitle">Domains: Support · Summarization · Coding &nbsp;|&nbsp; {total} cases &nbsp;|&nbsp; {errors} errors</p>

<div class="overall">
  <div class="overall-card">
    <div class="overall-val {_color(overall_composite)}">{_pct(overall_composite)}</div>
    <div class="overall-lbl">Composite Score</div>
  </div>
  <div class="overall-card">
    <div class="overall-val {_color(overall_judge)}">{_pct(overall_judge)}</div>
    <div class="overall-lbl">LLM Judge Avg</div>
  </div>
  <div class="overall-card">
    <div class="overall-val {_color(overall_rubric)}">{_pct(overall_rubric)}</div>
    <div class="overall-lbl">Rubric Pass Rate</div>
  </div>
  <div class="overall-card">
    <div class="overall-val" style="color:var(--text)">{total}</div>
    <div class="overall-lbl">Total Cases</div>
  </div>
  <div class="overall-card">
    <div class="overall-val {('bad' if errors > 0 else 'good')}">{errors}</div>
    <div class="overall-lbl">Errors</div>
  </div>
</div>

<div class="section-header">Domain Breakdown</div>
<div class="domain-grid">
  {domain_cards}
</div>

<div class="bw-grid">
  <div class="bw-card">
    <h3>⬇️ Lowest-Scoring Cases</h3>
    {worst_html}
  </div>
  <div class="bw-card">
    <h3>⬆️ Highest-Scoring Cases</h3>
    {best_html}
  </div>
</div>

<div class="section-header">All Cases (click row to expand)</div>
<table class="results">
  <thead>
    <tr>
      <th>Case ID</th>
      <th>Domain</th>
      <th>Difficulty</th>
      <th>Composite</th>
      <th>Judge</th>
      <th>Rubric</th>
    </tr>
  </thead>
  <tbody>
    {case_rows}
  </tbody>
</table>

<script>
function toggleDetail(row) {{
  const next = row.nextElementSibling;
  if (next && next.classList.contains('detail-row')) {{
    next.style.display = next.style.display === 'table-row' ? 'none' : 'table-row';
  }}
}}
</script>

</body>
</html>"""
