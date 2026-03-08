"""
experiments/analyze_results.py
────────────────────────────────
Reads experiments/results/comparison.json and produces:
  - experiments/results/analysis_report.md   — human-readable report
  - experiments/figures/bar_avg_metric.png   — avg score by condition
  - experiments/figures/bar_per_agent.png    — per-agent scores across conditions
  - experiments/figures/bar_generation_time.png
  - experiments/figures/heatmap_scores.png   — agents × conditions heatmap

Run:
  python experiments/analyze_results.py
"""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path("experiments/results")
FIGURES_DIR = Path("experiments/figures")
INPUT_FILE  = RESULTS_DIR / "comparison.json"
REPORT_FILE = RESULTS_DIR / "analysis_report.md"

CONDITION_ORDER  = ["baseline", "adaptive", "rag_dspy"]
CONDITION_LABELS = {
    "baseline": "C1 Baseline",
    "adaptive": "C2 Adaptive",
    "rag_dspy": "C3 RAG+DSPy",
}
CONDITION_COLORS = {
    "baseline": "#4C72B0",
    "adaptive": "#55A868",
    "rag_dspy": "#C44E52",
}

ALL_AGENTS = [
    "aidl", "cpp", "selinux", "build", "vintf",
    "design_doc", "puml",
    "android_app", "android_layout",
    "backend", "backend_model", "simulator",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def load_comparison() -> dict:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"{INPUT_FILE} not found.\n"
            "Run: python experiments/run_comparison.py"
        )
    return json.loads(INPUT_FILE.read_text(encoding="utf-8"))


def present_conditions(data: dict) -> list[str]:
    """Return condition keys that actually have results."""
    return [k for k in CONDITION_ORDER if k in data["conditions"]]


# ── figures ──────────────────────────────────────────────────────────────────

def plot_avg_metric(data: dict, keys: list[str]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  [SKIP] matplotlib not installed — skipping figures")
        return

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    conds = data["conditions"]

    # Bar: avg metric score per condition
    fig, ax = plt.subplots(figsize=(7, 4))
    labels = [CONDITION_LABELS[k] for k in keys]
    scores = [conds[k]["avg_metric_score"] for k in keys]
    colors = [CONDITION_COLORS[k] for k in keys]
    bars = ax.bar(labels, scores, color=colors, width=0.5, edgecolor="white")
    ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Avg Composite Metric Score")
    ax.set_title("Average Metric Score by Condition")
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    out = FIGURES_DIR / "bar_avg_metric.png"
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"  [FIG] {out}")

    # Bar: generation time per condition
    fig, ax = plt.subplots(figsize=(7, 4))
    times = [conds[k]["avg_generation_time_s"] for k in keys]
    bars = ax.bar(labels, times, color=colors, width=0.5, edgecolor="white")
    ax.bar_label(bars, fmt="%.1fs", padding=3, fontsize=10)
    ax.set_ylabel("Avg Generation Time (s)")
    ax.set_title("Average Generation Time by Condition")
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    out = FIGURES_DIR / "bar_generation_time.png"
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"  [FIG] {out}")

    # Per-agent grouped bar
    matrix = data.get("per_agent_matrix", {})
    agents_present = [a for a in ALL_AGENTS
                      if any(k in matrix.get(a, {}) for k in keys)]
    if agents_present and len(keys) > 1:
        x = np.arange(len(agents_present))
        width = 0.8 / len(keys)
        fig, ax = plt.subplots(figsize=(14, 5))
        for i, k in enumerate(keys):
            vals = [matrix.get(a, {}).get(k, 0.0) for a in agents_present]
            offset = (i - len(keys) / 2 + 0.5) * width
            ax.bar(x + offset, vals, width,
                   label=CONDITION_LABELS[k],
                   color=CONDITION_COLORS[k],
                   edgecolor="white")
        ax.set_xticks(x)
        ax.set_xticklabels(agents_present, rotation=35, ha="right", fontsize=9)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("Avg Score")
        ax.set_title("Per-Agent Scores Across Conditions")
        ax.legend()
        ax.spines[["top", "right"]].set_visible(False)
        plt.tight_layout()
        out = FIGURES_DIR / "bar_per_agent.png"
        plt.savefig(out, dpi=150)
        plt.close()
        print(f"  [FIG] {out}")

    # Heatmap: agents × conditions
    if agents_present and len(keys) > 1:
        import numpy as np
        heat_data = np.array([
            [matrix.get(a, {}).get(k, float("nan")) for k in keys]
            for a in agents_present
        ])
        fig, ax = plt.subplots(figsize=(max(5, len(keys) * 2), max(5, len(agents_present) * 0.6)))
        im = ax.imshow(heat_data, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels([CONDITION_LABELS[k] for k in keys])
        ax.set_yticks(range(len(agents_present)))
        ax.set_yticklabels(agents_present, fontsize=9)
        for i in range(len(agents_present)):
            for j in range(len(keys)):
                val = heat_data[i, j]
                if not (val != val):  # not nan
                    ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                            fontsize=8, color="black" if 0.3 < val < 0.8 else "white")
        plt.colorbar(im, ax=ax, label="Score")
        ax.set_title("Score Heatmap (Agents × Conditions)")
        plt.tight_layout()
        out = FIGURES_DIR / "heatmap_scores.png"
        plt.savefig(out, dpi=150)
        plt.close()
        print(f"  [FIG] {out}")


