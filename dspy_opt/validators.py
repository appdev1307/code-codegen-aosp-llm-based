"""
dspy_opt/validators.py
═══════════════════════════════════════════════════════════════════════════════
Syntax and parse validators for every HAL output type.

Cross-compilation reality:
  AIDL, C++ VHAL, Android.bp, and Kotlin all target Android/AOSP.
  You cannot run the AOSP build system on a host Linux machine without
  a full AOSP checkout (~200 GB). We use the highest-fidelity tool
  actually available per output type:

  ┌──────────────────┬──────────────────────────────┬─────────────────────────┐
  │ Output type      │ Validator used               │ What it catches         │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ AIDL             │ Python AIDL grammar parser   │ Package, interface,     │
  │                  │ (AIDL grammar is simple)     │ method syntax, types    │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ C++ VHAL         │ clang++ --syntax-only        │ All C++ syntax errors;  │
  │                  │ (stub AOSP headers injected) │ no linker needed        │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ SELinux .te      │ checkpolicy (native Linux)   │ Full policy compile;    │
  │                  │                              │ type and rule validity  │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ Android.bp       │ Python JSON5 parser          │ Block structure, fields,│
  │                  │ (bp is JSON5 subset)         │ brace balance           │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ VINTF XML        │ xml.etree + schema rules     │ XML well-formedness +   │
  │ Layout XML       │ (Python stdlib)              │ required elements       │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ PlantUML         │ plantuml.jar (optional)      │ Full diagram parse;     │
  │                  │ + regex fallback             │ falls back gracefully   │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ Kotlin Fragment  │ kotlinc (syntax only)        │ Syntax errors; Android  │
  │                  │ + regex fallback             │ API refs filtered       │
  ├──────────────────┼──────────────────────────────┼─────────────────────────┤
  │ FastAPI / Python │ ast.parse (Python stdlib)    │ Full Python AST parse;  │
  │ Models / Simulator│                             │ identical to CPython    │
  └──────────────────┴──────────────────────────────┴─────────────────────────┘

Each validator returns a ValidatorResult(ok, score, errors, tool).
  ok     : bool  — did validation pass?
  score  : float — partial credit 0.0-1.0 (fewer errors → higher partial score)
  errors : list  — human-readable error lines (used in repair prompts too)
  tool   : str   — which validator ran (recorded in thesis metrics)

Install optional tools on Ubuntu/Debian:
  sudo apt-get install -y clang checkpolicy default-jre
  # plantuml: https://plantuml.com/download → plantuml.jar somewhere in PATH
  # kotlinc:  https://kotlinlang.org/docs/command-line.html
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidatorResult:
    ok:     bool
    score:  float                        # 0.0 – 1.0
    errors: list[str] = field(default_factory=list)
    tool:   str       = "unknown"
    detail: str       = ""

    def __bool__(self) -> bool:
        return self.ok


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess helper
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_CACHE: dict[str, Optional[str]] = {}

def _tool(name: str) -> Optional[str]:
    if name not in _TOOL_CACHE:
        _TOOL_CACHE[name] = shutil.which(name)
    return _TOOL_CACHE[name]


def _run(cmd: list[str], input_text: Optional[str] = None,
         timeout: int = 30) -> tuple[int, str, str]:
    """Run subprocess; never raises. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timeout after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def _count_errors(stderr: str) -> int:
    return len([l for l in stderr.splitlines() if "error:" in l.lower()])


def _partial(n_errors: int, base: float = 0.85, per: float = 0.12) -> float:
    return round(max(0.15, base - n_errors * per), 3)


# ═════════════════════════════════════════════════════════════════════════════
# A1 — AIDL  ·  Python grammar parser
# ═════════════════════════════════════════════════════════════════════════════

_AIDL_PRIMITIVE_TYPES = {
    "void", "boolean", "byte", "char", "short", "int", "long",
    "float", "double", "String", "byte[]", "int[]", "long[]",
    "IBinder", "ParcelableHolder", "FileDescriptor",
}

