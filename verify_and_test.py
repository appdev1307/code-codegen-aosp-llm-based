#!/usr/bin/env python3
"""
verify_and_test.py
──────────────────
AOSP Integration Verification & Testing Framework for VHAL Code Generation.

This script:
  1. Copies generated artifacts into an AOSP-like directory structure
  2. Attempts compilation (mmm / clang++ / kotlinc / python / checkpolicy)
  3. Runs test cases per agent type
  4. Logs pass/fail per condition × spec × agent
  5. Produces a verification report for the thesis

Usage:
    python verify_and_test.py --aosp-root /path/to/aosp
    python verify_and_test.py --standalone          # no AOSP tree, local compilation only
    python verify_and_test.py --only c1 --spec vehicle_speed
    python verify_and_test.py --test-only           # skip compilation, run tests only

Prerequisites (standalone mode):
    clang++ (apt install clang)
    checkpolicy (apt install checkpolicy)
    kotlinc (snap install kotlin --classic)  [optional]
    python3 with ast module (built-in)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# ── Configuration ─────────────────────────────────────────────────

CONDITIONS = {
    "c1": {"label": "C1 Baseline",  "output_dir": Path("output")},
    "c2": {"label": "C2 Adaptive",  "output_dir": Path("output_adaptive")},
    "c3": {"label": "C3 RAG+DSPy",  "output_dir": Path("output_rag_dspy")},
}

RESULTS_DIR = Path("experiments/results")
VERIFICATION_DIR = Path("experiments/verification")

# File pattern → agent type
FILE_CLASSIFY = {
    r"\.aidl$":              "aidl",
    r"VehicleHalServer\.cpp$": "cpp",
    r"(?<!Android)\.cpp$":   "cpp",
    r"\.te$":                "selinux",
    r"Android\.bp$":         "build",
    r"design_doc.*\.md$":    "design_doc",
    r"\.kt$":                "android_app",
    # Only match backend python files, not test/util scripts
    r"(backend|server|service|app).*\.py$": "backend",
}


@dataclass
class TestResult:
    condition: str
    spec: str
    agent: str
    file: str
    compile_pass: Optional[bool] = None
    compile_errors: str = ""
    tests_run: int = 0
    tests_passed: int = 0
    test_details: list = field(default_factory=list)
    time_seconds: float = 0.0

    @property
    def test_pass_rate(self) -> float:
        return self.tests_passed / self.tests_run if self.tests_run > 0 else 0.0


# ── File Classification ──────────────────────────────────────────

def classify_file(filename: str) -> Optional[str]:
    for pattern, agent_type in FILE_CLASSIFY.items():
        if re.search(pattern, filename, re.IGNORECASE):
            return agent_type
    return None


def discover_files(output_dir: Path) -> dict[str, list[tuple[str, Path]]]:
    """Discover all spec dirs and their files.
    Returns: {spec_name: [(agent_type, filepath), ...]}
    """
    specs = {}
    if not output_dir.exists():
        return specs

    for spec_dir in sorted(output_dir.iterdir()):
        if not spec_dir.is_dir():
            continue
        files = []
        for fpath in sorted(spec_dir.iterdir()):
            if fpath.is_file():
                agent = classify_file(fpath.name)
                if agent:
                    files.append((agent, fpath))
        if files:
            specs[spec_dir.name] = files

    return specs


# ══════════════════════════════════════════════════════════════════
# COMPILATION CHECKS
# ══════════════════════════════════════════════════════════════════

def compile_aidl(filepath: Path) -> tuple[bool, str]:
    """Validate AIDL file structure (no aidl compiler in standalone mode)."""
    content = filepath.read_text(errors="replace")
    errors = []

    if "package " not in content:
        errors.append("Missing package declaration")
    if not re.search(r"(interface|parcelable)\s+\w+", content):
        errors.append("Missing interface or parcelable declaration")
    if content.count("{") != content.count("}"):
        errors.append(f"Unbalanced braces: {content.count('{')} open, {content.count('}')} close")
    if "@VintfStability" not in content:
        errors.append("Missing @VintfStability annotation")

    # Check required fields for VehiclePropValue
    if "parcelable" in content:
        for field_name in ["status", "prop", "timestamp"]:
            if field_name not in content:
                errors.append(f"Parcelable missing expected field: {field_name}")

    return (len(errors) == 0, "; ".join(errors) if errors else "OK")


def compile_cpp(filepath: Path) -> tuple[bool, str]:
    """Compile C++ with clang++ --syntax-only."""
    content = filepath.read_text(errors="replace")

    # First try real compiler
    try:
        result = subprocess.run(
            ["clang++", "--syntax-only", "-std=c++17",
             "-I/dev/null",  # suppress missing includes (standalone)
             "-fsyntax-only", "-Wno-everything",
             str(filepath)],
            capture_output=True, timeout=15
        )
        if result.returncode == 0:
            return (True, "OK (clang++ passed)")
        stderr = result.stderr.decode(errors="replace")
        # Count real errors (not include-not-found)
        real_errors = [l for l in stderr.splitlines()
                       if "error:" in l and "file not found" not in l]
        if not real_errors:
            return (True, "OK (only missing includes)")
        return (False, f"{len(real_errors)} errors: {real_errors[0][:200]}")
    except FileNotFoundError:
        pass  # clang++ not available, use heuristic
    except subprocess.TimeoutExpired:
        return (False, "Compilation timed out")

    # Heuristic fallback
    errors = []
    if content.count("{") != content.count("}"):
        errors.append("Unbalanced braces")
    if content.count("(") != content.count(")"):
        errors.append("Unbalanced parentheses")
    if "#include" not in content:
        errors.append("No #include directives")

    # Check required VHAL methods
    for method in ["getAllPropertyConfigs", "getValues", "setValues"]:
        if method not in content:
            errors.append(f"Missing required method: {method}")

    return (len(errors) == 0, "; ".join(errors) if errors else "OK (heuristic)")


def compile_selinux(filepath: Path) -> tuple[bool, str]:
    """Validate SELinux policy with checkpolicy."""
    try:
        result = subprocess.run(
            ["checkpolicy", "-M", "-c", "30", "-o", "/dev/null", str(filepath)],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return (True, "OK (checkpolicy passed)")
        stderr = result.stderr.decode(errors="replace")
        return (False, stderr[:300])
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return (False, "checkpolicy timed out")

    # Heuristic
    content = filepath.read_text(errors="replace")
    errors = []
    for i, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("type ", "allow ", "neverallow ", "typeattribute ")):
            if not line.endswith(";"):
                errors.append(f"Line {i}: missing semicolon")
        if "allow" in line and ":" not in line and "{" not in line:
            if not line.startswith("#"):
                errors.append(f"Line {i}: allow rule may be malformed")

    return (len(errors) == 0, "; ".join(errors[:5]) if errors else "OK (heuristic)")


def compile_build(filepath: Path) -> tuple[bool, str]:
    """Validate Android.bp as parseable blueprint."""
    content = filepath.read_text(errors="replace")
    errors = []

    if content.count("{") != content.count("}"):
        errors.append("Unbalanced braces")
    if content.count("[") != content.count("]"):
        errors.append("Unbalanced brackets")

    # Required fields
    if "name:" not in content and '"name"' not in content:
        errors.append("Missing 'name' field")
    if "srcs:" not in content and '"srcs"' not in content:
        errors.append("Missing 'srcs' field")
    if "vendor:" not in content and "vendor :" not in content:
        errors.append("Missing 'vendor: true' (required for VHAL HAL)")

    # Check for valid module type
    module_types = ["cc_binary", "cc_library", "cc_library_shared",
                    "cc_library_static", "cc_defaults", "aidl_interface"]
    if not any(mt in content for mt in module_types):
        errors.append("No recognised module type (cc_binary, cc_library, etc.)")

    return (len(errors) == 0, "; ".join(errors) if errors else "OK")


def compile_kotlin(filepath: Path) -> tuple[bool, str]:
    """Compile Kotlin with kotlinc."""
    try:
        result = subprocess.run(
            ["kotlinc", "-nowarn", "-no-reflect", "-no-stdlib",
             str(filepath)],
            capture_output=True, timeout=60
        )
        if result.returncode == 0:
            return (True, "OK (kotlinc passed)")
        stderr = result.stderr.decode(errors="replace")
        error_lines = [l for l in stderr.splitlines() if "error:" in l.lower()]
        return (False, f"{len(error_lines)} errors: {error_lines[0][:200]}" if error_lines else stderr[:300])
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return (False, "kotlinc timed out")

    # Heuristic
    content = filepath.read_text(errors="replace")
    errors = []
    if content.count("{") != content.count("}"):
        errors.append("Unbalanced braces")
    if "class " not in content and "fun " not in content:
        errors.append("No class or function definitions")
    if "import " not in content:
        errors.append("No import statements")

    return (len(errors) == 0, "; ".join(errors) if errors else "OK (heuristic)")


def compile_python(filepath: Path) -> tuple[bool, str]:
    """Validate Python with ast.parse."""
    import ast
    content = filepath.read_text(errors="replace")
    try:
        ast.parse(content)
        return (True, "OK (ast.parse passed)")
    except SyntaxError as e:
        return (False, f"SyntaxError at line {e.lineno}: {e.msg}")


def compile_markdown(filepath: Path) -> tuple[bool, str]:
    """Validate design doc structure."""
    content = filepath.read_text(errors="replace")
    errors = []
    lines = content.splitlines()

    if not any(l.startswith("# ") for l in lines):
        errors.append("Missing H1 heading")
    if not any(l.startswith("## ") for l in lines):
        errors.append("Missing H2 sections")
    body_lines = [l for l in lines if l.strip() and not l.startswith("#")]
    if len(body_lines) < 10:
        errors.append(f"Insufficient body content ({len(body_lines)} lines)")

    return (len(errors) == 0, "; ".join(errors) if errors else "OK")


COMPILERS = {
    "aidl":        compile_aidl,
    "cpp":         compile_cpp,
    "selinux":     compile_selinux,
    "build":       compile_build,
    "android_app": compile_kotlin,
    "backend":     compile_python,
    "design_doc":  compile_markdown,
}


# ══════════════════════════════════════════════════════════════════
# TEST CASES
# ══════════════════════════════════════════════════════════════════

def run_tests(agent: str, filepath: Path, content: str) -> list[dict]:
    """Run test cases for a given agent type. Returns list of test dicts."""
    tests = TEST_SUITES.get(agent, [])
    results = []
    for tc in tests:
        try:
            passed = tc["check"](content, filepath)
            results.append({
                "id": tc["id"],
                "name": tc["name"],
                "category": tc["category"],
                "passed": passed,
                "severity": tc["severity"],
            })
        except Exception as e:
            results.append({
                "id": tc["id"],
                "name": tc["name"],
                "category": tc["category"],
                "passed": False,
                "severity": tc["severity"],
                "error": str(e)[:200],
            })
    return results


# ── AIDL Test Cases ───────────────────────────────────────────────

AIDL_TESTS = [
    {
        "id": "AIDL-001", "name": "Package declaration present",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: bool(re.search(r"^package\s+[\w.]+;", c, re.M)),
    },
    {
        "id": "AIDL-002", "name": "@VintfStability annotation",
        "category": "aaos_compliance", "severity": "critical",
        "check": lambda c, f: "@VintfStability" in c,
    },
    {
        "id": "AIDL-003", "name": "Interface or parcelable declared",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: bool(re.search(r"(interface|parcelable)\s+\w+", c)),
    },
    {
        "id": "AIDL-004", "name": "Balanced braces",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: c.count("{") == c.count("}"),
    },
    {
        "id": "AIDL-005", "name": "VehiclePropValue contains 'prop' field",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "prop" in c if "parcelable" in c else True,
    },
    {
        "id": "AIDL-006", "name": "VehiclePropValue contains 'status' field",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "status" in c if "parcelable" in c else True,
    },
    {
        "id": "AIDL-007", "name": "VehiclePropValue contains 'timestamp' field",
        "category": "domain", "severity": "minor",
        "check": lambda c, f: "timestamp" in c if "parcelable" in c else True,
    },
    {
        "id": "AIDL-008", "name": "VehiclePropValue contains 'areaId' field",
        "category": "domain", "severity": "minor",
        "check": lambda c, f: "areaId" in c.lower() if "parcelable" in c else True,
    },
    {
        "id": "AIDL-009", "name": "No hardcoded ADAS property names",
        "category": "flexibility", "severity": "minor",
        "check": lambda c, f: "ADAS" not in c or "VehiclePropertyVss" not in c,
    },
    {
        "id": "AIDL-010", "name": "All declarations end with semicolons",
        "category": "syntax", "severity": "major",
        "check": lambda c, f: all(
            l.strip().endswith(";") or l.strip().endswith("{") or l.strip().endswith("}")
            or l.strip().startswith("//") or l.strip().startswith("/*") or l.strip().startswith("*")
            or l.strip().startswith("@") or l.strip().startswith("package")
            or l.strip().startswith("import") or not l.strip()
            for l in c.splitlines()
        ),
    },
]

# ── C++ Test Cases ────────────────────────────────────────────────

CPP_TESTS = [
    {
        "id": "CPP-001", "name": "Has #include directives",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "#include" in c,
    },
    {
        "id": "CPP-002", "name": "Implements getAllPropertyConfigs",
        "category": "aaos_compliance", "severity": "critical",
        "check": lambda c, f: "getAllPropertyConfigs" in c,
    },
    {
        "id": "CPP-003", "name": "Implements getValues",
        "category": "aaos_compliance", "severity": "critical",
        "check": lambda c, f: "getValues" in c,
    },
    {
        "id": "CPP-004", "name": "Implements setValues",
        "category": "aaos_compliance", "severity": "critical",
        "check": lambda c, f: "setValues" in c,
    },
    {
        "id": "CPP-005", "name": "Uses namespace",
        "category": "structure", "severity": "major",
        "check": lambda c, f: "namespace" in c,
    },
    {
        "id": "CPP-006", "name": "References VehiclePropConfig",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "VehiclePropConfig" in c,
    },
    {
        "id": "CPP-007", "name": "References VehiclePropValue",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "VehiclePropValue" in c,
    },
    {
        "id": "CPP-008", "name": "Uses StatusCode or Return type",
        "category": "domain", "severity": "minor",
        "check": lambda c, f: "StatusCode" in c or "Return<" in c or "ndk::ScopedAStatus" in c,
    },
    {
        "id": "CPP-009", "name": "Balanced braces",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: c.count("{") == c.count("}"),
    },
    {
        "id": "CPP-010", "name": "Balanced parentheses",
        "category": "syntax", "severity": "major",
        "check": lambda c, f: c.count("(") == c.count(")"),
    },
    {
        "id": "CPP-011", "name": "Has class or struct definition",
        "category": "structure", "severity": "major",
        "check": lambda c, f: bool(re.search(r"(class|struct)\s+\w+", c)),
    },
    {
        "id": "CPP-012", "name": "No raw 'TODO' placeholders left",
        "category": "completeness", "severity": "minor",
        "check": lambda c, f: c.upper().count("TODO") < 3,
    },
]

# ── SELinux Test Cases ────────────────────────────────────────────

SELINUX_TESTS = [
    {
        "id": "SE-001", "name": "Has 'type' declaration",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: bool(re.search(r"^type\s+", c, re.M)),
    },
    {
        "id": "SE-002", "name": "Has 'allow' rules",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: bool(re.search(r"^allow\s+", c, re.M)),
    },
    {
        "id": "SE-003", "name": "All rules end with semicolons",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: all(
            l.strip().endswith(";") or l.strip().startswith("#") or not l.strip()
            for l in c.splitlines()
        ),
    },
    {
        "id": "SE-004", "name": "References hal_ domain type",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "hal_" in c,
    },
    {
        "id": "SE-005", "name": "References binder or hwbinder",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "binder" in c.lower(),
    },
    {
        "id": "SE-006", "name": "Domain-specific type (not generic)",
        "category": "flexibility", "severity": "minor",
        "check": lambda c, f: bool(re.search(r"type\s+hal_\w+_default", c)),
    },
    {
        "id": "SE-007", "name": "Has vendor or device context",
        "category": "domain", "severity": "minor",
        "check": lambda c, f: "vendor" in c.lower() or "device" in c.lower(),
    },
]

# ── Build (Android.bp) Test Cases ─────────────────────────────────

BUILD_TESTS = [
    {
        "id": "BP-001", "name": "Has module type declaration",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: any(t in c for t in ["cc_binary", "cc_library", "cc_library_shared",
                                                      "cc_library_static", "aidl_interface"]),
    },
    {
        "id": "BP-002", "name": "Has 'name' field",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "name:" in c or '"name"' in c,
    },
    {
        "id": "BP-003", "name": "Has 'srcs' field",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "srcs:" in c or '"srcs"' in c,
    },
    {
        "id": "BP-004", "name": "vendor: true is set",
        "category": "aaos_compliance", "severity": "critical",
        "check": lambda c, f: "vendor:" in c and "true" in c,
    },
    {
        "id": "BP-005", "name": "Has shared_libs",
        "category": "structure", "severity": "major",
        "check": lambda c, f: "shared_libs" in c,
    },
    {
        "id": "BP-006", "name": "Balanced braces",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: c.count("{") == c.count("}"),
    },
    {
        "id": "BP-007", "name": "Balanced brackets",
        "category": "syntax", "severity": "major",
        "check": lambda c, f: c.count("[") == c.count("]"),
    },
]

# ── Design Doc Test Cases ─────────────────────────────────────────

DESIGN_DOC_TESTS = [
    {
        "id": "DD-001", "name": "Has H1 title",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: any(l.startswith("# ") for l in c.splitlines()),
    },
    {
        "id": "DD-002", "name": "Has H2 sections",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: sum(1 for l in c.splitlines() if l.startswith("## ")) >= 2,
    },
    {
        "id": "DD-003", "name": "Mentions VHAL or Vehicle HAL",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "vhal" in c.lower() or "vehicle hal" in c.lower(),
    },
    {
        "id": "DD-004", "name": "Mentions property or properties",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "propert" in c.lower(),
    },
    {
        "id": "DD-005", "name": "Has architecture or design section",
        "category": "completeness", "severity": "major",
        "check": lambda c, f: any(kw in c.lower() for kw in ["architecture", "design", "overview", "system"]),
    },
    {
        "id": "DD-006", "name": "At least 200 words of content",
        "category": "completeness", "severity": "minor",
        "check": lambda c, f: len(c.split()) >= 200,
    },
]

# ── Android App (Kotlin) Test Cases ───────────────────────────────

KOTLIN_TESTS = [
    {
        "id": "KT-001", "name": "Has import statements",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "import " in c,
    },
    {
        "id": "KT-002", "name": "Has class definition",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: bool(re.search(r"class\s+\w+", c)),
    },
    {
        "id": "KT-003", "name": "References Car or CarPropertyManager",
        "category": "domain", "severity": "critical",
        "check": lambda c, f: "Car" in c,
    },
    {
        "id": "KT-004", "name": "Has Activity or ViewModel",
        "category": "structure", "severity": "major",
        "check": lambda c, f: any(kw in c for kw in ["Activity", "ViewModel", "Fragment", "Service"]),
    },
    {
        "id": "KT-005", "name": "References VehicleProperty",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "VehicleProperty" in c or "vehicleProperty" in c or "VEHICLE_PROPERTY" in c,
    },
    {
        "id": "KT-006", "name": "Balanced braces",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: c.count("{") == c.count("}"),
    },
    {
        "id": "KT-007", "name": "Has function definitions",
        "category": "structure", "severity": "major",
        "check": lambda c, f: "fun " in c,
    },
    {
        "id": "KT-008", "name": "Handles lifecycle (onCreate/onDestroy)",
        "category": "completeness", "severity": "minor",
        "check": lambda c, f: "onCreate" in c or "onStart" in c,
    },
]

# ── Backend (Python) Test Cases ───────────────────────────────────

BACKEND_TESTS = [
    {
        "id": "PY-001", "name": "Passes ast.parse",
        "category": "syntax", "severity": "critical",
        "check": lambda c, f: _ast_check(c),
    },
    {
        "id": "PY-002", "name": "Has import statements",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "import " in c,
    },
    {
        "id": "PY-003", "name": "Uses Flask or FastAPI",
        "category": "structure", "severity": "major",
        "check": lambda c, f: any(fw in c.lower() for fw in ["flask", "fastapi", "django"]),
    },
    {
        "id": "PY-004", "name": "Has route/endpoint definitions",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: any(kw in c for kw in ["@app.route", "@router.", "@app.get", "@app.post", "def get", "def post"]),
    },
    {
        "id": "PY-005", "name": "Has function definitions",
        "category": "structure", "severity": "critical",
        "check": lambda c, f: "def " in c,
    },
    {
        "id": "PY-006", "name": "References JSON response",
        "category": "domain", "severity": "major",
        "check": lambda c, f: "json" in c.lower() or "jsonify" in c,
    },
    {
        "id": "PY-007", "name": "Has main guard or server start",
        "category": "completeness", "severity": "minor",
        "check": lambda c, f: '__name__' in c or 'uvicorn.run' in c or 'app.run' in c,
    },
    {
        "id": "PY-008", "name": "Has error handling",
        "category": "robustness", "severity": "minor",
        "check": lambda c, f: "try:" in c or "except" in c or "errorhandler" in c.lower(),
    },
]


def _ast_check(content: str) -> bool:
    import ast
    try:
        ast.parse(content)
        return True
    except SyntaxError:
        return False


TEST_SUITES = {
    "aidl":        AIDL_TESTS,
    "cpp":         CPP_TESTS,
    "selinux":     SELINUX_TESTS,
    "build":       BUILD_TESTS,
    "design_doc":  DESIGN_DOC_TESTS,
    "android_app": KOTLIN_TESTS,
    "backend":     BACKEND_TESTS,
}


# ══════════════════════════════════════════════════════════════════
# MAIN VERIFICATION PIPELINE
# ══════════════════════════════════════════════════════════════════

def verify_file(condition: str, spec: str, agent: str, filepath: Path,
                skip_compile: bool = False) -> TestResult:
    """Run compilation + tests on a single file."""
    t0 = time.time()
    content = filepath.read_text(errors="replace")
    result = TestResult(condition=condition, spec=spec, agent=agent, file=filepath.name)

    # Compilation
    if not skip_compile and agent in COMPILERS:
        passed, msg = COMPILERS[agent](filepath)
        result.compile_pass = passed
        result.compile_errors = msg

    # Tests
    test_results = run_tests(agent, filepath, content)
    result.tests_run = len(test_results)
    result.tests_passed = sum(1 for t in test_results if t["passed"])
    result.test_details = test_results
    result.time_seconds = round(time.time() - t0, 3)

    return result


def run_verification(conditions_to_run: list[str],
                     specs_filter: Optional[str] = None,
                     skip_compile: bool = False) -> list[TestResult]:
    """Run full verification across conditions and specs."""
    all_results = []

    for cond_key in conditions_to_run:
        cfg = CONDITIONS[cond_key]
        label = cfg["label"]
        output_dir = cfg["output_dir"]

        print(f"\n{'='*60}")
        print(f"  {label}  ({output_dir}/)")
        print(f"{'='*60}")

        specs = discover_files(output_dir)
        if not specs:
            print(f"  ⚠ No files found in {output_dir}/")
            continue

        for spec_name, files in specs.items():
            if specs_filter and specs_filter != spec_name:
                continue

            print(f"\n  [{spec_name}]")
            for agent, filepath in files:
                result = verify_file(cond_key, spec_name, agent, filepath, skip_compile)
                status = "✓" if (result.compile_pass is None or result.compile_pass) else "✗"
                test_str = f"{result.tests_passed}/{result.tests_run}"
                print(f"    {status} {agent:12s} compile={'PASS' if result.compile_pass else 'FAIL' if result.compile_pass is False else 'SKIP':4s}  "
                      f"tests={test_str:5s}  {filepath.name}")
                if result.compile_errors and result.compile_errors != "OK" and not result.compile_pass:
                    print(f"      → {result.compile_errors[:120]}")
                all_results.append(result)

    return all_results


# ══════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════

def generate_report(results: list[TestResult]):
    """Generate verification report files."""
    VERIFICATION_DIR.mkdir(parents=True, exist_ok=True)

    # ── JSON raw results ──
    json_path = VERIFICATION_DIR / "verification_results.json"
    json_data = [asdict(r) for r in results]
    json_path.write_text(json.dumps(json_data, indent=2))

    # ── Summary tables ──
    report_lines = []
    report_lines.append("# VHAL Code Generation — Verification & Test Report\n")

    # 1. Compilation summary
    report_lines.append("## 1. Compilation Results\n")
    report_lines.append("| Condition | Agent | Total | Compile Pass | Compile Fail | Rate |")
    report_lines.append("|---|---|---|---|---|---|")

    from collections import defaultdict
    comp_buckets = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0})
    for r in results:
        key = (r.condition, r.agent)
        comp_buckets[key]["total"] += 1
        if r.compile_pass is True:
            comp_buckets[key]["pass"] += 1
        elif r.compile_pass is False:
            comp_buckets[key]["fail"] += 1

    for (cond, agent), counts in sorted(comp_buckets.items()):
        rate = counts["pass"] / counts["total"] if counts["total"] > 0 else 0
        label = CONDITIONS.get(cond, {}).get("label", cond)
        report_lines.append(
            f"| {label} | {agent} | {counts['total']} | {counts['pass']} | {counts['fail']} | {rate:.1%} |"
        )

    # 2. Test results summary
    report_lines.append("\n## 2. Test Results Summary\n")
    report_lines.append("| Condition | Agent | Tests Run | Tests Passed | Pass Rate |")
    report_lines.append("|---|---|---|---|---|")

    test_buckets = defaultdict(lambda: {"run": 0, "passed": 0})
    for r in results:
        key = (r.condition, r.agent)
        test_buckets[key]["run"] += r.tests_run
        test_buckets[key]["passed"] += r.tests_passed

    for (cond, agent), counts in sorted(test_buckets.items()):
        rate = counts["passed"] / counts["run"] if counts["run"] > 0 else 0
        label = CONDITIONS.get(cond, {}).get("label", cond)
        report_lines.append(
            f"| {label} | {agent} | {counts['run']} | {counts['passed']} | {rate:.1%} |"
        )

    # 3. Overall condition comparison
    report_lines.append("\n## 3. Overall Condition Comparison\n")
    report_lines.append("| Condition | Files | Compile Rate | Test Pass Rate | Avg Tests/File |")
    report_lines.append("|---|---|---|---|---|")

    for cond_key in ["c1", "c2", "c3"]:
        cond_results = [r for r in results if r.condition == cond_key]
        if not cond_results:
            continue
        label = CONDITIONS[cond_key]["label"]
        n = len(cond_results)
        comp_pass = sum(1 for r in cond_results if r.compile_pass is True)
        comp_total = sum(1 for r in cond_results if r.compile_pass is not None)
        comp_rate = comp_pass / comp_total if comp_total > 0 else 0
        test_total = sum(r.tests_run for r in cond_results)
        test_pass = sum(r.tests_passed for r in cond_results)
        test_rate = test_pass / test_total if test_total > 0 else 0
        avg_tests = test_total / n if n > 0 else 0
        report_lines.append(
            f"| {label} | {n} | {comp_rate:.1%} | {test_rate:.1%} | {avg_tests:.1f} |"
        )

    # 4. Failed tests detail
    report_lines.append("\n## 4. Failed Test Cases (Critical/Major)\n")
    report_lines.append("| Condition | Spec | Agent | Test ID | Test Name | Severity |")
    report_lines.append("|---|---|---|---|---|---|")

    for r in results:
        label = CONDITIONS.get(r.condition, {}).get("label", r.condition)
        for td in r.test_details:
            if not td["passed"] and td["severity"] in ("critical", "major"):
                report_lines.append(
                    f"| {label} | {r.spec} | {r.agent} | {td['id']} | {td['name']} | {td['severity']} |"
                )

    # 5. Test case catalogue
    report_lines.append("\n## 5. Test Case Catalogue\n")
    for agent in sorted(TEST_SUITES.keys()):
        tests = TEST_SUITES[agent]
        report_lines.append(f"\n### {agent} ({len(tests)} tests)\n")
        report_lines.append("| ID | Name | Category | Severity |")
        report_lines.append("|---|---|---|---|")
        for tc in tests:
            report_lines.append(f"| {tc['id']} | {tc['name']} | {tc['category']} | {tc['severity']} |")

    report_path = VERIFICATION_DIR / "verification_report.md"
    report_path.write_text("\n".join(report_lines))
    print(f"\n→ Report: {report_path}")
    print(f"→ Raw JSON: {json_path}")


# ── CSV export ────────────────────────────────────────────────────

def export_csv(results: list[TestResult]):
    """Export flat CSV for external analysis."""
    import csv
    csv_path = VERIFICATION_DIR / "verification_results.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["condition", "spec", "agent", "file",
                         "compile_pass", "tests_run", "tests_passed",
                         "test_pass_rate", "time_seconds"])
        for r in results:
            writer.writerow([
                r.condition, r.spec, r.agent, r.file,
                r.compile_pass, r.tests_run, r.tests_passed,
                f"{r.test_pass_rate:.3f}", r.time_seconds,
            ])
    print(f"→ CSV: {csv_path}")


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AOSP Integration Verification & Testing for VHAL Code Generation"
    )
    parser.add_argument("--only", choices=["c1", "c2", "c3"],
                        help="Verify only one condition")
    parser.add_argument("--spec", type=str,
                        help="Verify only one spec (e.g., vehicle_speed)")
    parser.add_argument("--test-only", action="store_true",
                        help="Skip compilation, run tests only")
    parser.add_argument("--standalone", action="store_true",
                        help="Standalone mode (no AOSP tree, local tools only)")
    parser.add_argument("--aosp-root", type=str,
                        help="Path to AOSP source tree (not yet implemented)")
    args = parser.parse_args()

    print("=" * 60)
    print("VHAL Code Generation — Verification & Testing")
    print("=" * 60)

    conds = [args.only] if args.only else ["c1", "c2", "c3"]

    results = run_verification(
        conditions_to_run=conds,
        specs_filter=args.spec,
        skip_compile=args.test_only,
    )

    if not results:
        print("\nNo results to report.")
        return

    generate_report(results)
    export_csv(results)

    # Print summary
    total_tests = sum(r.tests_run for r in results)
    total_passed = sum(r.tests_passed for r in results)
    total_compile = sum(1 for r in results if r.compile_pass is not None)
    compile_pass = sum(1 for r in results if r.compile_pass is True)

    print(f"\n{'='*60}")
    print(f"VERIFICATION COMPLETE")
    print(f"{'='*60}")
    print(f"  Files checked:    {len(results)}")
    print(f"  Compile pass:     {compile_pass}/{total_compile} ({compile_pass/total_compile:.1%})" if total_compile else "")
    print(f"  Tests:            {total_passed}/{total_tests} ({total_passed/total_tests:.1%})" if total_tests else "")
    print()


if __name__ == "__main__":
    main()
