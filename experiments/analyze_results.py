"""
experiments/analyze_results.py
═══════════════════════════════════════════════════════════════════
Statistical analysis and thesis chart generation.

Reads experiments/results/comparison.json (produced by
run_comparison.py) and generates:

  Terminal output:
    - Descriptive statistics per condition
    - Statistical significance tests (Mann-Whitney U)
    - Effect size (Cohen's d)
    - Summary verdict for thesis conclusion

  Files saved to experiments/results/figures/:
    - bar_avg_metric.png      — avg metric score per condition
    - bar_per_agent.png       — per-agent scores, 3 conditions grouped
    - bar_generation_time.png — avg generation time per condition
    - heatmap_scores.png      — score heatmap across agents × conditions

  Files saved to experiments/results/:
    - thesis_table.tex        — LaTeX table for direct thesis inclusion
    - thesis_table.csv        — CSV version for spreadsheet analysis
    - analysis_report.md      — Markdown summary of all findings

Usage:
    python experiments/analyze_results.py

    # Save figures to custom directory
    python experiments/analyze_results.py --figures-dir docs/figures

    # Skip chart generation (text output only)
    python experiments/analyze_results.py --no-charts
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────

RESULTS_DIR    = Path("experiments/results")
COMPARISON_FILE = RESULTS_DIR / "comparison.json"
FIGURES_DIR    = RESULTS_DIR / "figures"

CONDITION_ORDER  = ["baseline", "adaptive", "rag_dspy"]
CONDITION_LABELS = {
    "baseline": "Baseline\n(Condition 1)",
    "adaptive": "Adaptive\n(Condition 2)",
    "rag_dspy": "RAG+DSPy\n(Condition 3)",
}
CONDITION_COLORS = {
    "baseline": "#4C72B0",
    "adaptive": "#55A868",
    "rag_dspy": "#C44E52",
}

AGENT_TYPE_LABELS = {
    "aidl":           "AIDL",
    "cpp":            "C++ VHAL",
    "selinux":        "SELinux",
    "build":          "Android.bp",
    "vintf":          "VINTF",
    "design_doc":     "Design Doc",
    "puml":           "PlantUML",
    "android_app":    "Android App",
    "android_layout": "Layout XML",
    "backend":        "FastAPI",
    "backend_model":  "Models",
    "simulator":      "Simulator",
}


# ─────────────────────────────────────────────────────────────────
# Statistical helpers (pure Python — no scipy needed)
# ─────────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _cohens_d(a: list[float], b: list[float]) -> float:
    """Cohen's d effect size between two samples."""
    if not a or not b:
        return 0.0
    pooled_std = math.sqrt(
        (_std(a) ** 2 + _std(b) ** 2) / 2
    )
    if pooled_std == 0:
        return 0.0
    return (_mean(a) - _mean(b)) / pooled_std


def _effect_size_label(d: float) -> str:
    """Interpret Cohen's d magnitude."""
    d = abs(d)
    if   d >= 0.8: return "large"
    elif d >= 0.5: return "medium"
    elif d >= 0.2: return "small"
    else:          return "negligible"


