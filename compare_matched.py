#!/usr/bin/env python3
"""
compare_matched.py
──────────────────
Fair 3-way comparison using only agents present in ALL three conditions.

Problem: C3 (RAG+DSPy) only produced 5 files (aidl, build, cpp, selinux)
while C1/C2 produced 19-20 files across all 7 agent types. Comparing
raw averages is misleading because C3's score excludes the easier agents
(design_doc, backend) that inflate C1/C2 averages.

Solution: This script filters to only agents present in all 3 conditions,
then compares apples-to-apples.

Usage:
    python compare_matched.py

Outputs:
    experiments/results/matched_comparison.json
    experiments/results/matched_analysis.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# Optional scipy
try:
    from scipy import stats as sp_stats
    import statistics
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    import statistics

RESULTS_DIR = Path("experiments/results")

CONDITION_FILES = {
    "C1 Baseline":    RESULTS_DIR / "baseline.json",
    "C2 Adaptive":    RESULTS_DIR / "adaptive.json",
    "C3 RAG+DSPy":    RESULTS_DIR / "rag_dspy.json",
    "C4 Feedback":    RESULTS_DIR / "c4_feedback.json",
}

CONDITION_ORDER = ["C1 Baseline", "C2 Adaptive", "C3 RAG+DSPy", "C4 Feedback"]


def load_results() -> dict[str, dict]:
    data = {}
    for label, fpath in CONDITION_FILES.items():
        if fpath.exists():
            data[label] = json.loads(fpath.read_text())
        else:
            print(f"⚠ Missing: {fpath}")
    return data


def get_agents_per_condition(data: dict) -> dict[str, set[str]]:
    """Return set of agent types per condition."""
    out = {}
    for label, d in data.items():
        agents = set()
        for f in d.get("files", []):
            agents.add(f["agent"])
        out[label] = agents
    return out


def get_matched_agents(data: dict) -> set[str]:
    """Return agents present in ALL conditions."""
    agent_sets = get_agents_per_condition(data)
    if not agent_sets:
        return set()
    common = set.intersection(*agent_sets.values())
    return common


def filter_files(data: dict, matched_agents: set[str]) -> dict[str, list[dict]]:
    """Filter to only files from matched agents."""
    return {
        label: [f for f in d.get("files", []) if f["agent"] in matched_agents]
        for label, d in data.items()
    }


def run_statistics(score_map: dict[str, list[float]]) -> list[str]:
    lines = []
    labels = [l for l in CONDITION_ORDER if l in score_map]

    if not HAS_SCIPY:
        lines.append("⚠ scipy not installed — skipping statistical tests")
        return lines

    if len(labels) < 2:
        lines.append("⚠ Need at least 2 conditions")
        return lines

    groups = [score_map[l] for l in labels]

    # Kruskal-Wallis
    if len(labels) >= 3 and all(len(g) >= 2 for g in groups):
        h_stat, p_val = sp_stats.kruskal(*groups)
        lines.append(f"Kruskal-Wallis H = {h_stat:.4f}, p = {p_val:.6f}")
        if p_val < 0.05:
            lines.append("  → Significant difference among conditions (p < 0.05)")
        else:
            lines.append("  → No significant difference (p ≥ 0.05)")
        lines.append("")

    # Pairwise Mann-Whitney U
    lines.append("Pairwise Mann-Whitney U tests:")
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = score_map[labels[i]], score_map[labels[j]]
            if len(a) < 2 or len(b) < 2:
                lines.append(f"  {labels[i]} vs {labels[j]}: insufficient samples (n={len(a)}, {len(b)})")
                continue
            u_stat, p_val = sp_stats.mannwhitneyu(a, b, alternative="two-sided")
            n1, n2 = len(a), len(b)
            r_effect = 1 - (2 * u_stat) / (n1 * n2) if (n1 * n2) > 0 else 0
            sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
            lines.append(
                f"  {labels[i]} vs {labels[j]}: "
                f"U = {u_stat:.1f}, p = {p_val:.4f} {sig}, "
                f"r = {r_effect:.3f}"
            )

    # Descriptive
    lines.append("")
    lines.append("Descriptive (matched agents only):")
    for l in labels:
        s = score_map[l]
        if len(s) > 1:
            lines.append(f"  {l}: n={len(s)}, mean={statistics.mean(s):.4f}, "
                         f"median={statistics.median(s):.4f}, sd={statistics.stdev(s):.4f}")
        elif len(s) == 1:
            lines.append(f"  {l}: n=1, mean={s[0]:.4f}")

    return lines


def main():
    print("=" * 60)
    print("VHAL — Matched-Agent Comparison")
    print("=" * 60)

    data = load_results()
    if len(data) < 2:
        print("Need at least 2 condition result files.")
        sys.exit(1)

    # Find matched agents
    agents_per = get_agents_per_condition(data)
    matched = get_matched_agents(data)

    print(f"\nAgents per condition:")
    for label in CONDITION_ORDER:
        if label in agents_per:
            print(f"  {label}: {sorted(agents_per[label])}")

    print(f"\nMatched agents (in ALL conditions): {sorted(matched)}")

    if not matched:
        print("No common agents across all conditions!")
        sys.exit(1)

    excluded = set()
    for agents in agents_per.values():
        excluded |= agents
    excluded -= matched
    if excluded:
        print(f"Excluded agents (not in all conditions): {sorted(excluded)}")

    # Filter
    filtered = filter_files(data, matched)

    # ── Build comparison tables ──
    report = []
    report.append("# VHAL — Matched-Agent Fair Comparison\n")
    report.append(f"Only agents present in ALL three conditions: **{', '.join(sorted(matched))}**\n")
    if excluded:
        report.append(f"Excluded (missing from ≥1 condition): {', '.join(sorted(excluded))}\n")

    # Overall matched scores
    report.append("## 1. Overall Matched Scores\n")
    report.append("| Condition | Avg Score | Min | Max | Files |")
    report.append("|---|---|---|---|---|")

    score_map = {}
    json_summary = {}

    for label in CONDITION_ORDER:
        if label not in filtered:
            continue
        files = filtered[label]
        if not files:
            continue
        scores = [f["score"] for f in files]
        avg = statistics.mean(scores)
        score_map[label] = scores
        json_summary[label] = {
            "avg_score": round(avg, 4),
            "min_score": round(min(scores), 4),
            "max_score": round(max(scores), 4),
            "num_files": len(files),
            "matched_agents": sorted(matched),
        }
        report.append(f"| {label} | {avg:.4f} | {min(scores):.4f} | {max(scores):.4f} | {len(files)} |")

    # Per-agent matched
    report.append("\n## 2. Per-Agent Scores (matched only)\n")
    report.append("| Agent | C1 Baseline | C2 Adaptive | C3 RAG+DSPy |")
    report.append("|---|---|---|---|")

    for agent in sorted(matched):
        row = [f"| {agent} "]
        for label in CONDITION_ORDER:
            if label not in filtered:
                row.append("| — ")
                continue
            agent_files = [f for f in filtered[label] if f["agent"] == agent]
            if agent_files:
                avg = statistics.mean([f["score"] for f in agent_files])
                row.append(f"| {avg:.4f} ({len(agent_files)}) ")
            else:
                row.append("| — ")
        row.append("|")
        report.append("".join(row))

    # Per-dimension
    report.append("\n## 3. Per-Dimension Scores (matched only)\n")
    report.append("| Dimension | C1 Baseline | C2 Adaptive | C3 RAG+DSPy |")
    report.append("|---|---|---|---|")

    for dim in ["struct", "syntax", "coverage"]:
        row = [f"| {dim} "]
        for label in CONDITION_ORDER:
            if label not in filtered:
                row.append("| — ")
                continue
            vals = [f.get(dim, 0) for f in filtered[label]]
            if vals:
                row.append(f"| {statistics.mean(vals):.4f} ")
            else:
                row.append("| — ")
        row.append("|")
        report.append("".join(row))

    # Stats
    report.append("\n## 4. Statistical Tests (matched agents only)\n")
    report.append("```")
    stat_lines = run_statistics(score_map)
    report.extend(stat_lines)
    report.append("```")

    # Comparison with full scores
    report.append("\n## 5. Matched vs Full Scores\n")
    report.append("| Condition | Full Avg (all agents) | Matched Avg | Delta | Full n | Matched n |")
    report.append("|---|---|---|---|---|---|")
    for label in CONDITION_ORDER:
        if label not in data or label not in json_summary:
            continue
        full_avg = data[label].get("avg_score", 0)
        matched_avg = json_summary[label]["avg_score"]
        delta = matched_avg - full_avg
        full_n = data[label].get("num_files", 0)
        matched_n = json_summary[label]["num_files"]
        report.append(f"| {label} | {full_avg:.4f} | {matched_avg:.4f} | {delta:+.4f} | {full_n} | {matched_n} |")

    report.append("\n## 6. Note on C3 Completeness\n")
    report.append(
        "C3 (RAG+DSPy) produced only 5 files due to a ChromaDB singleton error that "
        "caused most agents to fail. The matched comparison above is fair (same agents "
        "compared across all conditions) but has low statistical power due to small n. "
        "After fixing the ChromaDB issue and re-running C3, run `rescore_all_conditions.py` "
        "and `analyze_final.py` for a complete comparison.\n"
    )

    # Write outputs
    report_path = RESULTS_DIR / "matched_analysis.md"
    report_path.write_text("\n".join(report))
    print(f"\n→ Report: {report_path}")

    json_path = RESULTS_DIR / "matched_comparison.json"
    json_path.write_text(json.dumps(json_summary, indent=2))
    print(f"→ JSON: {json_path}")

    # Print summary
    print(f"\n{'='*60}")
    print("MATCHED-AGENT COMPARISON SUMMARY")
    print(f"{'='*60}")
    print(f"Matched agents: {sorted(matched)}")
    for label in CONDITION_ORDER:
        if label in json_summary:
            s = json_summary[label]
            print(f"  {label}: {s['avg_score']:.4f} ({s['num_files']} files)")
    print()


if __name__ == "__main__":
    main()
