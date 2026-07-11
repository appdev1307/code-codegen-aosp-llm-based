#!/usr/bin/env python3
"""
analyze_final.py
────────────────
Final N-way comparison and thesis-ready analysis for VHAL code generation.
Supports any subset of C1 Baseline / C2 Adaptive / C3 RAG+DSPy / C4 Feedback
present in experiments/results/ — conditions with a missing JSON file are
silently excluded (see load_results()). Table headers and columns are
built dynamically from whichever conditions are actually loaded, so
adding a future condition (e.g. C5) only requires updating
CONDITION_FILES / CONDITION_ORDER at the top of this file — no other
edits needed.

Reads the JSON results from rescore_all_conditions.py and produces:
  1. Statistical comparison (Kruskal-Wallis + pairwise Mann-Whitney U)
  2. Per-agent breakdown table
  3. Per-dimension breakdown (struct / syntax / coverage)
  4. Markdown report  → experiments/results/final_analysis.md
  5. CSV data table   → experiments/results/final_scores.csv
  6. LaTeX table       → experiments/results/latex_table.tex

Usage:
    python analyze_final.py
"""

from __future__ import annotations

import json
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Optional: scipy for statistical tests
try:
    from scipy import stats as sp_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

RESULTS_DIR = Path("experiments/results")
CONDITION_FILES = {
    "C1 Baseline":  RESULTS_DIR / "baseline.json",
    "C2 Adaptive":  RESULTS_DIR / "adaptive.json",
    "C3 RAG+DSPy":  RESULTS_DIR / "rag_dspy.json",
    "C4 Feedback":  RESULTS_DIR / "c4_feedback.json",
}

AGENT_ORDER = ["aidl", "cpp", "selinux", "build", "design_doc", "android_app", "backend"]
CONDITION_ORDER = ["C1 Baseline", "C2 Adaptive", "C3 RAG+DSPy", "C4 Feedback"]


def get_all_agents_present(per_agent: dict) -> list[str]:
    """AGENT_ORDER, extended with any agent types found in the actual
    data but not in that fixed list.

    Bug this fixes: AGENT_ORDER previously hardcoded only 7 types, but
    validators.py's dispatch table supports up to 12 (also vintf, puml,
    android_layout, backend_model, simulator). Files scored under a
    type missing from AGENT_ORDER were silently dropped from Section 2
    (Per-Agent Breakdown) and the LaTeX export — while still counted
    in Section 1 (Overall Scores) and Section 4 (Statistics), since
    those read from the flat file list, not through AGENT_ORDER. This
    produced a real discrepancy: e.g. observed n=58 in the overview
    but only 50 files summed across the 7 listed agents in Section 2,
    with the 8 invisible files' high scores (~0.99) inflating the
    overall average above what Section 2 alone would suggest.

    Extra types are appended in sorted order after the known ones so
    existing table layouts stay stable and any newly-appearing type is
    still visible rather than silently vanishing again.
    """
    known = set(AGENT_ORDER)
    extra = set()
    for agent_map in per_agent.values():
        extra |= set(agent_map.keys()) - known
    return AGENT_ORDER + sorted(extra)


def load_results() -> dict[str, dict]:
    """Load all condition result JSONs."""
    data = {}
    for label, fpath in CONDITION_FILES.items():
        if fpath.exists():
            data[label] = json.loads(fpath.read_text())
        else:
            print(f"⚠ Missing: {fpath} — {label} will be excluded")
    return data


def extract_scores(data: dict) -> dict[str, list[float]]:
    """Extract flat score lists per condition."""
    return {
        label: [f["score"] for f in d.get("files", [])]
        for label, d in data.items()
    }


def extract_per_agent(data: dict) -> dict[str, dict[str, list[dict]]]:
    """Nested: condition → agent → list of file dicts."""
    out: dict[str, dict[str, list[dict]]] = {}
    for label, d in data.items():
        agent_map: dict[str, list[dict]] = defaultdict(list)
        for f in d.get("files", []):
            agent_map[f["agent"]].append(f)
        out[label] = dict(agent_map)
    return out


