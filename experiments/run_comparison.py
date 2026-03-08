"""
experiments/run_comparison.py
──────────────────────────────
Reads per-condition result JSON files and merges them into a single
comparison.json used by analyze_results.py.

Expected result files (written by each pipeline):
  experiments/results/baseline.json     ← written by multi_main.py
  experiments/results/adaptive.json     ← written by multi_main_adaptive.py
  experiments/results/rag_dspy.json     ← written by multi_main_rag_dspy.py

Run:
  python experiments/run_comparison.py
"""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path("experiments/results")
OUTPUT_FILE = RESULTS_DIR / "comparison.json"

# Map condition key -> (label, result filename)
CONDITIONS = [
    ("baseline", "Condition 1 — Baseline (LLM-First)",     "baseline.json"),
    ("adaptive", "Condition 2 — Adaptive (Thompson + RL)", "adaptive.json"),
    ("rag_dspy", "Condition 3 — RAG + DSPy (MIPROv2)",     "rag_dspy.json"),
]

ALL_AGENTS = [
    "aidl", "cpp", "selinux", "build", "vintf",
    "design_doc", "puml",
    "android_app", "android_layout",
    "backend", "backend_model", "simulator",
]


def load_result(path: Path) -> dict | None:
    if not path.exists():
        print(f"  [SKIP] Not found: {path}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"  [OK]   Loaded: {path}  (avg_score={data.get('avg_metric_score', 'N/A')})")
        return data
    except Exception as e:
        print(f"  [ERR]  {path}: {e}")
        return None


def extract_per_agent(data: dict) -> dict[str, float]:
    """Return {agent_type: avg_score} from a result dict."""
    return data.get("per_agent_avg_scores", {})


def build_summary_row(key: str, label: str, data: dict) -> dict:
    return {
        "condition":             key,
        "label":                 label,
        "total_signals":         data.get("total_signals", 0),
        "modules_succeeded":     data.get("modules_succeeded", 0),
        "avg_metric_score":      round(data.get("avg_metric_score", 0.0), 4),
        "avg_generation_time_s": round(data.get("avg_generation_time_s", 0.0), 2),
        "stages_succeeded":      data.get("stages_succeeded", 0),
        "stages_total":          data.get("stages_total", 0),
        "total_run_time_s":      round(data.get("total_run_time_s", 0.0), 1),
    }


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  run_comparison.py — merging condition results")
    print("=" * 60)

    conditions_out = {}
    summary_table  = []
    # agent -> {condition: score}
    per_agent_matrix: dict[str, dict[str, float]] = {a: {} for a in ALL_AGENTS}

    for key, label, filename in CONDITIONS:
        print(f"\n[{key}] {label}")
        data = load_result(RESULTS_DIR / filename)
        if data is None:
            print(f"  -> Skipping (no results yet — run the pipeline first)")
            continue

        per_agent = extract_per_agent(data)

        conditions_out[key] = {
            "label":                 label,
            "total_signals":         data.get("total_signals", 0),
            "total_modules":         data.get("total_modules", 0),
            "modules_succeeded":     data.get("modules_succeeded", 0),
            "total_run_time_s":      data.get("total_run_time_s", 0.0),
            "avg_metric_score":      data.get("avg_metric_score", 0.0),
            "avg_generation_time_s": data.get("avg_generation_time_s", 0.0),
            "stages_succeeded":      data.get("stages_succeeded", 0),
            "stages_total":          data.get("stages_total", 0),
            "adaptive_success_rate": data.get("adaptive_success_rate", None),
            "file_scores":           per_agent,
            "per_stage":             _build_per_stage(data),
            "rag_top_k":             data.get("rag_top_k", None),
            "dspy_optimised":        data.get("dspy_optimised", False),
        }

        for agent, score in per_agent.items():
            if agent in per_agent_matrix:
                per_agent_matrix[agent][key] = score

        summary_table.append(build_summary_row(key, label, data))

    comparison = {
        "generated_at":  datetime.now().isoformat(timespec="seconds"),
        "conditions":    conditions_out,
        "summary_table": summary_table,
        "per_agent_matrix": per_agent_matrix,
    }

    OUTPUT_FILE.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n[OUT] Saved -> {OUTPUT_FILE}")

    # Print quick summary table
    print()
    print(f"  {'Condition':<12} {'AvgScore':>9} {'Time(s)':>9} {'Stages':>8}")
    print("  " + "-" * 44)
    for row in summary_table:
        print(f"  {row['condition']:<12} {row['avg_metric_score']:>9.4f} "
              f"{row['avg_generation_time_s']:>9.1f} "
              f"{row['stages_succeeded']:>4}/{row['stages_total']:<4}")

    missing = [k for k, _, _ in CONDITIONS
               if k not in conditions_out]
    if missing:
        print(f"\n  [WARN] Missing conditions: {', '.join(missing)}")
        print("  Run the corresponding pipeline(s) first:")
        for k in missing:
            script = {"baseline": "multi_main.py",
                      "adaptive": "multi_main_adaptive.py",
                      "rag_dspy": "multi_main_rag_dspy.py"}[k]
            print(f"    python {script}")


def _build_per_stage(data: dict) -> dict:
    """Aggregate per_stage_metrics list into a summary dict."""
    stage_map: dict[str, dict] = {}
    for m in data.get("per_stage_metrics", []):
        stage = m.get("stage", "unknown")
        if stage not in stage_map:
            stage_map[stage] = {
                "total": 0, "succeeded": 0,
                "total_time_s": 0.0, "scores": []
            }
        s = stage_map[stage]
        s["total"] += 1
        if m.get("success"):
            s["succeeded"] += 1
        s["total_time_s"] += m.get("generation_time", 0.0)
        score = m.get("metric_score")
        if score is not None:
            s["scores"].append(score)
        for v in m.get("file_scores", {}).values():
            s["scores"].append(v)

    out = {}
    for stage, s in stage_map.items():
        scores = s["scores"]
        out[stage] = {
            "total":        s["total"],
            "succeeded":    s["succeeded"],
            "total_time_s": round(s["total_time_s"], 2),
            "avg_score":    round(sum(scores) / len(scores), 4) if scores else 0.0,
            "avg_time_s":   round(s["total_time_s"] / max(s["total"], 1), 2),
        }
    return out


if __name__ == "__main__":
    main()