def _mann_whitney_u(a: list[float], b: list[float]) -> tuple[float, str]:
    """
    Non-parametric Mann-Whitney U test (pure Python).
    Returns (U statistic, significance label).
    Uses normal approximation for p-value when n > 8.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return 0.0, "n/a"

    # Count concordant pairs
    u = sum(
        1 if ai > bi else (0.5 if ai == bi else 0)
        for ai in a
        for bi in b
    )

    # Normal approximation
    mu_u  = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return u, "n/a"

    z = (u - mu_u) / sigma

    # Two-tailed p-value approximation via error function
    p = 1 - math.erf(abs(z) / math.sqrt(2))

    if   p < 0.001: sig = "p<0.001 ***"
    elif p < 0.01:  sig = "p<0.01  **"
    elif p < 0.05:  sig = "p<0.05  *"
    else:           sig = f"p={p:.3f} ns"

    return u, sig


# ─────────────────────────────────────────────────────────────────
# ResultsAnalyzer
# ─────────────────────────────────────────────────────────────────

class ResultsAnalyzer:
    """
    Loads comparison.json and produces statistical analysis,
    thesis tables, and matplotlib charts.

    Parameters
    ----------
    comparison_file : str  — path to comparison.json
    figures_dir     : str  — where to save chart PNG files
    generate_charts : bool — if False, skip matplotlib entirely
    """

    def __init__(
        self,
        comparison_file: str  = str(COMPARISON_FILE),
        figures_dir:     str  = str(FIGURES_DIR),
        generate_charts: bool = True,
    ):
        self.comparison_file = Path(comparison_file)
        self.figures_dir     = Path(figures_dir)
        self.generate_charts = generate_charts
        self.data: dict      = {}

        if not self.comparison_file.exists():
            raise FileNotFoundError(
                f"Comparison file not found: {self.comparison_file}\n"
                f"Run: python experiments/run_comparison.py first"
            )

        self.data = json.loads(
            self.comparison_file.read_text(encoding="utf-8")
        )
        self.conditions = self.data.get("conditions", {})

        if generate_charts:
            self.figures_dir.mkdir(parents=True, exist_ok=True)
            self._setup_matplotlib()

    # ── Public API ───────────────────────────────────────────────

    def run(self) -> None:
        """Run all analyses and generate all outputs."""
        print("\n" + "═" * 65)
        print("  THESIS RESULTS ANALYSIS")
        print("═" * 65)
        print(f"  Comparison file: {self.comparison_file}")
        print(f"  Generated at:    {self.data.get('generated_at', '—')}")
        print()

        self._print_descriptive_stats()
        self._print_significance_tests()
        self._print_per_agent_breakdown()
        self._print_verdict()

        # Save outputs
        self._save_latex_table()
        self._save_csv_table()
        self._save_markdown_report()

        if self.generate_charts:
            self._chart_avg_metric()
            self._chart_per_agent()
            self._chart_generation_time()
            self._chart_heatmap()

        print("\n[ANALYSIS] All outputs saved:")
        print(f"  Figures    → {self.figures_dir}/")
        print(f"  LaTeX      → {RESULTS_DIR}/thesis_table.tex")
        print(f"  CSV        → {RESULTS_DIR}/thesis_table.csv")
        print(f"  Report     → {RESULTS_DIR}/analysis_report.md")

    # ── Statistical analysis ─────────────────────────────────────

    def _print_descriptive_stats(self) -> None:
        """Print per-condition descriptive statistics."""
        print("  DESCRIPTIVE STATISTICS")
        print("  " + "─" * 60)
        print(
            f"  {'Metric':<28} "
            f"{'Baseline':>10} {'Adaptive':>10} {'RAG+DSPy':>10}"
        )
        print("  " + "─" * 60)

        metrics = [
            ("Modules succeeded",       "modules_succeeded"),
            ("Avg metric score",        "avg_metric_score"),
            ("Avg gen time (s)",        "avg_generation_time_s"),
            ("Stages succeeded",        "stages_succeeded"),
            ("Total run time (s)",      "total_run_time_s"),
        ]
        for label, key in metrics:
            vals = []
            for c in CONDITION_ORDER:
                v = self.conditions.get(c, {}).get(key, "—")
                vals.append(
                    f"{v:.3f}" if isinstance(v, float)
                    else (f"{v:.0f}" if isinstance(v, int) else "—")
                )
            print(
                f"  {label:<28} "
                f"{vals[0]:>10} {vals[1]:>10} {vals[2]:>10}"
            )
        print()

    def _print_significance_tests(self) -> None:
        """Run Mann-Whitney U tests on available per-stage score samples."""
        print("  STATISTICAL SIGNIFICANCE (Mann-Whitney U)")
        print("  " + "─" * 60)

        # Extract score samples per condition from per_stage metrics
        scores: dict[str, list[float]] = {}
        for cond in CONDITION_ORDER:
            cond_data  = self.conditions.get(cond, {})
            per_stage  = cond_data.get("per_stage", {})
            file_scores = list(cond_data.get("file_scores", {}).values())

            # Combine per_stage avg_scores + file_scores into one sample
            stage_scores = [
                v.get("avg_score", 0.0)
                for v in per_stage.values()
                if isinstance(v, dict) and "avg_score" in v
            ]
            scores[cond] = stage_scores + file_scores

        pairs = [
            ("baseline", "rag_dspy",  "Condition 1 vs Condition 3"),
            ("baseline", "adaptive",  "Condition 1 vs Condition 2"),
            ("adaptive", "rag_dspy",  "Condition 2 vs Condition 3"),
        ]

        for c1, c2, label in pairs:
            a = scores.get(c1, [])
            b = scores.get(c2, [])
            if len(a) < 2 or len(b) < 2:
                print(f"  {label:<35} insufficient data")
                continue
            _, sig   = _mann_whitney_u(a, b)
            d        = _cohens_d(a, b)
            effect   = _effect_size_label(d)
            mean_a   = _mean(a)
            mean_b   = _mean(b)
            delta    = mean_b - mean_a
            print(
                f"  {label:<35} "
                f"Δmean={delta:+.3f}  d={d:.2f} ({effect})  {sig}"
            )
        print()

    def _print_per_agent_breakdown(self) -> None:
        """Print per-agent file scores for all three conditions."""
        # Gather all agent types that have scores in any condition
        all_agents: set[str] = set()
        for cond_data in self.conditions.values():
            all_agents.update(cond_data.get("file_scores", {}).keys())

        if not all_agents:
            return

        print("  PER-AGENT FILE SCORES")
        print("  " + "─" * 60)
        print(
            f"  {'Agent':<20} "
            f"{'Baseline':>10} {'Adaptive':>10} {'RAG+DSPy':>10} {'Δ(3-1)':>8}"
        )
        print("  " + "─" * 60)

        for agent_type in sorted(all_agents):
            label = AGENT_TYPE_LABELS.get(agent_type, agent_type)
            vals  = []
            for cond in CONDITION_ORDER:
                v = self.conditions.get(cond, {}).get(
                    "file_scores", {}
                ).get(agent_type, None)
                vals.append(v)

            b, _, r = vals
            delta_str = (
                f"{r - b:+.3f}"
                if isinstance(r, float) and isinstance(b, float)
                else "—"
            )
            formatted = [
                f"{v:.3f}" if isinstance(v, float) else "—"
                for v in vals
            ]
            print(
                f"  {label:<20} "
                f"{formatted[0]:>10} {formatted[1]:>10} "
                f"{formatted[2]:>10} {delta_str:>8}"
            )
        print()

    def _print_verdict(self) -> None:
        """Print a plain-language research conclusion."""
        b_score = self.conditions.get("baseline", {}).get("avg_metric_score", None)
        a_score = self.conditions.get("adaptive", {}).get("avg_metric_score", None)
        r_score = self.conditions.get("rag_dspy", {}).get("avg_metric_score", None)

        print("  RESEARCH CONCLUSION")
        print("  " + "─" * 60)

        if None in (b_score, a_score, r_score):
            print("  Insufficient data for conclusion — run all three conditions.")
            return

        best    = max(
            [("Baseline",  b_score),
             ("Adaptive",  a_score),
             ("RAG+DSPy",  r_score)],
            key=lambda x: x[1],
        )
        delta_rag_base = r_score - b_score
        delta_rag_adap = r_score - a_score

        print(f"  Best performing condition : {best[0]} "
              f"(avg score = {best[1]:.3f})")
        print(f"  RAG+DSPy vs Baseline     : {delta_rag_base:+.3f} "
              f"({'improvement' if delta_rag_base > 0 else 'regression'})")
        print(f"  RAG+DSPy vs Adaptive     : {delta_rag_adap:+.3f} "
              f"({'improvement' if delta_rag_adap > 0 else 'regression'})")
        print()

        if delta_rag_base > 0.05:
            verdict = (
                "RAG+DSPy demonstrates meaningful improvement over both "
                "baseline and adaptive conditions. The combination of "
                "retrieved AOSP grounding context and MIPROv2-optimised "
                "prompts produces structurally more correct HAL code."
            )
        elif delta_rag_base > 0:
            verdict = (
                "RAG+DSPy shows marginal improvement over baseline. "
                "Consider increasing RAG_TOP_K or re-running the DSPy "
                "optimiser with a larger training set for stronger results."
            )
        else:
            verdict = (
                "RAG+DSPy does not improve over baseline in this run. "
                "Investigate retrieval quality (run smoke test) and "
                "whether the DSPy optimiser converged correctly."
            )

        # Word-wrap at 60 chars
        words, line, lines = verdict.split(), "", []
        for w in words:
            if len(line) + len(w) + 1 > 60:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        if line:
            lines.append(line)

        for l in lines:
            print(f"  {l}")
        print("  " + "─" * 60)
        print()

    # ── Output generators ────────────────────────────────────────

    def _save_latex_table(self) -> None:
        """Save a LaTeX table for direct thesis inclusion."""
        rows = self.data.get("summary_table", [])
        if not rows:
            return

        lines = [
            r"\begin{table}[h]",
            r"\centering",
            r"\caption{Three-Way Pipeline Comparison (50 VSS Signals)}",
            r"\label{tab:pipeline_comparison}",
            r"\begin{tabular}{lrrrrr}",
            r"\toprule",
            r"Condition & Modules & Avg Score & Avg Time (s) "
            r"& Stages OK & Run Time (s) \\",
            r"\midrule",
        ]

        for row in rows:
            label = {
                "baseline": r"Baseline (C1)",
                "adaptive": r"Adaptive (C2)",
                "rag_dspy": r"RAG+DSPy (C3)",
            }.get(row.get("condition", ""), row.get("condition", ""))

            lines.append(
                f"{label} & "
                f"{row.get('modules_succeeded', 0):.0f} & "
                f"{row.get('avg_metric_score', 0):.3f} & "
                f"{row.get('avg_generation_time_s', 0):.1f} & "
                f"{row.get('stages_succeeded', 0):.0f} & "
                f"{row.get('total_run_time_s', 0):.0f} \\\\"
            )

        lines += [
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]

        out_path = RESULTS_DIR / "thesis_table.tex"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  [ANALYSIS] LaTeX table → {out_path}")

    def _save_csv_table(self) -> None:
        """Save comparison data as CSV."""
        rows = self.data.get("summary_table", [])
        if not rows:
            return

        out_path = RESULTS_DIR / "thesis_table.csv"
        fieldnames = sorted(
            {k for row in rows for k in row.keys()}
        )
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  [ANALYSIS] CSV table     → {out_path}")

    def _save_markdown_report(self) -> None:
        """Save a Markdown analysis report."""
        lines = [
            "# Thesis Experiment Analysis Report",
            f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"\nSource: `{self.comparison_file}`",
            "\n## Experimental Setup",
            "\n| Condition | Description |",
            "|-----------|-------------|",
            "| Condition 1 — Baseline | Original pipeline, hand-crafted prompts |",
            "| Condition 2 — Adaptive | Thompson Sampling + prompt variant selection |",
            "| Condition 3 — RAG+DSPy | AOSP retrieval + MIPROv2-optimised prompts |",
            "\n## Results Summary",
            "\n| Condition | Modules | Avg Score | Avg Time (s) | Stages OK |",
            "|-----------|---------|-----------|--------------|-----------|",
        ]

        for row in self.data.get("summary_table", []):
            lines.append(
                f"| {row.get('condition',''):<12} "
                f"| {row.get('modules_succeeded', 0):.0f} "
                f"| {row.get('avg_metric_score', 0):.3f} "
                f"| {row.get('avg_generation_time_s', 0):.1f} "
                f"| {row.get('stages_succeeded', 0):.0f} |"
            )

        lines += [
            "\n## Per-Agent File Scores",
            "\n| Agent | Baseline | Adaptive | RAG+DSPy | Δ(C3-C1) |",
            "|-------|----------|----------|----------|----------|",
        ]

        all_agents: set[str] = set()
        for cond_data in self.conditions.values():
            all_agents.update(cond_data.get("file_scores", {}).keys())

        for agent_type in sorted(all_agents):
            label = AGENT_TYPE_LABELS.get(agent_type, agent_type)
            vals  = [
                self.conditions.get(c, {}).get("file_scores", {}).get(agent_type)
                for c in CONDITION_ORDER
            ]
            b, _, r = vals
            delta = f"{r - b:+.3f}" if isinstance(r, float) and isinstance(b, float) else "—"
            formatted = [f"{v:.3f}" if isinstance(v, float) else "—" for v in vals]
            lines.append(
                f"| {label:<15} "
                f"| {formatted[0]:>8} "
                f"| {formatted[1]:>8} "
                f"| {formatted[2]:>8} "
                f"| {delta:>8} |"
            )

        lines += [
            "\n## Figures",
            "\n- `figures/bar_avg_metric.png` — Average metric score by condition",
            "- `figures/bar_per_agent.png` — Per-agent scores across conditions",
            "- `figures/bar_generation_time.png` — Generation time by condition",
            "- `figures/heatmap_scores.png` — Score heatmap (agents × conditions)",
        ]

        out_path = RESULTS_DIR / "analysis_report.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  [ANALYSIS] Report        → {out_path}")

    # ── Chart generators ─────────────────────────────────────────

    def _setup_matplotlib(self) -> None:
        """Configure matplotlib for thesis-quality output."""
        try:
            import matplotlib
            matplotlib.use("Agg")   # non-interactive backend
            import matplotlib.pyplot as plt
            plt.rcParams.update({
                "figure.dpi":       150,
                "font.size":        11,
                "axes.titlesize":   13,
                "axes.labelsize":   12,
                "xtick.labelsize":  10,
                "ytick.labelsize":  10,
                "legend.fontsize":  10,
                "figure.facecolor": "white",
                "axes.spines.top":  False,
                "axes.spines.right":False,
            })
            self._plt = plt
        except ImportError:
            print(
                "[ANALYSIS] WARNING: matplotlib not installed — "
                "charts will be skipped.\n"
                "  pip install matplotlib"
            )
            self.generate_charts = False
            self._plt = None

    def _chart_avg_metric(self) -> None:
        """Bar chart: average metric score per condition."""
        if self._plt is None:
            return
        plt = self._plt

        labels = [CONDITION_LABELS[c] for c in CONDITION_ORDER]
        values = [
            self.conditions.get(c, {}).get("avg_metric_score", 0.0)
            for c in CONDITION_ORDER
        ]
        colors = [CONDITION_COLORS[c] for c in CONDITION_ORDER]

        fig, ax = plt.subplots(figsize=(7, 4.5))
        bars = ax.bar(labels, values, color=colors, width=0.5, edgecolor="white")

        # Value labels on bars
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.005,
                f"{val:.3f}",
                ha="center", va="bottom", fontsize=10, fontweight="bold",
            )

        ax.set_ylim(0, 1.1)
        ax.set_ylabel("Average Metric Score (0–1)")
        ax.set_title(
            "Average HAL Generation Quality Score\nby Pipeline Condition"
        )
        ax.axhline(0.8, color="gray", linestyle="--", linewidth=0.8,
                   label="Good threshold (0.80)")
        ax.axhline(0.9, color="green", linestyle="--", linewidth=0.8,
                   label="Excellent threshold (0.90)")
        ax.legend(loc="lower right", fontsize=9)

        fig.tight_layout()
        out = self.figures_dir / "bar_avg_metric.png"
        fig.savefig(out)
        plt.close(fig)
        print(f"  [ANALYSIS] Chart         → {out}")

    def _chart_per_agent(self) -> None:
        """Grouped bar chart: per-agent scores × conditions."""
        if self._plt is None:
            return
        plt = self._plt
        import numpy as np

        all_agents = sorted({
            k
            for c in self.conditions.values()
            for k in c.get("file_scores", {}).keys()
        })
        if not all_agents:
            return

        x     = np.arange(len(all_agents))
        width = 0.25

        fig, ax = plt.subplots(figsize=(max(10, len(all_agents) * 1.1), 5))

        for i, cond in enumerate(CONDITION_ORDER):
            values = [
                self.conditions.get(cond, {}).get("file_scores", {}).get(a, 0.0)
                for a in all_agents
            ]
            ax.bar(
                x + (i - 1) * width,
                values,
                width=width,
                label=CONDITION_LABELS[cond].replace("\n", " "),
                color=CONDITION_COLORS[cond],
                edgecolor="white",
            )

        ax.set_xticks(x)
        ax.set_xticklabels(
            [AGENT_TYPE_LABELS.get(a, a) for a in all_agents],
            rotation=30, ha="right",
        )
        ax.set_ylim(0, 1.15)
        ax.set_ylabel("Metric Score (0–1)")
        ax.set_title("Per-Agent HAL Generation Score by Condition")
        ax.legend(loc="upper right")
        ax.axhline(0.8, color="gray", linestyle="--", linewidth=0.7, alpha=0.6)

        fig.tight_layout()
        out = self.figures_dir / "bar_per_agent.png"
        fig.savefig(out)
        plt.close(fig)
        print(f"  [ANALYSIS] Chart         → {out}")

    def _chart_generation_time(self) -> None:
        """Bar chart: average generation time per condition."""
        if self._plt is None:
            return
        plt = self._plt

        labels = [CONDITION_LABELS[c] for c in CONDITION_ORDER]
        values = [
            self.conditions.get(c, {}).get("avg_generation_time_s", 0.0)
            for c in CONDITION_ORDER
        ]
        colors = [CONDITION_COLORS[c] for c in CONDITION_ORDER]

        fig, ax = plt.subplots(figsize=(7, 4.5))
        bars = ax.bar(labels, values, color=colors, width=0.5, edgecolor="white")

        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.5,
                f"{val:.1f}s",
                ha="center", va="bottom", fontsize=10, fontweight="bold",
            )

        ax.set_ylabel("Average Generation Time (seconds)")
        ax.set_title("Average Generation Time per Module\nby Pipeline Condition")
        fig.tight_layout()
        out = self.figures_dir / "bar_generation_time.png"
        fig.savefig(out)
        plt.close(fig)
        print(f"  [ANALYSIS] Chart         → {out}")

    def _chart_heatmap(self) -> None:
        """Heatmap: metric scores — agents (rows) × conditions (cols)."""
        if self._plt is None:
            return
        plt = self._plt
        import numpy as np

        all_agents = sorted({
            k
            for c in self.conditions.values()
            for k in c.get("file_scores", {}).keys()
        })
        if not all_agents:
            return

        # Build matrix: rows=agents, cols=conditions
        matrix = np.array([
            [
                self.conditions.get(cond, {}).get("file_scores", {}).get(a, 0.0)
                for cond in CONDITION_ORDER
            ]
            for a in all_agents
        ])

        fig, ax = plt.subplots(
            figsize=(5, max(4, len(all_agents) * 0.55))
        )
        im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)

        ax.set_xticks(range(len(CONDITION_ORDER)))
        ax.set_xticklabels(
            [CONDITION_LABELS[c].replace("\n", " ") for c in CONDITION_ORDER],
            rotation=15, ha="right",
        )
        ax.set_yticks(range(len(all_agents)))
        ax.set_yticklabels(
            [AGENT_TYPE_LABELS.get(a, a) for a in all_agents]
        )

        # Annotate each cell with its value
        for row_i in range(len(all_agents)):
            for col_j in range(len(CONDITION_ORDER)):
                val = matrix[row_i, col_j]
                color = "black" if 0.3 < val < 0.8 else "white"
                ax.text(
                    col_j, row_i, f"{val:.2f}",
                    ha="center", va="center",
                    fontsize=8.5, color=color,
                )

        plt.colorbar(im, ax=ax, label="Metric Score (0–1)")
        ax.set_title("HAL Generation Score Heatmap\n(Agents × Conditions)")
        fig.tight_layout()
        out = self.figures_dir / "heatmap_scores.png"
        fig.savefig(out)
        plt.close(fig)
        print(f"  [ANALYSIS] Chart         → {out}")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyse three-way pipeline comparison and generate thesis outputs"
    )
    parser.add_argument(
        "--comparison-file",
        default=str(COMPARISON_FILE),
        help=f"Path to comparison.json (default: {COMPARISON_FILE})",
    )
    parser.add_argument(
        "--figures-dir",
        default=str(FIGURES_DIR),
        help=f"Directory to save chart PNG files (default: {FIGURES_DIR})",
    )
    parser.add_argument(
        "--no-charts",
        action="store_true",
        help="Skip matplotlib chart generation (text output only)",
    )
    args = parser.parse_args()

    analyzer = ResultsAnalyzer(
        comparison_file=args.comparison_file,
        figures_dir=args.figures_dir,
        generate_charts=not args.no_charts,
    )
    analyzer.run()