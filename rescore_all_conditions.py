#!/usr/bin/env python3
"""
rescore_all_conditions.py  (v2 — AOSP tree layout)
───────────────────────────
Retroactive validator-based rescoring for ALL three conditions.

Output directories mirror an AOSP source tree:
    output/
      hardware/interfaces/automotive/vehicle/   ← .aidl, .cpp, .h, .bp
      sepolicy/.../                             ← .te
      backend/vss_dynamic_server/               ← .py
      docs/design/                              ← .md, .puml
      packages/apps/VssDynamicApp/              ← .kt, Android.bp
      ...

This script rglobs each output dir for scoreable file extensions,
classifies them, and applies the weighted scoring rubric.

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
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# ── paths ──────────────────────────────────────────────────────────
RESULTS_DIR = Path("experiments/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONDITIONS = {
    "baseline":     {"output_dir": Path("output"),              "out_file": RESULTS_DIR / "baseline.json"},
    "adaptive":     {"output_dir": Path("output_adaptive"),     "out_file": RESULTS_DIR / "adaptive.json"},
    "rag_dspy":     {"output_dir": Path("output_rag_dspy"),     "out_file": RESULTS_DIR / "rag_dspy.json"},
    "c4_feedback":  {"output_dir": Path("output_c4_feedback"),  "out_file": RESULTS_DIR / "c4_feedback.json"},
}

# ── scoring weights (must match thesis) ───────────────────────────
WEIGHTS = {
    "aidl":           {"struct": 0.30, "syntax": 0.50, "coverage": 0.20},
    "cpp":            {"struct": 0.35, "syntax": 0.45, "coverage": 0.20},
    "selinux":        {"struct": 0.25, "syntax": 0.65, "coverage": 0.10},
    "build":          {"struct": 0.35, "syntax": 0.55, "coverage": 0.10},
    "design_doc":     {"struct": 0.50, "syntax": 0.30, "coverage": 0.20},
    "android_app":    {"struct": 0.30, "syntax": 0.40, "coverage": 0.30},
    "android_layout": {"struct": 0.35, "syntax": 0.45, "coverage": 0.20},
    "backend":        {"struct": 0.25, "syntax": 0.50, "coverage": 0.25},
    "vintf":          {"struct": 0.40, "syntax": 0.50, "coverage": 0.10},
}

# Dirs/files to always skip
SKIP_DIRS = {".llm_draft", "__pycache__", ".git", "latest"}
SKIP_FILES = {"file_contexts", "PLAN.json", "MODULE_PLAN.json"}
SKIP_PREFIXES = ("SPEC_FROM_VSS", "VHAL_AIDL_RAW", "VHAL_SERVICE_RAW",
                 "VHAL_AIDL_BP_RAW", "VHAL_SERVICE_BP_RAW", "OLLAMA_HTTP")
SKIP_EXTENSIONS = {".txt", ".json", ".yaml", ".puml", ".h", ".java", ".rc"}


def classify_file(filepath: Path, output_root: Path) -> Optional[str]:
    """Classify a file into an agent type based on extension + path."""
    name = filepath.name
    suffix = filepath.suffix.lower()
    rel = str(filepath.relative_to(output_root))
    parts = set(Path(rel).parts)

    # Skip dirs
    if parts & SKIP_DIRS:
        return None
    # Skip known non-output files
    if name in SKIP_FILES:
        return None
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return None
    if suffix in SKIP_EXTENSIONS:
        return None

    # ── Classify by extension + path context ──

    if suffix == ".aidl":
        return "aidl"

    if suffix == ".te":
        return "selinux"

    if suffix == ".cpp":
        return "cpp"

    if name == "Android.bp" or suffix == ".bp":
        # Disambiguate: build in packages/apps → app build, else → hal build
        # Score both as "build" agent type
        return "build"

    if suffix == ".md":
        # Only score design docs, not README etc.
        if "design" in rel.lower() or "DESIGN" in name:
            return "design_doc"
        return None

    if suffix == ".kt":
        return "android_app"

    if suffix == ".xml":
        # Android layout XMLs (fragment_*.xml, activity_*.xml)
        if "layout" in rel.lower() or name.startswith("fragment_") or name.startswith("activity_"):
            return "android_layout"
        # VINTF manifest
        if name == "manifest.xml" or "vintf" in rel.lower():
            return "vintf"
        # Skip other XMLs (AndroidManifest.xml, strings.xml, etc.)
        return None

    if suffix == ".py":
        # Only score backend service files, not utility scripts
        if "backend" in rel.lower() or "server" in rel.lower():
            # Skip tiny helper files
            if filepath.stat().st_size > 100:
                return "backend"
        return None

    return None


def discover_scoreable_files(output_dir: Path) -> list[tuple[str, Path]]:
    """Walk the entire AOSP-structured output tree."""
    found = []
    if not output_dir.exists():
        return found

    for filepath in sorted(output_dir.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.stat().st_size < 20:
            continue
        agent = classify_file(filepath, output_dir)
        if agent:
            found.append((agent, filepath))

    return found


# ── Validator helpers ─────────────────────────────────────────────

def score_structure(content: str, agent_type: str) -> float:
    if agent_type == "aidl":
        checks = ["package ", "interface ", "@VintfStability"]
        parcelable_checks = ["parcelable ", "prop", "status"]
        hits = sum(1 for c in checks if c in content)
        hits += sum(1 for c in parcelable_checks if c in content)
        return round(min(hits / 5, 1.0), 4)
    elif agent_type == "cpp":
        checks = ["#include", "namespace", "class ", "getAllPropertyConfigs",
                   "getValues", "setValues"]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 5, 1.0), 4)
    elif agent_type == "selinux":
        checks = ["type ", "allow ", ";", "hal_", "domain"]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 4, 1.0), 4)
    elif agent_type == "build":
        checks = ["name:", "srcs:", "vendor:", "shared_libs"]
        alt = ['"name"', '"srcs"', "cc_binary", "cc_library", "aidl_interface"]
        hits = sum(1 for c in checks if c in content)
        hits += sum(1 for c in alt if c in content)
        return round(min(hits / 4, 1.0), 4)
    elif agent_type == "design_doc":
        lines = content.splitlines()
        has_h1 = any(l.startswith("# ") for l in lines)
        has_h2 = sum(1 for l in lines if l.startswith("## ")) >= 2
        body = len([l for l in lines if l.strip() and not l.startswith("#")])
        return round(0.4 * has_h1 + 0.3 * has_h2 + 0.3 * min(body / 20, 1.0), 4)
    elif agent_type == "android_app":
        checks = ["import ", "class ", "fun ", "override ", "Activity"]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 4, 1.0), 4)
    elif agent_type == "android_layout":
        checks = ["<LinearLayout", "<RelativeLayout", "<ConstraintLayout",
                   "<FrameLayout", "<ScrollView", "<TextView", "<Button",
                   "xmlns:android", "android:layout_width", "android:id"]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 4, 1.0), 4)
    elif agent_type == "vintf":
        checks = ["<manifest", "<hal", "format=", "android.hardware.automotive",
                   "<name>", "<version>", "<interface>"]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 4, 1.0), 4)
    elif agent_type == "backend":
        checks = ["import ", "def ", "class ", "return "]
        hits = sum(1 for c in checks if c in content)
        return round(min(hits / 3, 1.0), 4)
    return 0.5


def score_syntax(content: str, agent_type: str) -> float:
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
    elif agent_type == "android_layout":
        return _syntax_xml(content)
    elif agent_type == "vintf":
        return _syntax_xml(content)
    elif agent_type == "backend":
        return _syntax_python(content)
    return 0.5


def _syntax_aidl(content: str) -> float:
    issues = 0
    total = 5
    if "package " not in content: issues += 1
    if not re.search(r"(interface|parcelable)\s+\w+", content): issues += 1
    if content.count("{") != content.count("}"): issues += 1
    if "@VintfStability" not in content: issues += 0.5
    decl_lines = [l for l in content.splitlines()
                  if any(t in l for t in ["int ", "float ", "byte", "String ", "long "])]
    for l in decl_lines:
        if l.strip() and not l.strip().endswith(";") and not l.strip().endswith("{"):
            issues += 0.5
            break
    return round(max(0, (total - issues) / total), 4)


def _syntax_cpp(content: str) -> float:
    try:
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".cpp"))
        tmp.write_text(content)
        result = subprocess.run(
            ["clang++", "-fsyntax-only", "-std=c++17", str(tmp)],
            capture_output=True, timeout=10
        )
        tmp.unlink(missing_ok=True)
        if result.returncode == 0:
            return 1.0
        stderr = result.stderr.decode(errors="replace")
        real_errors = [l for l in stderr.splitlines()
                       if "error:" in l and "file not found" not in l]
        if not real_errors:
            return 0.9
        return round(max(0, 1.0 - len(real_errors) * 0.12), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    issues = 0
    if content.count("{") != content.count("}"): issues += 2
    if content.count("(") != content.count(")"): issues += 1
    if "#include" not in content: issues += 1
    return round(max(0, 1.0 - issues * 0.15), 4)


def _syntax_selinux(content: str) -> float:
    try:
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".te"))
        tmp.write_text(content)
        result = subprocess.run(
            ["checkpolicy", "-M", "-c", "30", "-o", "/dev/null", str(tmp)],
            capture_output=True, timeout=10
        )
        tmp.unlink(missing_ok=True)
        if result.returncode == 0:
            return 1.0
        errors = result.stderr.decode().count("ERROR")
        return round(max(0, 1.0 - errors * 0.2), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
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
    try:
        import json5
        json5.loads(content)
        return 1.0
    except Exception:
        pass
    issues = 0
    if content.count("{") != content.count("}"): issues += 2
    if content.count("[") != content.count("]"): issues += 1
    return round(max(0, 1.0 - issues * 0.2), 4)


def _syntax_markdown(content: str) -> float:
    lines = content.splitlines()
    has_h1 = any(l.startswith("# ") for l in lines)
    has_h2 = any(l.startswith("## ") for l in lines)
    has_body = len([l for l in lines if l.strip() and not l.startswith("#")]) > 5
    return round(0.4 * has_h1 + 0.3 * has_h2 + 0.3 * has_body, 4)


def _syntax_kotlin(content: str) -> float:
    try:
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".kt"))
        tmp.write_text(content)
        result = subprocess.run(
            ["kotlinc", "-nowarn", str(tmp)],
            capture_output=True, timeout=60
        )
        tmp.unlink(missing_ok=True)
        if result.returncode == 0:
            return 1.0
        errors = result.stderr.decode().count("error:")
        return round(max(0, 1.0 - errors * 0.15), 4)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    issues = 0
    if content.count("{") != content.count("}"): issues += 2
    if "class " not in content and "fun " not in content: issues += 1
    return round(max(0, 1.0 - issues * 0.2), 4)


def _syntax_python(content: str) -> float:
    import ast
    try:
        ast.parse(content)
        return 1.0
    except SyntaxError:
        return 0.3


def _syntax_xml(content: str) -> float:
    """Validate XML with xml.etree."""
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(content)
        return 1.0
    except ET.ParseError:
        # Check if it's close to valid (common LLM issues)
        issues = 0
        if content.count("<") != content.count(">"):
            issues += 2
        if "<?xml" not in content and "</" not in content:
            issues += 1
        return round(max(0, 0.6 - issues * 0.15), 4)


def score_coverage(content: str, agent_type: str) -> float:
    cl = content.lower()
    if agent_type == "aidl":
        terms = ["vehiclepropvalue", "vehiclepropertytype", "status", "prop",
                 "timestamp", "areaid", "value", "int32values", "floatvalues"]
    elif agent_type == "cpp":
        terms = ["getallpropertyconfigs", "getvalues", "setvalues", "vehiclepropconfig",
                 "vehiclepropvalue", "statuscode", "hidl", "aidl", "onpropertyevent"]
    elif agent_type == "selinux":
        terms = ["hal_", "domain", "allow", "binder", "hwservice", "vendor", "vehicle"]
    elif agent_type == "build":
        terms = ["vendor", "shared_libs", "srcs", "defaults", "name", "vehicle"]
    elif agent_type == "design_doc":
        terms = ["vhal", "property", "sensor", "architecture", "android", "vehicle", "aidl"]
    elif agent_type == "android_app":
        terms = ["vehicleproperty", "carpropertymanager", "car", "activity",
                 "viewmodel", "binding", "getproperty", "registercallback"]
    elif agent_type == "android_layout":
        terms = ["android:id", "android:text", "textview", "button", "switch",
                 "seekbar", "layout_width", "layout_height", "vehicle", "property"]
    elif agent_type == "vintf":
        terms = ["manifest", "hal", "automotive", "vehicle", "interface",
                 "version", "format", "aidl", "name"]
    elif agent_type == "backend":
        terms = ["flask", "fastapi", "route", "endpoint", "request", "response",
                 "json", "vehicle", "property", "vss"]
    else:
        return 0.5
    hits = sum(1 for t in terms if t.lower() in cl)
    return round(min(hits / max(len(terms) - 2, 1), 1.0), 4)


def score_file(agent_type: str, filepath: Path, output_root: Path) -> dict[str, Any]:
    content = filepath.read_text(errors="replace")
    rel_path = str(filepath.relative_to(output_root))

    if len(content.strip()) < 20:
        return {"file": rel_path, "agent": agent_type, "score": 0.0,
                "struct": 0.0, "syntax": 0.0, "coverage": 0.0, "skipped": "empty"}

    w = WEIGHTS[agent_type]
    s_struct   = score_structure(content, agent_type)
    s_syntax   = score_syntax(content, agent_type)
    s_coverage = score_coverage(content, agent_type)
    total = w["struct"] * s_struct + w["syntax"] * s_syntax + w["coverage"] * s_coverage

    return {
        "file": rel_path,
        "agent": agent_type,
        "struct": s_struct,
        "syntax": s_syntax,
        "coverage": s_coverage,
        "score": round(total, 4),
    }


# ── Main rescore ──────────────────────────────────────────────────

def rescore(label: str, output_dir: Path, out_file: Path) -> dict:
    if not output_dir.exists():
        print(f"  ⚠ {output_dir}/ not found — skipping {label}")
        return {}

    files = discover_scoreable_files(output_dir)
    if not files:
        print(f"  ⚠ No scoreable files in {output_dir}/")
        return {}

    print(f"  Found {len(files)} scoreable files:")
    agent_counts = defaultdict(int)
    for agent, fp in files:
        agent_counts[agent] += 1
    for agent, count in sorted(agent_counts.items()):
        print(f"    {agent:12s}: {count}")

    results = []
    for agent_type, filepath in files:
        r = score_file(agent_type, filepath, output_dir)
        results.append(r)

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


def main():
    parser = argparse.ArgumentParser(description="Rescore VHAL pipeline outputs (v2)")
    parser.add_argument("--only", choices=["c1", "c2", "c3", "baseline", "adaptive", "rag_dspy"])
    args = parser.parse_args()

    alias = {"c1": "baseline", "c2": "adaptive", "c3": "rag_dspy"}
    only = alias.get(args.only, args.only) if args.only else None

    print("=" * 60)
    print("VHAL Code Generation — Retroactive Rescoring (v2)")
    print("=" * 60)

    summaries = {}
    for label, cfg in CONDITIONS.items():
        if only and label != only:
            continue
        print(f"\n[{label}]")
        s = rescore(label, cfg["output_dir"], cfg["out_file"])
        if s:
            summaries[label] = s

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
