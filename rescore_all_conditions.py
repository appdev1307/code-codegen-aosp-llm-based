#!/usr/bin/env python3
"""
rescore_all_conditions.py
─────────────────────────
Retroactive validator-based rescoring for ALL three conditions:
  C1 (Baseline)      → output/
  C2 (Adaptive)      → output_adaptive/
  C3 (RAG+DSPy)      → output_rag_dspy/

Scoring logic is identical for every condition — same validators,
same weights, same rubric.  This replaces the old rescore_c1_c2.py
and adds C3 support.

Usage:
    python rescore_all_conditions.py            # rescore all three
    python rescore_all_conditions.py --only c3  # rescore just C3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

# ── paths ──────────────────────────────────────────────────────────
RESULTS_DIR = Path("experiments/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONDITIONS = {
    "baseline":  {"output_dir": Path("output"),            "out_file": RESULTS_DIR / "baseline.json"},
    "adaptive":  {"output_dir": Path("output_adaptive"),   "out_file": RESULTS_DIR / "adaptive.json"},
    "rag_dspy":  {"output_dir": Path("output_rag_dspy"),   "out_file": RESULTS_DIR / "rag_dspy.json"},
}

# ── scoring weights (must match thesis Table X) ───────────────────
WEIGHTS = {
    "aidl":        {"struct": 0.30, "syntax": 0.50, "coverage": 0.20},
    "cpp":         {"struct": 0.35, "syntax": 0.45, "coverage": 0.20},
    "selinux":     {"struct": 0.25, "syntax": 0.65, "coverage": 0.10},
    "build":       {"struct": 0.35, "syntax": 0.55, "coverage": 0.10},
    "design_doc":  {"struct": 0.50, "syntax": 0.30, "coverage": 0.20},
    "android_app": {"struct": 0.30, "syntax": 0.40, "coverage": 0.30},
    "backend":     {"struct": 0.25, "syntax": 0.50, "coverage": 0.25},
}

# Map file-name patterns → agent type
FILE_PATTERNS = {
    r"\.aidl$":                       "aidl",
    r"VehicleHalServer\.cpp$":        "cpp",
    r"\.cpp$":                        "cpp",
    r"\.te$":                         "selinux",
    r"Android\.bp$":                  "build",
    r"design_doc.*\.md$":             "design_doc",
    r"\.kt$":                         "android_app",
    r"\.py$":                         "backend",
}


# ── Validator helpers ─────────────────────────────────────────────
def classify_file(filename: str) -> str | None:
    """Return agent type for a given filename, or None if unrecognised."""
    for pattern, agent_type in FILE_PATTERNS.items():
        if re.search(pattern, filename, re.IGNORECASE):
            return agent_type
    return None


def score_structure(content: str, agent_type: str) -> float:
    """Heuristic structural score (0-1)."""
    score = 0.0
    lines = content.strip().splitlines()
    if not lines:
        return 0.0

    if agent_type == "aidl":
        checks = ["package ", "interface ", "parcelable ", "@VintfStability"]
        score = sum(1 for c in checks if c in content) / len(checks)
    elif agent_type == "cpp":
        checks = ["#include", "namespace", "getAllPropertyConfigs", "getValues", "setValues", "Return<void>", "class "]
        score = sum(1 for c in checks if c in content) / max(len(checks), 1)
    elif agent_type == "selinux":
        checks = ["type ", "allow ", "domain", "hal_", ";"]
        score = sum(1 for c in checks if c in content) / len(checks)
    elif agent_type == "build":
        checks = ["cc_binary", "cc_library", "name:", "srcs:", "vendor: true", "shared_libs:"]
        alt_checks = ['"name"', '"srcs"', "vendor:", "shared_libs"]
        hits = sum(1 for c in checks if c in content)
        hits += sum(1 for c in alt_checks if c in content)
        score = min(hits / len(checks), 1.0)
    elif agent_type == "design_doc":
        checks = ["# ", "## ", "VHAL", "property", "sensor", "vehicle"]
        score = sum(1 for c in checks if c.lower() in content.lower()) / len(checks)
    elif agent_type == "android_app":
        checks = ["import ", "class ", "fun ", "override ", "Activity", "ViewModel"]
        score = sum(1 for c in checks if c in content) / len(checks)
    elif agent_type == "backend":
        checks = ["import ", "def ", "class ", "return ", "flask", "fastapi", "__name__"]
        score = sum(1 for c in checks if c.lower() in content.lower()) / len(checks)

    return round(min(score, 1.0), 4)


def score_syntax(content: str, agent_type: str) -> float:
    """
    Syntax validation score (0-1).
    Uses external tools where available, falls back to heuristic.
    """
    if agent_type == "aidl":
        return _syntax_aidl(content)
    elif agent_type == "cpp":
        return _syntax_cpp(content)
    elif agent_type == "selinux":
        return _syntax_selinux(content)
    elif agent_type == "build":
        return _syntax_build(content)
    elif agent_type == "design_doc":
        return _syntax_markdown(content)
    elif agent_type == "android_app":
        return _syntax_kotlin(content)
    elif agent_type == "backend":
        return _syntax_python(content)
    return 0.5


def _syntax_aidl(content: str) -> float:
    """Simple AIDL grammar check."""
    issues = 0
    total = 5
    if "package " not in content:
        issues += 1
    if not re.search(r"interface\s+\w+", content):
        issues += 1
    # Check balanced braces
    if content.count("{") != content.count("}"):
        issues += 1
    # Check semicolons on declarations
    decl_lines = [l for l in content.splitlines() if any(t in l for t in ["int ", "float ", "byte[]", "String ", "long "])]
    for l in decl_lines:
        if not l.strip().endswith(";"):
            issues += 1
            break
    if "@VintfStability" not in content and "VintfStability" not in content:
        issues += 0.5
    return round(max(0, (total - issues) / total), 4)


def _syntax_cpp(content: str) -> float:
    """Try clang++ --syntax-only, fallback to heuristic."""
    try:
        tmp = Path("/tmp/_rescore_tmp.cpp")
        tmp.write_text(content)
        result = subprocess.run(
            ["clang++", "--syntax-only", "-std=c++17", str(tmp)],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return 1.0
        errors = result.stderr.decode().count("error:")
        return round(max(0, 1.0 - errors * 0.15), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Heuristic fallback
        issues = 0
        if content.count("{") != content.count("}"):
            issues += 2
        if content.count("(") != content.count(")"):
            issues += 1
        if "#include" not in content:
            issues += 1
        return round(max(0, 1.0 - issues * 0.15), 4)


def _syntax_selinux(content: str) -> float:
    """Try checkpolicy, fallback to heuristic."""
    try:
        tmp = Path("/tmp/_rescore_tmp.te")
        tmp.write_text(content)
        result = subprocess.run(
            ["checkpolicy", "-M", "-c", "30", "-o", "/dev/null", str(tmp)],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return 1.0
        errors = result.stderr.decode().count("ERROR")
        return round(max(0, 1.0 - errors * 0.2), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        issues = 0
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("type ", "allow ", "neverallow ", "typeattribute ")):
                if not line.endswith(";"):
                    issues += 1
        return round(max(0, 1.0 - issues * 0.15), 4)


def _syntax_build(content: str) -> float:
    """Validate Android.bp as JSON5-ish."""
    try:
        import json5  # type: ignore
        json5.loads(content)
        return 1.0
    except Exception:
        pass
    # Heuristic: balanced braces, colons, brackets
    issues = 0
    if content.count("{") != content.count("}"):
        issues += 2
    if content.count("[") != content.count("]"):
        issues += 1
    return round(max(0, 1.0 - issues * 0.2), 4)


def _syntax_markdown(content: str) -> float:
    """Markdown structure check."""
    lines = content.splitlines()
    has_h1 = any(l.startswith("# ") for l in lines)
    has_h2 = any(l.startswith("## ") for l in lines)
    has_body = len([l for l in lines if l.strip() and not l.startswith("#")]) > 5
    return round((0.4 * has_h1 + 0.3 * has_h2 + 0.3 * has_body), 4)


def _syntax_kotlin(content: str) -> float:
    """Try kotlinc, fallback to heuristic."""
    try:
        tmp = Path("/tmp/_rescore_tmp.kt")
        tmp.write_text(content)
        result = subprocess.run(
            ["kotlinc", "-script", str(tmp)],
            capture_output=True, timeout=30
        )
        if result.returncode == 0:
            return 1.0
        errors = result.stderr.decode().count("error:")
        return round(max(0, 1.0 - errors * 0.15), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        issues = 0
        if content.count("{") != content.count("}"):
            issues += 2
        if "class " not in content and "fun " not in content:
            issues += 1
        return round(max(0, 1.0 - issues * 0.2), 4)


def _syntax_python(content: str) -> float:
    """Use ast.parse."""
    import ast
    try:
        ast.parse(content)
        return 1.0
    except SyntaxError:
        return 0.3


def score_coverage(content: str, agent_type: str) -> float:
    """Domain-coverage heuristic (0-1): does the generated code cover
    the key concepts expected for this agent type in VHAL context?"""
    content_lower = content.lower()

    if agent_type == "aidl":
        terms = ["vehiclepropvalue", "vehiclepropertytype", "status", "prop", "timestamp", "areaId", "value"]
    elif agent_type == "cpp":
        terms = ["getAllPropertyConfigs", "getValues", "setValues", "VehiclePropConfig",
                 "VehiclePropValue", "StatusCode", "hidl", "aidl"]
    elif agent_type == "selinux":
        terms = ["hal_", "domain", "allow", "binder", "hwservice", "vendor"]
    elif agent_type == "build":
        terms = ["vendor", "shared_libs", "srcs", "defaults", "name"]
    elif agent_type == "design_doc":
        terms = ["vhal", "property", "sensor", "architecture", "android", "vehicle"]
    elif agent_type == "android_app":
        terms = ["vehicleproperty", "carpropertymanager", "car", "activity", "viewmodel", "binding"]
    elif agent_type == "backend":
        terms = ["flask", "fastapi", "route", "endpoint", "request", "response", "json"]
    else:
        return 0.5

    hits = sum(1 for t in terms if t.lower() in content_lower)
    return round(min(hits / max(len(terms) - 1, 1), 1.0), 4)


def score_file(filepath: Path) -> dict[str, Any] | None:
    """Score a single output file.  Returns a dict or None if unrecognised."""
    agent_type = classify_file(filepath.name)
    if agent_type is None:
        return None

    content = filepath.read_text(errors="replace")
    if len(content.strip()) < 20:
        return {"file": filepath.name, "agent": agent_type, "score": 0.0,
                "struct": 0.0, "syntax": 0.0, "coverage": 0.0, "skipped": "empty"}

    w = WEIGHTS[agent_type]
    s_struct   = score_structure(content, agent_type)
    s_syntax   = score_syntax(content, agent_type)
    s_coverage = score_coverage(content, agent_type)
    total = (w["struct"] * s_struct + w["syntax"] * s_syntax + w["coverage"] * s_coverage)

    return {
        "file": filepath.name,
        "agent": agent_type,
        "struct": s_struct,
        "syntax": s_syntax,
        "coverage": s_coverage,
        "score": round(total, 4),
    }


# ── Main rescore logic ────────────────────────────────────────────
def rescore(label: str, output_dir: Path, out_file: Path) -> dict:
    """Rescore every recognised file in output_dir, write JSON results."""
    if not output_dir.exists():
        print(f"  ⚠ {output_dir}/ not found — skipping {label}")
        return {}

    results = []
    for spec_dir in sorted(output_dir.iterdir()):
        if not spec_dir.is_dir():
            continue
        for fpath in sorted(spec_dir.iterdir()):
            if fpath.is_file():
                r = score_file(fpath)
                if r:
                    r["spec"] = spec_dir.name
                    results.append(r)

    # Also check loose files in output_dir root
    for fpath in sorted(output_dir.iterdir()):
        if fpath.is_file():
            r = score_file(fpath)
            if r:
                r["spec"] = "_root"
                results.append(r)

    if not results:
        print(f"  ⚠ No scoreable files in {output_dir}/")
        return {}

    scores = [r["score"] for r in results]
    summary = {
        "condition": label,
        "output_dir": str(output_dir),
        "num_files": len(results),
        "avg_score": round(sum(scores) / len(scores), 4),
        "min_score": round(min(scores), 4),
        "max_score": round(max(scores), 4),
        "per_agent": _per_agent_summary(results),
        "files": results,
    }

    out_file.write_text(json.dumps(summary, indent=2))
    print(f"  ✓ {label}: avg={summary['avg_score']:.4f}  "
          f"({len(results)} files)  → {out_file}")
    return summary


def _per_agent_summary(results: list[dict]) -> dict:
    from collections import defaultdict
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in results:
        buckets[r["agent"]].append(r["score"])
    return {
        agent: {
            "count": len(scores),
            "avg": round(sum(scores) / len(scores), 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
        }
        for agent, scores in sorted(buckets.items())
    }


# ── CLI ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rescore VHAL pipeline outputs")
    parser.add_argument("--only", choices=["c1", "c2", "c3", "baseline", "adaptive", "rag_dspy"],
                        help="Rescore only one condition")
    args = parser.parse_args()

    alias = {"c1": "baseline", "c2": "adaptive", "c3": "rag_dspy"}
    only = alias.get(args.only, args.only) if args.only else None

    print("=" * 60)
    print("VHAL Code Generation — Retroactive Rescoring")
    print("=" * 60)

    summaries = {}
    for label, cfg in CONDITIONS.items():
        if only and label != only:
            continue
        print(f"\n[{label}]")
        s = rescore(label, cfg["output_dir"], cfg["out_file"])
        if s:
            summaries[label] = s

    # Write merged comparison
    if len(summaries) > 1:
        comp_file = RESULTS_DIR / "comparison.json"
        comparison = {
            label: {"avg_score": s["avg_score"], "num_files": s["num_files"], "per_agent": s["per_agent"]}
            for label, s in summaries.items()
        }
        comp_file.write_text(json.dumps(comparison, indent=2))
        print(f"\n→ Comparison written to {comp_file}")

    print("\nDone.")


if __name__ == "__main__":
    main()