def validate_aidl(code: str) -> ValidatorResult:
    """
    Validate an AIDL file using a Python AIDL grammar parser.

    Why not the real `aidl` binary?
    The AOSP `aidl` tool requires a full source tree for imports.
    Even a valid standalone file fails because 'android.hardware.*'
    packages are not resolvable without -I pointing into AOSP.
    Our parser validates everything we can actually control.
    """
    tool = "aidl-python-parser"
    if not code.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty output"], tool=tool)

    errors, score = [], 0.0

    # 1. Package declaration
    pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", code, re.MULTILINE)
    if pkg:
        score += 0.20
        if not re.match(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$", pkg.group(1)):
            errors.append(f"Package '{pkg.group(1)}' should be lowercase.dot.separated")
    else:
        errors.append("Missing package declaration — e.g. 'package vendor.vss.adas;'")

    # 2. Interface block with optional annotations
    iface = re.search(r"(?:@\w+\s*)*interface\s+(\w+)\s*\{", code)
    if iface:
        score += 0.25
        if not iface.group(1).startswith("I"):
            errors.append(f"Interface '{iface.group(1)}' should start with 'I' per AIDL convention")
    else:
        errors.append("No interface block found — expected: 'interface IName { ... }'")

    # 3. Brace balance
    if code.count("{") == code.count("}"):
        score += 0.10
    else:
        errors.append(f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close")

    # 4. Method signatures  (type name(params);)
    methods = re.findall(
        r"(?:oneway\s+)?(\w[\w<>\[\], ]*)\s+(\w+)\s*\(([^)]*)\)\s*;", code
    )
    if methods:
        score += 0.20
        bad_types = []
        for ret, name, _ in methods:
            base = ret.strip().split("<")[0].strip()
            if base not in _AIDL_PRIMITIVE_TYPES and not base[0].isupper():
                bad_types.append(f"'{base}' in '{name}'")
        if not bad_types:
            score += 0.15
        else:
            errors.extend([f"Suspicious AIDL type {t}" for t in bad_types[:3]])
    else:
        errors.append("No method signatures found — expected: 'ReturnType method(args);'")

    # 5. @VintfStability annotation
    if "@VintfStability" in code:
        score += 0.10

    ok = (score >= 0.70) and (len(errors) == 0)
    return ValidatorResult(
        ok=ok,
        score=round(score, 3) if ok else round(score * 0.65, 3),
        errors=errors, tool=tool,
        detail=f"{len(methods)} methods parsed",
    )


# ═════════════════════════════════════════════════════════════════════════════
# A2 — C++ VHAL  ·  clang++ --syntax-only  (stub headers injected)
# ═════════════════════════════════════════════════════════════════════════════

# Minimal AOSP VHAL type stubs — enough for clang to check C++ syntax
# without a real AOSP checkout or NDK sysroot.
_CPP_VHAL_STUBS = """\
// ── Stubs injected for host-side clang --syntax-only check ──────────────────
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <memory>
#include <functional>
#include <optional>

namespace android::hardware::automotive::vehicle {
    struct VehiclePropValue { int32_t prop = 0; int32_t areaId = 0; };
    struct VehiclePropConfig { int32_t prop = 0; };
    struct GetValueRequest  { VehiclePropValue value; };
    struct SetValueRequest  { VehiclePropValue value; };
    struct StatusCode       { static constexpr int OK = 0; };
    class  IVehicleHardware {
    public:
        virtual ~IVehicleHardware() = default;
        virtual std::vector<VehiclePropConfig> getAllPropertyConfigs() const = 0;
    };
}
using namespace android::hardware::automotive::vehicle;
// ────────────────────────────────────────────────────────────────────────────
"""

def validate_cpp(code: str) -> ValidatorResult:
    """
    Validate C++ VHAL code using clang++ --syntax-only.

    Injects minimal AOSP VHAL stubs so clang can check syntax without
    a full AOSP checkout.  --syntax-only skips linking entirely so no
    Android libraries or NDK sysroot are needed.

    What this catches:  missing semicolons, bad declarations, type
      mismatches, incorrect template usage, malformed class definitions.
    What it does NOT catch:  missing VHAL method implementations,
      wrong property enum values, link-time errors.
    """
    tool = "clang++ --syntax-only"
    if not code.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty output"], tool=tool)

    clang = _tool("clang++") or _tool("clang")
    if not clang:
        return _cpp_regex_fallback(code)

    combined = _CPP_VHAL_STUBS + "\n" + code

    fd, tmp = tempfile.mkstemp(suffix=".cpp")
    try:
        os.write(fd, combined.encode())
        os.close(fd)
        rc, _, stderr = _run([
            clang, "--syntax-only", "-x", "c++", "-std=c++17",
            "-Wno-unknown-pragmas", "-Wno-unused-variable",
            "-Wno-unused-function", "-Wno-error",
            tmp,
        ])
    finally:
        os.unlink(tmp)

    # Filter error lines that are from the stub prelude (line 1-25)
    real_errors = []
    for line in stderr.splitlines():
        if "error:" not in line.lower():
            continue
        m = re.search(r":(\d+):", line)
        if m and int(m.group(1)) <= 25:
            continue    # skip stub lines
        if "Stubs injected" in line:
            continue
        real_errors.append(line)

    if not real_errors:
        return ValidatorResult(ok=True, score=1.0, tool=tool,
                               detail="Syntax OK (AOSP stubs injected)")
    partial = _partial(_count_errors("\n".join(real_errors)))
    return ValidatorResult(ok=False, score=partial,
                           errors=real_errors[:5], tool=tool,
                           detail=f"{len(real_errors)} syntax error(s)")


def _cpp_regex_fallback(code: str) -> ValidatorResult:
    tool, errors, score = "cpp-regex-fallback", [], 0.0
    if "#include"      in code: score += 0.15
    if "namespace"     in code: score += 0.15
    if "class "        in code: score += 0.20
    if code.count("{") == code.count("}"): score += 0.15
    else: errors.append("Unbalanced braces")
    if any(m in code for m in ["getAllPropertyConfigs","getValues","setValues"]):
        score += 0.25
    else:
        errors.append("Missing key VHAL methods (getAllPropertyConfigs/getValues/setValues)")
    if any(t in code for t in ["int32_t","VehiclePropValue","float","bool"]):
        score += 0.10
    return ValidatorResult(ok=score>=0.7 and not errors, score=round(score,3),
                           errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# A3 — SELinux .te  ·  checkpolicy  (native Linux — full compile)
# ═════════════════════════════════════════════════════════════════════════════

# Minimal class/common declarations so a standalone .te snippet compiles
_SELINUX_PRELUDE = """\
class binder  { call set_context_mgr transfer impersonate }
class file    { read write open getattr }
class dir     { read open search }
class service_manager { add find list }
class hwservice_manager { add find list }
class property_service { set }
common file_class_set { ioctl read write create getattr setattr lock relabelfrom relabelto append unlink link rename execute }
class file inherits file_class_set { execute_no_trans entrypoint open }
"""

def validate_selinux(policy: str) -> ValidatorResult:
    """
    Validate a SELinux .te policy file using checkpolicy.

    checkpolicy is a native Linux tool (policycoreutils) that compiles
    SELinux policy. Android and Linux use the same SELinux policy
    language — no cross-compilation needed.
    """
    tool = "checkpolicy"
    if not policy.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty policy"], tool=tool)

    if not _tool("checkpolicy"):
        return _selinux_regex_fallback(policy)

    combined = _SELINUX_PRELUDE + "\n" + policy

    fd, tmp = tempfile.mkstemp(suffix=".te")
    try:
        os.write(fd, combined.encode())
        os.close(fd)
        rc, stdout, stderr = _run(["checkpolicy", "-M", "-C", "-o", "/dev/null", tmp])
    finally:
        os.unlink(tmp)

    output = stdout + stderr
    if rc == 0 and "error" not in output.lower():
        return ValidatorResult(ok=True, score=1.0, tool=tool)

    error_lines = [l for l in output.splitlines()
                   if "error" in l.lower() or "undefined" in l.lower()]
    partial = _partial(len(error_lines))
    return ValidatorResult(ok=False, score=partial,
                           errors=error_lines[:5], tool=tool)


def _selinux_regex_fallback(policy: str) -> ValidatorResult:
    tool, errors, score = "selinux-regex-fallback", [], 0.0
    if "type "   in policy: score += 0.25
    if "allow "  in policy: score += 0.30
    else: errors.append("No 'allow' rules found")
    if any(k in policy for k in ["hal_vehicle","vhal","hal_attribute"]): score += 0.25
    else: errors.append("No VHAL-specific type declarations")
    if any(k in policy for k in ["binder_call","hwservice_use","add_hwservice"]): score += 0.20
    return ValidatorResult(ok=score>=0.7 and not errors, score=round(score,3),
                           errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# A4 — Android.bp  ·  Python JSON5 parser
# ═════════════════════════════════════════════════════════════════════════════

def validate_android_bp(bp: str) -> ValidatorResult:
    """
    Validate an Android.bp build file using a Python parser.

    Android.bp is JSON with Go-style // comments and unquoted keys.
    Soong (the AOSP build system that actually parses .bp files) is
    not available on a host machine without a full AOSP build.
    We validate structure, required blocks, and required fields.
    """
    tool = "android-bp-python-parser"
    if not bp.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty build file"], tool=tool)

    errors, score = [], 0.0

    # 1. Brace/bracket balance
    if bp.count("{") == bp.count("}"):   score += 0.20
    else: errors.append(f"Unbalanced braces: {bp.count('{')} open, {bp.count('}')} close")
    if bp.count("[") == bp.count("]"):   score += 0.05
    else: errors.append("Unbalanced square brackets in lists")

    # 2. Required block types
    block_types = set(re.findall(r"^\s*(\w+)\s*\{", bp, re.MULTILINE))
    required    = {"aidl_interface","cc_binary","cc_library_shared","cc_library"}
    found       = block_types & required
    if found:    score += 0.25
    else:        errors.append(f"No required block type found. Expected one of: {', '.join(sorted(required))}")

    # 3. Required fields
    if "name:" in bp:    score += 0.15
    else:                errors.append("Missing 'name:' field")

    if "srcs:" in bp:    score += 0.10
    else:                errors.append("Missing 'srcs:' field")

    if re.search(r"vendor:\s*true", bp): score += 0.15
    else:                errors.append("Missing 'vendor: true' — HAL modules must be on vendor partition")

    # 4. No unclosed strings
    if not re.findall(r'"[^"\n]*$', bp, re.MULTILINE):
        score += 0.10
    else:
        errors.append("Unclosed string literal detected")

    ok = score >= 0.70 and len(errors) == 0
    return ValidatorResult(
        ok=ok,
        score=round(score, 3) if ok else round(score * 0.65, 3),
        errors=errors, tool=tool,
        detail=f"blocks={', '.join(sorted(found)) or 'none'}",
    )


# ═════════════════════════════════════════════════════════════════════════════
# A5 — VINTF XML  ·  xml.etree + schema rules
# ═════════════════════════════════════════════════════════════════════════════

def validate_vintf_xml(content: str) -> ValidatorResult:
    """
    Validate a VINTF manifest XML + init.rc block using xml.etree.
    xml.etree is Python stdlib — always available, catches all XML
    well-formedness errors identically to a real XML parser.
    """
    tool = "xml.etree + vintf-schema"
    parts = content.split("# --- init.rc ---")
    xml_part = parts[0].strip()
    rc_part  = parts[1].strip() if len(parts) > 1 else ""

    if not xml_part:
        return ValidatorResult(ok=False, score=0.0, errors=["No XML content"], tool=tool)

    errors, score = [], 0.0

    # XML parse
    try:
        root = ET.fromstring(xml_part)
        score += 0.35
    except ET.ParseError as e:
        return ValidatorResult(ok=False, score=0.1,
                               errors=[f"XML parse error: {e}"], tool=tool)

    # <hal> element
    hal = root.find(".//hal") or root.find("hal")
    if hal is not None:         score += 0.20
    else:                       errors.append("Missing <hal> element")

    # <name> inside <hal>
    name_el = hal.find("name") if hal is not None else None
    if name_el is not None and name_el.text:
        score += 0.15
    else:
        errors.append("Missing <name> inside <hal>")

    # <transport>
    transport = root.find(".//transport")
    if transport is not None:
        score += 0.10
        if transport.text not in ("hwbinder","passthrough","binder"):
            errors.append(f"Unknown transport: '{transport.text}'")
    else:
        errors.append("Missing <transport> element")

    # init.rc block
    if rc_part and "service " in rc_part and ("class hal" in rc_part or "user " in rc_part):
        score += 0.20
    else:
        errors.append("Missing or incomplete init.rc section after '# --- init.rc ---'")

    score = round(min(score, 1.0), 3)
    ok    = score >= 0.75 and not any("parse error" in e.lower() for e in errors)
    return ValidatorResult(ok=ok, score=score, errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# B1 — PlantUML  ·  plantuml.jar or regex fallback
# ═════════════════════════════════════════════════════════════════════════════

def _find_plantuml_jar() -> Optional[str]:
    for p in ["/usr/share/plantuml/plantuml.jar",
              "/usr/local/bin/plantuml.jar",
              str(Path.home() / "plantuml.jar")]:
        if Path(p).exists():
            return p
    return None


def validate_puml(puml: str) -> ValidatorResult:
    """Validate PlantUML using plantuml.jar -syntax if available."""
    tool = "plantuml"
    if not puml.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty diagram"], tool=tool)
    if "@startuml" not in puml:
        return ValidatorResult(ok=False, score=0.1, errors=["Missing @startuml"], tool=tool)
    if "@enduml" not in puml:
        return ValidatorResult(ok=False, score=0.15, errors=["Missing @enduml"], tool=tool)

    java    = _tool("java")
    jar     = _find_plantuml_jar()
    if java and jar:
        fd, tmp = tempfile.mkstemp(suffix=".puml")
        try:
            os.write(fd, puml.encode())
            os.close(fd)
            rc, stdout, stderr = _run([java, "-jar", jar, "-syntax", tmp])
        finally:
            os.unlink(tmp)
        combined = (stdout + stderr).lower()
        if rc == 0 and "error" not in combined:
            return ValidatorResult(ok=True, score=1.0, tool="plantuml.jar -syntax")
        error_lines = [l for l in (stdout+stderr).splitlines() if "error" in l.lower()]
        return ValidatorResult(ok=False, score=_partial(len(error_lines)),
                               errors=error_lines[:4], tool="plantuml.jar -syntax")

    # Regex fallback
    score = 0.0
    errors = []
    score += 0.30 if "@startuml" in puml else 0
    score += 0.30 if "@enduml"   in puml else 0
    score += 0.20 if any(a in puml for a in ["->","-->","=>"]) else 0
    score += 0.20 if any(c in puml for c in ["component","package","node","class","rectangle"]) else 0
    if len(puml.splitlines()) < 5:
        errors.append("Diagram too short — likely incomplete")
    return ValidatorResult(ok=score>=0.8 and not errors, score=round(score,3),
                           errors=errors, tool="puml-regex-fallback")


# ═════════════════════════════════════════════════════════════════════════════
# C1 — Kotlin Fragment  ·  kotlinc or regex fallback
# ═════════════════════════════════════════════════════════════════════════════

# Android API names that will always be "unresolved reference" without SDK
_ANDROID_API_NAMES = {
    "CarPropertyManager","Car","Fragment","Context","Bundle",
    "View","LayoutInflater","ViewGroup","CarPropertyEventCallback",
    "R","ViewBinding","ViewModel","LiveData","lifecycleScope",
}

def validate_kotlin(code: str) -> ValidatorResult:
    """
    Validate Kotlin using kotlinc.
    Android SDK API "unresolved reference" errors are filtered out —
    they are expected on a host machine without the Android SDK installed.
    """
    tool = "kotlinc"
    if not code.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty output"], tool=tool)

    kotlinc = _tool("kotlinc")
    if not kotlinc:
        return _kotlin_regex_fallback(code)

    fd, tmp = tempfile.mkstemp(suffix=".kt")
    try:
        os.write(fd, code.encode())
        os.close(fd)
        rc, stdout, stderr = _run([kotlinc, "-nowarn", tmp, "-d", "/dev/null"], timeout=60)
    finally:
        os.unlink(tmp)

    real_errors = []
    for line in stderr.splitlines():
        if "error:" not in line.lower():
            continue
        ref = re.search(r"unresolved reference:\s*(\w+)", line, re.IGNORECASE)
        if ref and ref.group(1) in _ANDROID_API_NAMES:
            continue    # expected without Android SDK
        real_errors.append(line)

    if not real_errors:
        return ValidatorResult(ok=True, score=1.0, tool=tool,
                               detail="Syntax OK (Android API refs filtered)")
    return ValidatorResult(ok=False, score=_partial(len(real_errors)),
                           errors=real_errors[:5], tool=tool)


def _kotlin_regex_fallback(code: str) -> ValidatorResult:
    tool, errors, score = "kotlin-regex-fallback", [], 0.0
    if any(c in code for c in ["CarPropertyManager","Car.createCar"]): score += 0.25
    if "Fragment" in code:    score += 0.15
    if "fun "     in code:    score += 0.15
    if any(m in code for m in ["onViewCreated","onCreateView"]):       score += 0.15
    if any(c in code for c in ["registerCallback","CarPropertyEventCallback"]): score += 0.15
    if code.count("{") == code.count("}"): score += 0.15
    else: errors.append("Unbalanced braces")
    return ValidatorResult(ok=score>=0.75 and not errors, score=round(score,3),
                           errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# C2 — Android XML Layout  ·  xml.etree
# ═════════════════════════════════════════════════════════════════════════════

def validate_layout_xml(xml_str: str) -> ValidatorResult:
    """Validate Android layout XML using Python's xml.etree parser."""
    tool = "xml.etree + android-layout"
    if not xml_str.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty XML"], tool=tool)

    errors, score = [], 0.0
    try:
        root = ET.fromstring(xml_str)
        score += 0.35
    except ET.ParseError as e:
        return ValidatorResult(ok=False, score=0.1,
                               errors=[f"XML parse error: {e}"], tool=tool)

    tag = root.tag.split("}")[-1]
    if tag in {"LinearLayout","ConstraintLayout","RelativeLayout",
               "FrameLayout","ScrollView","CoordinatorLayout"}:
        score += 0.20
    else:
        errors.append(f"Unusual root element <{tag}>")

    ns = "http://schemas.android.com/apk/res/android"
    ids = [el.get(f"{{{ns}}}id") or el.get("android:id") for el in root.iter()]
    ids = [i for i in ids if i]
    if ids:
        score += 0.20
        bad = [i for i in ids if not i.startswith("@+id/") and not i.startswith("@id/")]
        if bad:
            errors.append(f"android:id should use '@+id/name' format — bad: {bad[:2]}")
    else:
        errors.append("No android:id attributes found")

    tags = {el.tag.split("}")[-1] for el in root.iter()}
    widgets = {"TextView","Switch","Button","SeekBar","CheckBox","EditText","ImageView"}
    if tags & widgets: score += 0.15
    else:              errors.append("No standard widget elements found")

    if 'xmlns:android="http://schemas.android.com/apk/res/android"' in xml_str:
        score += 0.10
    else:
        errors.append("Missing android namespace declaration")

    score = round(min(score, 1.0), 3)
    ok    = score >= 0.75 and not any("parse error" in e.lower() for e in errors)
    return ValidatorResult(ok=ok, score=score, errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# D — Python outputs  ·  ast.parse  (always available, full syntax check)
# ═════════════════════════════════════════════════════════════════════════════

def validate_python(code: str, agent_type: str = "python") -> ValidatorResult:
    """
    Validate Python code using ast.parse() — the same parser CPython uses.
    No cross-compilation needed; works for FastAPI, Pydantic, and asyncio code.
    Checks agent-type-specific patterns using the parsed AST.
    """
    tool = f"ast.parse [{agent_type}]"
    if not code.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty output"], tool=tool)

    # Full Python syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ValidatorResult(ok=False, score=0.1,
                               errors=[f"SyntaxError line {e.lineno}: {e.msg}"],
                               tool=tool)

    score, errors = 0.40, []   # 0.40 base for passing AST parse

    # Collect AST facts
    classes   = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    funcs     = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    async_fns = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    imports   = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module)
    decorators = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                decorators.append(ast.dump(d))

    if agent_type == "backend":
        if any("fastapi" in m.lower() for m in imports):
            score += 0.20
        else:
            errors.append("Missing FastAPI import")
        if async_fns:
            score += 0.20
        else:
            errors.append("No async def endpoints found")
        if any(k in d for d in decorators for k in ["get","post","put","delete"]):
            score += 0.20
        else:
            errors.append("No route decorator (@app.get/post) found")

    elif agent_type == "backend_model":
        if any("pydantic" in m.lower() for m in imports) or "BaseModel" in code:
            score += 0.20
        else:
            errors.append("Missing Pydantic import")
        if classes:
            score += 0.20
        else:
            errors.append("No class definitions found")
        annotated = [n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)]
        if annotated:
            score += 0.20
        else:
            errors.append("No type-annotated fields found")

    elif agent_type == "simulator":
        if classes:                                          score += 0.15
        if async_fns or "asyncio" in imports:               score += 0.20
        else: errors.append("No asyncio usage — simulator should be async")
        if any(f in funcs for f in ["start","stop","run","generate"]): score += 0.20
        else: errors.append("Missing start()/stop()/run() method")
        if "random" in imports or "random" in code:         score += 0.05

    score = round(min(score, 1.0), 3)
    ok    = score >= 0.75 and len(errors) == 0
    return ValidatorResult(
        ok=ok, score=score, errors=errors, tool=tool,
        detail=f"classes={len(classes)}, async_fns={len(async_fns)}",
    )


# ═════════════════════════════════════════════════════════════════════════════
# B0 — Markdown design document
# ═════════════════════════════════════════════════════════════════════════════

def validate_markdown(doc: str) -> ValidatorResult:
    tool, errors, score = "markdown-structure", [], 0.0
    if not doc.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty document"], tool=tool)
    if "## " in doc or "# " in doc: score += 0.30
    else: errors.append("No Markdown headings found")
    sections = ["overview","architecture","propert","security","build","data flow"]
    score += round(0.10 * sum(1 for s in sections if s in doc.lower()), 2)
    if len(doc) >= 500: score += 0.15
    else: errors.append(f"Document too short ({len(doc)} chars, expected 500+)")
    if "|" in doc:      score += 0.10
    score = round(min(score, 1.0), 3)
    return ValidatorResult(ok=score>=0.75 and not errors, score=score,
                           errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# Unified dispatch
# ═════════════════════════════════════════════════════════════════════════════

def validate(agent_type: str, code: str) -> ValidatorResult:
    """Dispatch to the correct validator. Safe for any agent_type."""
    dispatch = {
        "aidl":           validate_aidl,
        "cpp":            validate_cpp,
        "selinux":        validate_selinux,
        "build":          validate_android_bp,
        "vintf":          validate_vintf_xml,
        "puml":           validate_puml,
        "android_app":    validate_kotlin,
        "android_layout": validate_layout_xml,
        "design_doc":     validate_markdown,
        "backend":        lambda c: validate_python(c, "backend"),
        "backend_model":  lambda c: validate_python(c, "backend_model"),
        "simulator":      lambda c: validate_python(c, "simulator"),
    }
    fn = dispatch.get(agent_type)
    if fn is None:
        return ValidatorResult(ok=True, score=0.5, tool="no-validator",
                               detail=f"No validator for '{agent_type}'")
    return fn(code)


# ═════════════════════════════════════════════════════════════════════════════
# Availability report  —  print at experiment start, include in thesis methods
# ═════════════════════════════════════════════════════════════════════════════

def validator_availability_report() -> dict[str, dict]:
    """
    Returns a dict describing which tools are fully available vs. fallback.
    Include this output in your thesis methods section so reviewers know
    exactly what validation level was achieved for each output type.
    """
    return {
        "aidl":           {"tool": "Python AIDL grammar parser",
                           "available": True,
                           "note": "Host-native; no AOSP toolchain needed"},
        "cpp":            {"tool": "clang++ --syntax-only",
                           "available": bool(_tool("clang++") or _tool("clang")),
                           "fallback": "cpp-regex",
                           "note": "Syntax only; AOSP headers stubbed in"},
        "selinux":        {"tool": "checkpolicy",
                           "available": bool(_tool("checkpolicy")),
                           "fallback": "selinux-regex",
                           "note": "Full policy compile; no cross-compilation needed"},
        "build":          {"tool": "Python Android.bp parser",
                           "available": True,
                           "note": "Host-native; Soong not available on host"},
        "vintf":          {"tool": "xml.etree + VINTF schema",
                           "available": True,
                           "note": "Python stdlib; always available"},
        "puml":           {"tool": "plantuml.jar -syntax",
                           "available": bool(_tool("java") and _find_plantuml_jar()),
                           "fallback": "puml-regex"},
        "android_app":    {"tool": "kotlinc (syntax only)",
                           "available": bool(_tool("kotlinc")),
                           "fallback": "kotlin-regex",
                           "note": "Android SDK API refs filtered from errors"},
        "android_layout": {"tool": "xml.etree",
                           "available": True,
                           "note": "Python stdlib; always available"},
        "backend":        {"tool": "ast.parse",
                           "available": True,
                           "note": "Full Python AST — identical to CPython"},
        "backend_model":  {"tool": "ast.parse",
                           "available": True},
        "simulator":      {"tool": "ast.parse",
                           "available": True},
        "design_doc":     {"tool": "markdown-structure",
                           "available": True},
    }


def print_availability_report() -> None:
    """Print a formatted availability table to stdout."""
    report = validator_availability_report()
    print("\n[Validators] Tool availability report:")
    print(f"  {'Agent':<18} {'Tool':<32} {'Status':<10} Note")
    print("  " + "─" * 80)
    for agent, info in report.items():
        status = "✓ full" if info["available"] else f"⚠ fallback"
        note   = info.get("note", info.get("fallback", ""))
        print(f"  {agent:<18} {info['tool']:<32} {status:<10} {note}")
    print()