# ── markdown report ───────────────────────────────────────────────────────────

def build_report(data: dict, keys: list[str]) -> str:
    conds  = data["conditions"]
    matrix = data.get("per_agent_matrix", {})
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Thesis Experiment Analysis Report",
        f"\nGenerated: {now}",
        f"\nSource: `experiments/results/comparison.json`",
        "\n## Experimental Setup",
        "\n| Condition | Description |",
        "|-----------|-------------|",
        "| Condition 1 — Baseline | LLM-First pipeline, hand-crafted prompts |",
        "| Condition 2 — Adaptive | Thompson Sampling + prompt variant selection |",
        "| Condition 3 — RAG+DSPy | AOSP retrieval + MIPROv2-optimised prompts |",
        "\n## Results Summary",
        "\n| Condition | Modules | Avg Score | Avg Time (s) | Stages OK | Run Time (s) |",
        "|-----------|---------|-----------|--------------|-----------| -------------|",
    ]
    for k in keys:
        c = conds[k]
        lines.append(
            f"| {CONDITION_LABELS[k]:<20} "
            f"| {c['modules_succeeded']}/{c['total_modules']} "
            f"| {c['avg_metric_score']:.4f} "
            f"| {c['avg_generation_time_s']:.1f} "
            f"| {c['stages_succeeded']}/{c['stages_total']} "
            f"| {c['total_run_time_s']:.0f} |"
        )

    # Delta table if C1 and at least one other condition
    if "baseline" in keys and len(keys) > 1:
        lines += [
            "\n## Score Delta vs Baseline (C1)",
            "\n| Condition | Avg Score | Δ vs C1 | Δ% |",
            "|-----------|-----------|---------|-----|",
        ]
        base_score = conds["baseline"]["avg_metric_score"]
        for k in keys:
            score = conds[k]["avg_metric_score"]
            delta = score - base_score
            pct   = (delta / base_score * 100) if base_score > 0 else 0
            arrow = "↑" if delta > 0.001 else ("↓" if delta < -0.001 else "→")
            lines.append(
                f"| {CONDITION_LABELS[k]:<20} | {score:.4f} "
                f"| {arrow} {delta:+.4f} | {pct:+.1f}% |"
            )

    # Per-agent table
    lines += [
        "\n## Per-Agent Scores",
        "",
        "| Agent | " + " | ".join(CONDITION_LABELS[k] for k in keys) +
        (" | Δ(C3−C1)" if "baseline" in keys and "rag_dspy" in keys else "") + " |",
        "|-------|" + "|".join(["-------"] * len(keys)) +
        ("|---------|" if "baseline" in keys and "rag_dspy" in keys else "|"),
    ]
    for agent in ALL_AGENTS:
        scores = matrix.get(agent, {})
        if not any(k in scores for k in keys):
            continue
        row = f"| {agent:<18} |"
        for k in keys:
            s = scores.get(k)
            row += f" {s:.4f} |" if s is not None else "   N/A  |"
        if "baseline" in keys and "rag_dspy" in keys:
            b = scores.get("baseline")
            r = scores.get("rag_dspy")
            if b is not None and r is not None:
                d = r - b
                row += f" {d:+.4f} |"
            else:
                row += "   N/A  |"
        lines.append(row)

    # Per-stage breakdown
    lines += ["\n## Per-Stage Breakdown"]
    for k in keys:
        lines.append(f"\n### {CONDITION_LABELS[k]}")
        lines.append("\n| Stage | Succeeded | Avg Score | Avg Time (s) |")
        lines.append("|-------|-----------|-----------|-------------|")
        per_stage = conds[k].get("per_stage", {})
        for stage, s in per_stage.items():
            lines.append(
                f"| {stage:<18} | {s['succeeded']}/{s['total']} "
                f"| {s['avg_score']:.4f} | {s['avg_time_s']:.1f} |"
            )

    # Figures
    lines += [
        "\n## Figures",
        "",
        "- `figures/bar_avg_metric.png` — Average metric score by condition",
        "- `figures/bar_per_agent.png` — Per-agent scores across conditions",
        "- `figures/bar_generation_time.png` — Generation time by condition",
        "- `figures/heatmap_scores.png` — Score heatmap (agents × conditions)",
    ]

    # Notes on missing conditions
    missing = [k for k in CONDITION_ORDER if k not in keys]
    if missing:
        lines += [
            "\n## Missing Conditions",
            "",
            "The following conditions have not been run yet:",
        ]
        scripts = {"baseline": "multi_main.py",
                   "adaptive": "multi_main_adaptive.py",
                   "rag_dspy": "multi_main_rag_dspy.py"}
        for k in missing:
            lines.append(f"- **{CONDITION_LABELS.get(k, k)}** — run `python {scripts[k]}`")

    return "\n".join(lines) + "\n"


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  analyze_results.py")
    print("=" * 60)

    data = load_comparison()
    keys = present_conditions(data)

    if not keys:
        print("[ERROR] No conditions found in comparison.json")
        print("Run: python experiments/run_comparison.py")
        return

    print(f"\nConditions present: {', '.join(keys)}")
    missing = [k for k in CONDITION_ORDER if k not in keys]
    if missing:
        print(f"Conditions missing: {', '.join(missing)} (partial report)")

    print("\nGenerating figures...")
    plot_avg_metric(data, keys)

    print("\nGenerating report...")
    report = build_report(data, keys)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"  [RPT] {REPORT_FILE}")

    # Print summary to console
    print()
    conds = data["conditions"]
    print(f"  {'Condition':<20} {'AvgScore':>9} {'ΔvsC1':>8} {'Time(s)':>9}")
    print("  " + "-" * 52)
    base = conds.get("baseline", {}).get("avg_metric_score")
    for k in keys:
        c     = conds[k]
        score = c["avg_metric_score"]
        delta = f"{score - base:+.4f}" if base is not None and k != "baseline" else "  base "
        print(f"  {CONDITION_LABELS[k]:<20} {score:>9.4f} {delta:>8} "
              f"{c['avg_generation_time_s']:>9.1f}")

    print(f"\n  Done. Report -> {REPORT_FILE}")


if __name__ == "__main__":
    main()
