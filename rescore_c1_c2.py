"""
rescore_c1_c2.py
════════════════════════════════════════════════════════════════════════════════
Retroactively score C1 (output/) and C2 (output_adaptive/) using the same
dspy_opt/metrics.score_file() validators used by C3.

Run from project root:
    python rescore_c1_c2.py

Overwrites:
    experiments/results/baseline.json
    experiments/results/adaptive.json

Then re-run:
    python experiments/run_comparison.py
    python experiments/analyze_results.py
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import json, time
from pathlib import Path

# ── import scorer ─────────────────────────────────────────────────────────────
from dspy_opt.metrics import score_file

RESULTS_DIR = Path("experiments/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── file-type → (agent_type, glob_pattern) ───────────────────────────────────
# Maps each output file type to the metrics.score_file agent_type key.
FILE_SCORERS = [
    ("aidl",           "**/*.aidl"),
    ("cpp",            "**/*.cpp"),
    ("selinux",        "**/*.te"),
    ("build",          "**/Android.bp"),
    ("design_doc",     "**/*.md"),
    ("android_app",    "**/*.kt"),
    ("backend",        "**/main.py"),
    ("backend_model",  "**/models.py"),
    ("simulator",      "**/simulator.py"),
]

# ── helpers ───────────────────────────────────────────────────────────────────

def score_directory(output_dir: Path) -> dict:
    """
    Walk output_dir, score every matched file, return results dict
    in the schema expected by run_comparison.py.
    """
    per_stage_metrics: list[dict] = []
    seen_types: set[str] = set()

    for agent_type, pattern in FILE_SCORERS:
        files = sorted(output_dir.rglob(pattern))
        if not files:
            continue

        scores = []
        total_time = 0.0
        for fpath in files:
            try:
                code = fpath.read_text(errors="replace")
                t0   = time.perf_counter()
                s    = score_file(agent_type, code)
                elapsed = time.perf_counter() - t0
                scores.append(s)
                total_time += elapsed
                print(f"    {agent_type:<15} {fpath.name:<45} score={s:.3f}  ({elapsed:.2f}s)")
            except Exception as exc:
                print(f"    {agent_type:<15} {fpath.name:<45} ERROR: {exc}")
                scores.append(0.0)

        avg = round(sum(scores) / len(scores), 4) if scores else 0.0
        ok  = sum(1 for s in scores if s >= 0.5)

        per_stage_metrics.append({
            "stage":           agent_type,
            "success":         ok == len(scores),
            "metric_score":    avg,
            "generation_time": round(total_time, 2),
            "files_scored":    len(scores),
            "files_passed":    ok,
            "individual_scores": [round(s, 4) for s in scores],
        })
        seen_types.add(agent_type)

    if not per_stage_metrics:
        print(f"  [WARN] No scoreable files found in {output_dir}")
        return _empty_result()

    all_scores = [m["metric_score"] for m in per_stage_metrics]
    avg_metric = round(sum(all_scores) / len(all_scores), 4)
    succeeded  = sum(1 for m in per_stage_metrics if m["success"])
    total_t    = round(sum(m["generation_time"] for m in per_stage_metrics), 2)

    return {
        "avg_metric_score":     avg_metric,
        "total_time_s":         total_t,
        "stages_succeeded":     succeeded,
        "stages_total":         len(per_stage_metrics),
        "per_stage_metrics":    per_stage_metrics,
        "per_agent_avg_scores": {m["stage"]: m["metric_score"]
                                 for m in per_stage_metrics},
    }


def _empty_result() -> dict:
    return {
        "avg_metric_score":     0.0,
        "total_time_s":         0.0,
        "stages_succeeded":     0,
        "stages_total":         0,
        "per_stage_metrics":    [],
        "per_agent_avg_scores": {},
    }


# ── main ──────────────────────────────────────────────────────────────────────

def rescore(label: str, output_dir: Path, out_file: Path) -> None:
    print(f"\n{'─'*60}")
    print(f"  Rescoring {label}  ({output_dir})")
    print(f"{'─'*60}")

    if not output_dir.exists():
        print(f"  [SKIP] Directory not found: {output_dir}")
        return

    result = score_directory(output_dir)
    result["condition"] = label
    result["note"]      = f"Retroactively scored from {output_dir} by rescore_c1_c2.py"

    out_file.write_text(json.dumps(result, indent=2))

    avg = result["avg_metric_score"]
    s   = result["stages_succeeded"]
    t   = result["stages_total"]
    print(f"\n  → avg_metric_score={avg}  stages={s}/{t}")
    print(f"  → Saved: {out_file}")


if __name__ == "__main__":
    print("=" * 60)
    print("  rescore_c1_c2.py — retroactive validator scoring")
    print("=" * 60)

    rescore(
        label      = "baseline",
        output_dir = Path("output"),
        out_file   = RESULTS_DIR / "baseline.json",
    )

    rescore(
        label      = "adaptive",
        output_dir = Path("output_adaptive"),
        out_file   = RESULTS_DIR / "adaptive.json",
    )

    print(f"\n{'='*60}")
    print("  Done. Now run:")
    print("    python experiments/run_comparison.py")
    print("    python experiments/analyze_results.py")
    print("=" * 60)