# ── Statistical tests ─────────────────────────────────────────────
def run_statistics(score_lists: dict[str, list[float]]) -> str:
    """Kruskal-Wallis + pairwise Mann-Whitney U with effect sizes."""
    lines = []
    labels = [l for l in CONDITION_ORDER if l in score_lists]

    if not HAS_SCIPY:
        lines.append("⚠ scipy not installed — skipping statistical tests.")
        lines.append("  Install: pip install scipy")
        return "\n".join(lines)

    if len(labels) < 2:
        lines.append("⚠ Need at least 2 conditions for statistical tests.")
        return "\n".join(lines)

    groups = [score_lists[l] for l in labels]

    # Kruskal-Wallis (non-parametric ANOVA)
    if len(labels) >= 3:
        h_stat, p_val = sp_stats.kruskal(*groups)
        lines.append(f"Kruskal-Wallis H = {h_stat:.4f}, p = {p_val:.6f}")
        if p_val < 0.05:
            lines.append("  → Significant difference exists among conditions (p < 0.05)")
        else:
            lines.append("  → No significant difference among conditions (p ≥ 0.05)")
        lines.append("")

    # Pairwise Mann-Whitney U
    lines.append("Pairwise Mann-Whitney U tests:")
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = score_lists[labels[i]], score_lists[labels[j]]
            u_stat, p_val = sp_stats.mannwhitneyu(a, b, alternative="two-sided")
            n1, n2 = len(a), len(b)
            # rank-biserial r as effect size
            r_effect = 1 - (2 * u_stat) / (n1 * n2)
            sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
            lines.append(
                f"  {labels[i]} vs {labels[j]}: "
                f"U = {u_stat:.1f}, p = {p_val:.6f} {sig}, "
                f"r = {r_effect:.3f} (rank-biserial)"
            )

    # Descriptive
    lines.append("")
    lines.append("Descriptive statistics:")
    for l in labels:
        s = score_lists[l]
        import statistics
        lines.append(
            f"  {l}: n={len(s)}, mean={statistics.mean(s):.4f}, "
            f"median={statistics.median(s):.4f}, "
            f"sd={statistics.stdev(s):.4f}" if len(s) > 1 else
            f"  {l}: n={len(s)}, mean={statistics.mean(s):.4f}"
        )

    return "\n".join(lines)


# ── Table builders ────────────────────────────────────────────────
def build_overview_table(data: dict) -> str:
    """Markdown table: Condition | avg | min | max | n."""
    rows = []
    for label in CONDITION_ORDER:
        if label not in data:
            continue
        d = data[label]
        rows.append(f"| {label} | {d['avg_score']:.4f} | {d.get('min_score', 'N/A')} | "
                     f"{d.get('max_score', 'N/A')} | {d['num_files']} |")
    header = "| Condition | Avg Score | Min | Max | Files |"
    sep    = "|---|---|---|---|---|"
    return "\n".join([header, sep] + rows)


def build_per_agent_table(per_agent: dict) -> str:
    """Markdown table: Agent | <avg (n)> per condition present in per_agent.

    Header is built FROM the same condition list used to populate rows
    (`present`), rather than a separate hardcoded string — this is the
    fix for the header/data column-count drift bug: previously
    CONDITION_ORDER could grow (e.g. C4 added) while the header string
    stayed hardcoded at 3 columns, producing N+1 data cells under an
    N-column header. Building both from one source makes that class of
    bug structurally impossible.
    """
    present = [l for l in CONDITION_ORDER if l in per_agent]
    all_agents = get_all_agents_present(per_agent)
    header = "| Agent | " + " | ".join(present) + " |"
    sep    = "|---|" + "---|" * len(present)
    rows = []
    for agent in all_agents:
        cells = [f"| {agent} "]
        for label in present:
            if agent in per_agent[label]:
                files = per_agent[label][agent]
                avg = sum(f["score"] for f in files) / len(files)
                cells.append(f"| {avg:.4f} ({len(files)}) ")
            else:
                cells.append("| — ")
        cells.append("|")
        rows.append("".join(cells))
    return "\n".join([header, sep] + rows)


def build_dimension_table(per_agent: dict) -> str:
    """Per-dimension (struct/syntax/coverage) breakdown by condition.

    Same fix as build_per_agent_table: header derived from `present`,
    not a separate hardcoded string.
    """
    present = [l for l in CONDITION_ORDER if l in per_agent]
    header = "| Dimension | " + " | ".join(present) + " |"
    sep    = "|---|" + "---|" * len(present)
    rows = []
    for dim in ["struct", "syntax", "coverage"]:
        cells = [f"| {dim} "]
        for label in present:
            all_files = [f for agent_files in per_agent[label].values() for f in agent_files]
            if all_files:
                avg = sum(f.get(dim, 0) for f in all_files) / len(all_files)
                cells.append(f"| {avg:.4f} ")
            else:
                cells.append("| — ")
        cells.append("|")
        rows.append("".join(cells))
    return "\n".join([header, sep] + rows)


