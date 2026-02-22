"""
experiments/run_comparison.py
═══════════════════════════════════════════════════════════════════
Three-way experiment comparison runner.

Loads results from all three pipeline conditions, re-applies
uniform metric scoring, and outputs a single merged comparison
JSON used by analyze_results.py to produce thesis tables and charts.

Can also trigger a fresh run of one or all conditions if their
results file is missing or outdated.

Usage:
  # Compare already-run results (most common)
  python experiments/run_comparison.py

  # Re-run condition 3 then compare
  python experiments/run_comparison.py --rerun rag_dspy

  # Re-run all conditions then compare
  python experiments/run_comparison.py --rerun all

  # Show comparison table in terminal without saving
  python experiments/run_comparison.py --print-only
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────

RESULTS_DIR   = Path("experiments/results")
COMPARISON_OUT = RESULTS_DIR / "comparison.json"

CONDITION_CONFIG = {
    "baseline": {
        "label":       "Condition 1 — Baseline",
        "entry_point": "multi_main.py",
        "results_file": RESULTS_DIR / "baseline.json",
        "output_dir":  "output",
    },
    "adaptive": {
        "label":       "Condition 2 — Adaptive",
        "entry_point": "multi_main_adaptive.py",
        "results_file": RESULTS_DIR / "adaptive.json",
        "output_dir":  "output",
    },
    "rag_dspy": {
        "label":       "Condition 3 — RAG + DSPy",
        "entry_point": "multi_main_rag_dspy.py",
        "results_file": RESULTS_DIR / "rag_dspy.json",
        "output_dir":  "output_rag_dspy",
    },
}

# Stages scored in the per-stage breakdown
SCORED_STAGES = [
    "hal_module",
    "selinux",
    "design_doc",
    "android_app",
    "backend",
    "build_glue",
]


# ─────────────────────────────────────────────────────────────────
# Metric re-application helpers
# ─────────────────────────────────────────────────────────────────

# Map file glob patterns to agent_type keys
_FILE_PATTERNS: dict[str, list[str]] = {
    "aidl":           ["*.aidl"],
    "cpp":            ["*.cpp"],
    "selinux":        ["*.te"],
    "build":          ["Android.bp"],
    "vintf":          ["manifest*.xml"],
    "design_doc":     ["DESIGN_DOCUMENT.md"],
    "puml":           ["*.puml"],
    "android_app":    ["*Fragment*.kt"],
    "android_layout": ["fragment_*.xml"],
    "backend":        ["main.py"],
    "backend_model":  ["models_*.py"],
    "simulator":      ["simulator_*.py"],
}


def _score_generated_files(output_dir: str, condition: str) -> dict[str, float]:
    """
    Walk the output directory and score each generated file using
    score_file() from dspy_opt.metrics (three-tier: struct+syntax+coverage).

    Returns dict: {agent_type: avg_score}
    """
    try:
        from dspy_opt.metrics import score_file
    except ImportError:
        return {}

    output_path = Path(output_dir)
    if not output_path.exists():
        return {}

    scores: dict[str, list[float]] = {}

    for agent_type, patterns in _FILE_PATTERNS.items():
        for pattern in patterns:
            for fpath in output_path.rglob(pattern):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    if not content.strip():
                        continue
                    score = score_file(agent_type, content)
                    scores.setdefault(agent_type, []).append(score)
                except Exception:
                    pass

    return {
        agent_type: round(sum(vals) / len(vals), 4)
        for agent_type, vals in scores.items()
        if vals
    }


# ─────────────────────────────────────────────────────────────────
# ComparisonRunner
# ─────────────────────────────────────────────────────────────────

class ComparisonRunner:
    """
    Loads results from all three conditions, merges them into a
    unified comparison structure, and saves to comparison.json.

    Parameters
    ----------
    results_dir  : str  — directory containing per-condition JSON files
    rescore      : bool — if True, re-score generated files from disk
                          (more accurate but slower)
    """

    def __init__(
        self,
        results_dir: str  = "experiments/results",
        rescore:     bool = True,
    ):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.rescore = rescore

    # ── Public API ───────────────────────────────────────────────

    def run(self, print_only: bool = False) -> dict:
        """
        Load all condition results, merge, and optionally save.

        Returns
        -------
        dict — unified comparison structure
        """
        print("\n[COMPARISON] Loading results from all conditions...")
        print()

        loaded: dict[str, dict] = {}
        for condition, cfg in CONDITION_CONFIG.items():
            data = self._load_condition(condition, cfg)
            if data:
                loaded[condition] = data
            else:
                print(f"  [COMPARISON] WARNING: No results for {condition} — "
                      f"run {cfg['entry_point']} first")

        if not loaded:
            print("[COMPARISON] ERROR: No condition results found. "
                  "Run the pipeline entry points first.")
            return {}

        # Re-score from generated files if requested
        if self.rescore:
            print("\n[COMPARISON] Re-scoring generated files from disk...")
            for condition, cfg in CONDITION_CONFIG.items():
                if condition not in loaded:
                    continue
                file_scores = _score_generated_files(
                    cfg["output_dir"], condition
                )
                if file_scores:
                    loaded[condition]["file_scores"] = file_scores
                    print(f"  {condition}: scored {len(file_scores)} agent types")

        # Build comparison structure
        comparison = self._build_comparison(loaded)

        # Print table
        self._print_table(comparison)

        # Save
        if not print_only:
            out_path = self.results_dir / "comparison.json"
            out_path.write_text(
                json.dumps(comparison, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"\n[COMPARISON] Saved → {out_path}")
            print("[COMPARISON] Run analyze_results.py to generate thesis charts.")

        return comparison

    def rerun_condition(self, condition: str) -> bool:
        """
        Re-run a pipeline condition by calling its entry point as a subprocess.

        Parameters
        ----------
        condition : str — "baseline", "adaptive", or "rag_dspy"

        Returns
        -------
        bool — True if the run succeeded
        """
        cfg = CONDITION_CONFIG.get(condition)
        if cfg is None:
            print(f"[COMPARISON] Unknown condition: {condition}")
            return False

        entry = cfg["entry_point"]
        print(f"\n[COMPARISON] Re-running {condition} via: python {entry}")
        print(f"  This may take 30-90 minutes depending on hardware.")
        print()

        try:
            result = subprocess.run(
                [sys.executable, entry],
                check=True,
            )
            return result.returncode == 0
        except subprocess.CalledProcessError as e:
            print(f"[COMPARISON] {condition} run failed: {e}")
            return False

    # ── Private helpers ──────────────────────────────────────────

    def _load_condition(
        self,
        condition: str,
        cfg:       dict,
    ) -> Optional[dict]:
        """Load and validate a condition results file."""
        results_file = cfg["results_file"]
        if not results_file.exists():
            return None

        try:
            data = json.loads(
                results_file.read_text(encoding="utf-8")
            )
            print(f"  ✓ {condition:<12} ← {results_file.name}")
            return data
        except Exception as e:
            print(f"  ✗ {condition:<12}  parse error: {e}")
            return None

    def _build_comparison(self, loaded: dict[str, dict]) -> dict:
        """
        Build a unified comparison dict from all loaded condition data.

        Structure:
        {
          "generated_at": ...,
          "conditions": {
            "baseline": {
              "label": ...,
              "total_signals": ...,
              "modules_succeeded": ...,
              "avg_metric_score": ...,
              "avg_generation_time_s": ...,
              "stages_succeeded": ...,
              "stages_total": ...,
              "file_scores": {...},
              "per_stage": {...}
            },
            ...
          },
          "summary_table": [...]   ← flat rows for easy analysis
        }
        """
        conditions_out = {}

        for condition, data in loaded.items():
            cfg = CONDITION_CONFIG[condition]

            # Extract per-stage breakdown from per_stage_metrics list
            per_stage = self._extract_per_stage(
                data.get("per_stage_metrics", [])
            )

            conditions_out[condition] = {
                "label":                cfg["label"],
                "total_signals":        data.get("total_signals",        0),
                "total_modules":        data.get("total_modules",        0),
                "modules_succeeded":    data.get("modules_succeeded",    0),
                "total_run_time_s":     data.get("total_run_time_s",     0),
                "avg_metric_score":     data.get("avg_metric_score",     0.0),
                "avg_generation_time_s":data.get("avg_generation_time_s",0.0),
                "stages_succeeded":     data.get("stages_succeeded",     0),
                "stages_total":         data.get("stages_total",         0),
                "adaptive_success_rate":data.get("adaptive_stats", {})
                                            .get("overall_success_rate", None),
                "file_scores":          data.get("file_scores",          {}),
                "per_stage":            per_stage,
                "rag_top_k":            data.get("rag_top_k",            None),
                "dspy_optimised":       data.get("dspy_optimised",       None),
            }

        # Build flat summary table for easy CSV/LaTeX export
        summary_table = self._build_summary_table(conditions_out)

        return {
            "generated_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "conditions":    conditions_out,
            "summary_table": summary_table,
        }

    def _extract_per_stage(
        self,
        per_stage_metrics: list[dict],
    ) -> dict[str, dict]:
        """
        Aggregate per-stage metrics from the flat list stored by
        multi_main_rag_dspy.py into a dict keyed by stage name.
        """
        aggregated: dict[str, dict] = {}

        for m in per_stage_metrics:
            stage = m.get("stage", "unknown")
            if stage not in aggregated:
                aggregated[stage] = {
                    "total":       0,
                    "succeeded":   0,
                    "total_time_s":0.0,
                    "avg_score":   0.0,
                    "scores":      [],
                }
            agg = aggregated[stage]
            agg["total"]        += 1
            agg["succeeded"]    += int(m.get("success", False))
            agg["total_time_s"] += m.get("generation_time", 0.0)
            if "metric_score" in m:
                agg["scores"].append(m["metric_score"])

        # Finalise averages
        for stage, agg in aggregated.items():
            scores = agg.pop("scores", [])
            agg["avg_score"]   = round(sum(scores) / len(scores), 4) if scores else 0.0
            agg["avg_time_s"]  = (
                round(agg["total_time_s"] / agg["total"], 2)
                if agg["total"] > 0 else 0.0
            )

        return aggregated

    def _build_summary_table(
        self,
        conditions: dict[str, dict],
    ) -> list[dict]:
        """
        Build a flat list of rows, one per condition, for easy export.
        """
        rows = []
        for cond_key, data in conditions.items():
            row = {
                "condition":             cond_key,
                "label":                 data["label"],
                "total_signals":         data["total_signals"],
                "modules_succeeded":     data["modules_succeeded"],
                "avg_metric_score":      data["avg_metric_score"],
                "avg_generation_time_s": data["avg_generation_time_s"],
                "stages_succeeded":      data["stages_succeeded"],
                "stages_total":          data["stages_total"],
                "total_run_time_s":      data["total_run_time_s"],
            }
            # Add per-agent file scores if available
            for agent_type, score in data.get("file_scores", {}).items():
                row[f"score_{agent_type}"] = score
            rows.append(row)
        return rows

    def _print_table(self, comparison: dict) -> None:
        """Print a formatted comparison table to stdout."""
        conditions = comparison.get("conditions", {})
        if not conditions:
            return

        print("\n" + "═" * 70)
        print("  THREE-WAY COMPARISON RESULTS")
        print("═" * 70)
        print(
            f"  {'Metric':<30} "
            f"{'Baseline':>10} "
            f"{'Adaptive':>10} "
            f"{'RAG+DSPy':>10}"
        )
        print("  " + "─" * 64)

        metrics = [
            ("Modules succeeded",      "modules_succeeded",     ".0f"),
            ("Avg metric score",       "avg_metric_score",      ".3f"),
            ("Avg gen time (s)",       "avg_generation_time_s", ".1f"),
            ("Stages succeeded",       "stages_succeeded",      ".0f"),
            ("Total run time (s)",     "total_run_time_s",      ".0f"),
        ]

        for label, key, fmt in metrics:
            vals = []
            for cond in ["baseline", "adaptive", "rag_dspy"]:
                v = conditions.get(cond, {}).get(key, "—")
                vals.append(f"{v:{fmt}}" if isinstance(v, (int, float)) else str(v))
            print(f"  {label:<30} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10}")

        # Per-agent file scores if available
        all_agent_types = set()
        for cond_data in conditions.values():
            all_agent_types.update(cond_data.get("file_scores", {}).keys())

        if all_agent_types:
            print("  " + "─" * 64)
            print(f"  {'File-level scores':<30}")
            for agent_type in sorted(all_agent_types):
                vals = []
                for cond in ["baseline", "adaptive", "rag_dspy"]:
                    v = conditions.get(cond, {}).get(
                        "file_scores", {}
                    ).get(agent_type, "—")
                    vals.append(f"{v:.3f}" if isinstance(v, float) else str(v))
                print(
                    f"  {agent_type:<30} "
                    f"{vals[0]:>10} {vals[1]:>10} {vals[2]:>10}"
                )

        print("═" * 70)

        # Delta column (RAG+DSPy vs Baseline)
        b_score = conditions.get("baseline", {}).get("avg_metric_score", None)
        r_score = conditions.get("rag_dspy", {}).get("avg_metric_score", None)
        if b_score is not None and r_score is not None:
            delta = r_score - b_score
            symbol = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            print(
                f"\n  RAG+DSPy vs Baseline avg metric score: "
                f"{symbol} {delta:+.3f} "
                f"({delta / b_score * 100:+.1f}%)"
                if b_score != 0 else
                f"\n  RAG+DSPy vs Baseline avg metric score: {delta:+.3f}"
            )
        print()


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare results from all three pipeline conditions"
    )
    parser.add_argument(
        "--rerun",
        choices=["baseline", "adaptive", "rag_dspy", "all"],
        default=None,
        help="Re-run a condition before comparing (warning: slow)",
    )
    parser.add_argument(
        "--no-rescore",
        action="store_true",
        help="Skip re-scoring generated files from disk (faster)",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print comparison table without saving comparison.json",
    )
    parser.add_argument(
        "--results-dir",
        default="experiments/results",
        help="Directory containing condition result JSON files",
    )
    args = parser.parse_args()

    runner = ComparisonRunner(
        results_dir=args.results_dir,
        rescore=not args.no_rescore,
    )

    # Re-run conditions if requested
    if args.rerun:
        targets = (
            list(CONDITION_CONFIG.keys())
            if args.rerun == "all"
            else [args.rerun]
        )
        for condition in targets:
            ok = runner.rerun_condition(condition)
            if not ok:
                print(f"[COMPARISON] {condition} run failed — aborting comparison")
                sys.exit(1)

    runner.run(print_only=args.print_only)