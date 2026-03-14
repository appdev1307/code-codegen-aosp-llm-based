#!/usr/bin/env python3
"""
analyze_final.py
────────────────
Final 3-way comparison and thesis-ready analysis for VHAL code generation.

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
}

AGENT_ORDER = ["aidl", "cpp", "selinux", "build", "design_doc", "android_app", "backend"]
CONDITION_ORDER = ["C1 Baseline", "C2 Adaptive", "C3 RAG+DSPy"]


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
    """Markdown table: Agent | C1 avg | C2 avg | C3 avg."""
    header = "| Agent | C1 Baseline | C2 Adaptive | C3 RAG+DSPy |"
    sep    = "|---|---|---|---|"
    rows = []
    for agent in AGENT_ORDER:
        cells = [f"| {agent} "]
        for label in CONDITION_ORDER:
            if label in per_agent and agent in per_agent[label]:
                files = per_agent[label][agent]
                avg = sum(f["score"] for f in files) / len(files)
                cells.append(f"| {avg:.4f} ({len(files)}) ")
            else:
                cells.append("| — ")
        cells.append("|")
        rows.append("".join(cells))
    return "\n".join([header, sep] + rows)


def build_dimension_table(per_agent: dict) -> str:
    """Per-dimension (struct/syntax/coverage) breakdown by condition."""
    header = "| Dimension | C1 Baseline | C2 Adaptive | C3 RAG+DSPy |"
    sep    = "|---|---|---|---|"
    rows = []
    for dim in ["struct", "syntax", "coverage"]:
        cells = [f"| {dim} "]
        for label in CONDITION_ORDER:
            if label in per_agent:
                all_files = [f for agent_files in per_agent[label].values() for f in agent_files]
                if all_files:
                    avg = sum(f.get(dim, 0) for f in all_files) / len(all_files)
                    cells.append(f"| {avg:.4f} ")
                else:
                    cells.append("| — ")
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
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Average quality scores across three LLM pipeline conditions}",
        r"\label{tab:condition-comparison}",
        r"\begin{tabular}{lccc}",
        r"\toprule",
        r"Agent & C1 Baseline & C2 Adaptive & C3 RAG+DSPy \\",
        r"\midrule",
    ]
    for agent in AGENT_ORDER:
        cells = [agent.replace("_", r"\_")]
        for label in CONDITION_ORDER:
            if label in per_agent and agent in per_agent[label]:
                files = per_agent[label][agent]
                avg = sum(f["score"] for f in files) / len(files)
                cells.append(f"{avg:.3f}")
            else:
                cells.append("--")
        lines.append(" & ".join(cells) + r" \\")

    # Overall average row
    lines.append(r"\midrule")
    cells = [r"\textbf{Overall}"]
    for label in CONDITION_ORDER:
        if label in data:
            cells.append(f"\\textbf{{{data[label]['avg_score']:.3f}}}")
        else:
            cells.append("--")
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
    sections = []

    sections.append("# VHAL Code Generation — Final 3-Way Comparison\n")
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
    sections.append("\n## 5. Interpretation Notes\n")
    sections.append(
        "- All three conditions were scored using identical validators and weights.\n"
        "- C1 (Baseline) uses hand-crafted prompts with LLM-first generation.\n"
        "- C2 (Adaptive) adds Thompson Sampling for prompt selection.\n"
        "- C3 (RAG+DSPy) adds AOSP document retrieval + MIPROv2-optimised prompts.\n"
        "- Statistical significance tested with non-parametric tests (Kruskal-Wallis, Mann-Whitney U) "
        "since score distributions may not be normal.\n"
        "- Effect size reported as rank-biserial correlation r.\n"
    )

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