# ── CSV export ────────────────────────────────────────────────────
def export_csv(data: dict, path: Path):
    """Flat CSV of all file scores across conditions."""
    rows = []
    for label in CONDITION_ORDER:
        if label not in data:
            continue
        for f in data[label].get("files", []):
            rows.append({
                "condition": label,
                "spec": f.get("spec", ""),
                "file": f["file"],
                "agent": f["agent"],
                "struct": f["struct"],
                "syntax": f["syntax"],
                "coverage": f["coverage"],
                "score": f["score"],
            })
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  → CSV: {path}")


# ── LaTeX table ───────────────────────────────────────────────────
def export_latex(data: dict, per_agent: dict, path: Path):
    """LaTeX table for thesis insertion."""
    present = [l for l in CONDITION_ORDER if l in data]
    col_spec = "l" + "c" * len(present)
    header_row = "Agent & " + " & ".join(present) + r" \\"
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        rf"\caption{{Average quality scores across {len(present)} LLM pipeline conditions}}",
        r"\label{tab:condition-comparison}",
        rf"\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        header_row,
        r"\midrule",
    ]
    for agent in get_all_agents_present(per_agent):
        cells = [agent.replace("_", r"\_")]
        for label in present:
            if agent in per_agent.get(label, {}):
                files = per_agent[label][agent]
                avg = sum(f["score"] for f in files) / len(files)
                cells.append(f"{avg:.3f}")
            else:
                cells.append("--")
        lines.append(" & ".join(cells) + r" \\")

    # Overall average row
    lines.append(r"\midrule")
    cells = [r"\textbf{Overall}"]
    for label in present:
        cells.append(f"\\textbf{{{data[label]['avg_score']:.3f}}}")
    lines.append(" & ".join(cells) + r" \\")

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    path.write_text("\n".join(lines))
    print(f"  → LaTeX: {path}")


# ── Markdown report ───────────────────────────────────────────────
def generate_report(data: dict, per_agent: dict, score_lists: dict, path: Path):
    """Write the full analysis report as Markdown."""
    present = [l for l in CONDITION_ORDER if l in data]
    n_way = len(present)
    sections = []

    sections.append(f"# VHAL Code Generation — Final {n_way}-Way Comparison\n")
    sections.append(f"*Generated by analyze_final.py*\n")

    # Overview
    sections.append("## 1. Overall Scores\n")
    sections.append(build_overview_table(data))

    # Per-agent
    sections.append("\n## 2. Per-Agent Breakdown\n")
    sections.append("Scores shown as avg (n files).\n")
    sections.append(build_per_agent_table(per_agent))

    # Per-dimension
    sections.append("\n## 3. Per-Dimension Breakdown\n")
    sections.append(build_dimension_table(per_agent))

    # Statistics
    sections.append("\n## 4. Statistical Tests\n")
    sections.append("```")
    sections.append(run_statistics(score_lists))
    sections.append("```")

    # Interpretation guide
    condition_notes = {
        "C1 Baseline":  "- C1 (Baseline) uses hand-crafted prompts with LLM-first generation.\n",
        "C2 Adaptive":  "- C2 (Adaptive) adds Thompson Sampling for prompt selection.\n",
        "C3 RAG+DSPy":  "- C3 (RAG+DSPy) adds AOSP document retrieval + MIPROv2-optimised prompts.\n",
        "C4 Feedback":  "- C4 (Feedback) adds post-validation retry with error feedback on top of C3's "
                        "RAG+DSPy generation, self-correcting failed files within a bounded retry budget.\n",
    }
    sections.append("\n## 5. Interpretation Notes\n")
    notes = f"- All {n_way} conditions were scored using identical validators and weights.\n"
    for label in present:
        notes += condition_notes.get(label, "")
    notes += (
        "- Statistical significance tested with non-parametric tests (Kruskal-Wallis, Mann-Whitney U) "
        "since score distributions may not be normal.\n"
        "- Effect size reported as rank-biserial correlation r.\n"
    )
    sections.append(notes)

    path.write_text("\n".join(sections))
    print(f"  → Report: {path}")


# ── Main ──────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("VHAL Code Generation — Final Analysis")
    print("=" * 60)

    data = load_results()
    if not data:
        print("No result files found. Run rescore_all_conditions.py first.")
        sys.exit(1)

    score_lists = extract_scores(data)
    per_agent = extract_per_agent(data)

    print(f"\nLoaded {len(data)} conditions: {', '.join(data.keys())}\n")

    export_csv(data, RESULTS_DIR / "final_scores.csv")
    export_latex(data, per_agent, RESULTS_DIR / "latex_table.tex")
    generate_report(data, per_agent, score_lists, RESULTS_DIR / "final_analysis.md")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for label in CONDITION_ORDER:
        if label in data:
            print(f"  {label}: {data[label]['avg_score']:.4f} ({data[label]['num_files']} files)")
    print()


if __name__ == "__main__":
    main()