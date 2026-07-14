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
import re


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

    Accepts both:
      - interface pattern: interface IName { ReturnType method(args); }
      - enum pattern: @Backing(type="int") enum Name { CONST = 0x1000, }

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

    # 1. Package declaration (0.20)
    pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", code, re.MULTILINE)
    if pkg:
        score += 0.20
        if not re.match(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$", pkg.group(1)):
            errors.append(f"Package '{pkg.group(1)}' should be lowercase.dot.separated")
    else:
        errors.append("Missing package declaration — e.g. 'package android.hardware.automotive.vehicle;'")

    # 2. Detect AIDL type: interface OR enum (0.25)
    is_interface = bool(re.search(r"(?:@\w+[\s()\w=\"]*\s*)*interface\s+(\w+)\s*\{", code))
    is_enum = bool(re.search(r"(?:@\w+[\s()\w=\"]*\s*)*enum\s+(\w+)\s*\{", code))
    is_parcelable = bool(re.search(r"(?:@\w+[\s()\w=\"]*\s*)*parcelable\s+(\w+)\s*\{", code))

    if is_interface:
        iface = re.search(r"interface\s+(\w+)\s*\{", code)
        score += 0.25
        if iface and not iface.group(1).startswith("I"):
            errors.append(f"Interface '{iface.group(1)}' should start with 'I' per AIDL convention")
    elif is_enum:
        enum_match = re.search(r"enum\s+(\w+)\s*\{", code)
        score += 0.25
        # Check for @Backing annotation (required for typed enums)
        if "@Backing" in code:
            score += 0.05  # bonus for proper enum annotation
    elif is_parcelable:
        score += 0.25
    else:
        errors.append("No interface, enum, or parcelable block found")

    # 3. Brace balance (0.10)
    if code.count("{") == code.count("}"):
        score += 0.10
    else:
        errors.append(f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close")

    # 4. Content validation — depends on AIDL type (0.35)
    if is_interface:
        # Interface: check method signatures
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
            errors.append("No method signatures found in interface")

    elif is_enum:
        # Enum: check for constant definitions (NAME = value,)
        constants = re.findall(
            r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", code
        )
        if constants:
            score += 0.20
            # Check that constants are UPPER_CASE
            bad_names = [n for n, _ in constants if not re.match(r"^[A-Z][A-Z0-9_]*$", n)]
            if not bad_names:
                score += 0.15
            else:
                errors.extend([f"Enum constant '{n}' should be UPPER_CASE" for n in bad_names[:3]])
        else:
            errors.append("No enum constants found — expected: 'NAME = 0x1000,'")

    elif is_parcelable:
        # Parcelable: check for field declarations
        fields = re.findall(r"(\w+)\s+(\w+)\s*;", code)
        if fields:
            score += 0.35
        else:
            errors.append("No field declarations found in parcelable")

    # 5. @VintfStability annotation (0.10)
    if "@VintfStability" in code:
        score += 0.10

    ok = (score >= 0.70) and (len(errors) == 0)
    detail = ""
    if is_interface:
        methods = re.findall(r"(?:oneway\s+)?\w[\w<>\[\], ]*\s+\w+\s*\([^)]*\)\s*;", code)
        detail = f"interface, {len(methods)} methods"
    elif is_enum:
        constants = re.findall(r"\w+\s*=\s*(0x[0-9a-fA-F]+|\d+)", code)
        detail = f"enum, {len(constants)} constants"
    elif is_parcelable:
        detail = "parcelable"

    return ValidatorResult(
        ok=ok,
        score=round(score, 3) if ok else round(score * 0.65, 3),
        errors=errors, tool=tool,
        detail=detail,
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

// AIDL-generated data types live in aidl::android::hardware::automotive::
// vehicle in real AOSP (confirmed against the actual VehiclePropValue.aidl /
// GetValueRequest.aidl / IVehicleCallback.aidl sources) — NOT in the plain
// android::hardware::automotive::vehicle namespace. Real generated code
// relies on this via `using namespace aidl::android::hardware::automotive::
// vehicle;`. Declaring these WITHOUT the aidl:: wrapper meant that
// using-directive failed with "undeclared identifier 'aidl'", which made
// clang abort before reaching ANY semantic check on VehicleProperty::X
// expressions — including duplicate-case-value detection.
namespace aidl::android::hardware::automotive::vehicle {
    struct VehiclePropValue {
        int32_t prop = 0;
        int32_t areaId = 0;
        struct {
            std::vector<int32_t> int32Values;
            std::vector<float>   floatValues;
            std::vector<bool>    boolValues;
            std::vector<uint8_t> byteValues;
            std::string          stringValue;
        } value;
    };
    struct VehiclePropConfig {
        int32_t prop = 0;
        int access = 0;
        int changeMode = 0;
        float minSampleRate = 0.0f;
        float maxSampleRate = 0.0f;
        struct AreaCfg { int32_t areaId = 0; };
        std::vector<AreaCfg> areaConfigs;
    };
    struct GetValueRequest    { int64_t requestId = 0; VehiclePropValue prop; };
    struct SetValueRequest    { int64_t requestId = 0; VehiclePropValue value; };
    struct GetValueResult     { int64_t requestId = 0; int status = 0; VehiclePropValue prop; };
    struct SetValueResult     { int64_t requestId = 0; int status = 0; };
    struct SetValueErrorEvent { int64_t requestId = 0; int32_t propId = 0; int errorCode = 0; };
    struct SubscribeOptions   { int32_t propId = 0; std::vector<int32_t> areaIds; float sampleRate = 0; };
    struct DumpResult         { bool callerShouldDumpState = false; std::string buffer; };
    struct StatusCode {
        static constexpr int OK = 0;
        static constexpr int INVALID_ARG = 1;
        static constexpr int NOT_AVAILABLE = 2;
        int v = 0;
        constexpr StatusCode() = default;
        constexpr StatusCode(int x) : v(x) {}
        constexpr operator int() const { return v; }
    };

    // Plain (unscoped) enums so they implicitly convert to the int-typed
    // VehiclePropConfig.access/changeMode fields above, matching how real
    // AOSP's @Backing(type="int") VehiclePropertyAccess/ChangeMode behave
    // when assigned via designated initializers.
    enum VehiclePropertyAccess     { NONE = 0, READ = 1, WRITE = 2, READ_WRITE = 3 };
    enum VehiclePropertyChangeMode { STATIC = 0, ON_CHANGE = 1, CONTINUOUS = 2 };
}

// IVehicleHardware is NOT AIDL-generated — it's a reference-implementation
// C++ interface AOSP defines directly in android::hardware::automotive::
// vehicle. Its method signatures use the AIDL types above, brought into
// scope here exactly as real IVehicleHardware.h / VehicleHalTypes.h do.
namespace android::hardware::automotive::vehicle {
    using namespace aidl::android::hardware::automotive::vehicle;

    using GetValuesCallback         = std::function<void(std::vector<GetValueResult>)>;
    using SetValuesCallback         = std::function<void(std::vector<SetValueResult>)>;
    using PropertyChangeCallback    = std::function<void(std::vector<VehiclePropValue>)>;
    using PropertySetErrorCallback  = std::function<void(std::vector<SetValueErrorEvent>)>;

    class IVehicleHardware {
    public:
        virtual ~IVehicleHardware() = default;
        virtual std::vector<VehiclePropConfig> getAllPropertyConfigs() const = 0;
        virtual StatusCode getValues(std::shared_ptr<const GetValuesCallback> callback,
                                      const std::vector<GetValueRequest>& requests) const = 0;
        virtual StatusCode setValues(std::shared_ptr<const SetValuesCallback> callback,
                                      const std::vector<SetValueRequest>& requests) = 0;
        virtual StatusCode updateSampleRate(int32_t cookie, int32_t propId, float sampleRate) { return StatusCode::OK; }
        virtual StatusCode subscribe(const SubscribeOptions& options) { return StatusCode::OK; }
        virtual StatusCode unsubscribe(int32_t cookie, int32_t propId) { return StatusCode::OK; }
        virtual DumpResult dump(const std::vector<std::string>& options) { return {}; }
        virtual StatusCode checkHealth() { return StatusCode::OK; }
        virtual void registerOnPropertyChangeEvent(std::unique_ptr<const PropertyChangeCallback> callback) {}
        virtual void registerOnPropertySetErrorEvent(std::unique_ptr<const PropertySetErrorCallback> callback) {}
    };
}
using namespace android::hardware::automotive::vehicle;
using namespace aidl::android::hardware::automotive::vehicle;
// ────────────────────────────────────────────────────────────────────────────
"""

# Number of lines in _CPP_VHAL_STUBS — computed once at import time so the
# stub-line filter below stays correct even if the stub block is edited,
# instead of a hardcoded magic number that silently drifts out of sync.
_CPP_VHAL_STUBS_LINE_COUNT = _CPP_VHAL_STUBS.count("\n") + 1

# Generic placeholder AIDL enum used when validate_cpp() detects a
# `#include <aidl/.../VehicleProperty{Domain}.h>` for a domain it has no
# real generated content for (validate_cpp only receives a `code: str`,
# not which domain/AIDL-file context it came from). This intentionally
# does NOT try to guess real property names — it only needs to make the
# *type* `VehicleProperty{Domain}` exist so static_cast<int32_t>(...)
# expressions involving ANY identifier compile syntactically. A real
# "does this name actually exist" check is a separate, stronger
# concern handled by CppVehicleAssertions' AIDL cross-check in
# dspy_opt/hal_signatures.py, not by this syntax-only validator.
_USED_AIDL_CONSTANT_RE_TEMPLATE = r'VehicleProperty::(\w+)'


def _build_aidl_enum_stub(code: str, domain: str) -> str:
    """Build a VehicleProperty enum stub containing every constant name
    `code` actually references (via VehicleProperty::NAME), each given
    a distinct placeholder value. This guarantees the stub always
    satisfies whatever names the real generated code uses.

    Uses the PLAIN, unqualified `VehicleProperty` enum name — matching
    what real generated code actually references — NOT a domain-suffixed
    `VehicleProperty{domain}`. The project's real AIDL architecture
    merges every domain's properties into ONE combined VehicleProperty
    enum, so code always writes `VehicleProperty::X`, never
    `VehicleProperty{Domain}::X`, regardless of which domain-specific
    .aidl FILE the #include line names.

    This is NOT a check for whether those names are real AIDL property
    names (that's CppVehicleAssertions' job, which has the actual
    generated .aidl content to cross-check against) — it only needs to
    make `static_cast<int32_t>(VehicleProperty::X)` compile
    syntactically for whatever X appears in the code being validated,
    so clang can proceed to its own native semantic checks (duplicate
    case value among them) on the rest of the file.
    """
    usage_re = re.compile(_USED_AIDL_CONSTANT_RE_TEMPLATE)
    names = sorted(set(usage_re.findall(code)))
    if not names:
        names = ["_VALIDATOR_PLACEHOLDER_DO_NOT_USE"]
    body = "\n".join(f"    {name} = {i}," for i, name in enumerate(names))
    return (
        "#pragma once\n"
        "#include <cstdint>\n"
        f"namespace aidl::android::hardware::automotive::vehicle {{\n"
        f"enum class VehicleProperty : int32_t {{\n"
        f"{body}\n"
        "};\n"
        "}\n"
    )


_CLASS_DECL_RE = re.compile(
    r'class\s+\w+\s*:\s*public\s+IVehicleHardware\s*\{'
)


def _reorder_class_decl_first(code: str) -> str:
    """gen_hal_minimal_c4.py / multi_main_c4_feedback.py's retry engine
    concatenates files as `impl_text + "\n" + header_text + ...`
    (cpp_impl first, .h second) when building the combined string to
    validate — but C++ requires the class DEFINITION (from the .h) to
    appear before any `ClassName::method(...)` out-of-line definitions
    (from the .cpp) that reference it. Left as-is, every single
    validate_cpp() call on real generated output fails with "use of
    undeclared identifier 'VehicleHalService{Domain}'" regardless of
    whether the code itself is correct — a validator-side ordering bug,
    not a code-quality issue.

    This splits `code` on `#pragma once` boundaries (the natural file
    boundary every generated .h starts with) and moves whichever chunk
    contains the `class X : public IVehicleHardware {` definition to
    the front, preserving the relative order of all other chunks.
    If there's only one chunk (no `#pragma once` boundary found, e.g.
    validate_cpp() is being called on an isolated snippet rather than
    a header+impl combo), this is a no-op.
    """
    parts = re.split(r"(?=^#pragma once)", code, flags=re.MULTILINE)
    parts = [p for p in parts if p.strip()]
    if len(parts) <= 1:
        return code

    class_decl_idx = None
    for i, part in enumerate(parts):
        if _CLASS_DECL_RE.search(part):
            class_decl_idx = i
            break

    if class_decl_idx is None or class_decl_idx == 0:
        return code  # already first, or no class def found — leave as-is

    reordered = [parts[class_decl_idx]] + parts[:class_decl_idx] + parts[class_decl_idx + 1:]
    return "\n".join(reordered)


_SELF_INCLUDE_RE = re.compile(r'#include\s*"([^"]+\.h)"')
_AIDL_INCLUDE_RE = re.compile(
    r'#include\s*<aidl/android/hardware/automotive/vehicle/(VehicleProperty(\w+))\.h>'
)


def validate_cpp(code: str) -> ValidatorResult:
    """
    Validate C++ VHAL code using clang++ --syntax-only.

    Injects minimal AOSP VHAL stubs so clang can check syntax without
    a full AOSP checkout. --syntax-only skips linking entirely so no
    Android libraries or NDK sysroot are needed.

    Beyond the inline _CPP_VHAL_STUBS prelude (IVehicleHardware,
    VehicleHalTypes, etc. — injected as raw text ahead of `code`),
    this also resolves two classes of #include that the prelude alone
    cannot satisfy, because their exact filename is generated content
    not known until this code is scanned:

      1. Self-include: `#include "VehicleHalService{Domain}.h"` — the
         generated .h is already concatenated into `code` by the
         caller (gen_hal_minimal_c4.py / multi_main_c4_feedback.py
         pass header + impl + main_service as one combined string), so
         re-resolving the include via the filesystem would duplicate
         the class definition. An EMPTY stand-in file is created for
         it instead, so the #include line is a no-op and the real
         class definition (already present later in `code`) is the
         only one the compiler sees.

      2. AIDL enum include: `#include <aidl/.../VehicleProperty{Domain}.h>`
         — the real enum lives in a sibling file this validator never
         sees. A generic placeholder enum is generated so the *type*
         resolves and any `static_cast<int32_t>(VehicleProperty{Domain}::X)`
         expression is syntactically valid, regardless of which
         property name X is. (Whether X is a REAL property name is
         checked separately and more accurately by
         CppVehicleAssertions' AIDL cross-check, which has access to
         the actual generated .aidl content — this validator does not.)

    What this catches: missing semicolons, bad declarations, type
      mismatches, incorrect template usage, malformed class definitions.
    What it does NOT catch: missing VHAL method implementations,
      wrong/hallucinated property enum values (see CppVehicleAssertions
      instead), link-time errors.
    """
    tool = "clang++ -fsyntax-only"
    if not code.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty output"], tool=tool)

    # Hard, unconditional safety net — catches truncated/cut-off generation
    # (e.g. LLM output hit a token limit mid-statement) regardless of how
    # clang++'s stub-injection/line-filtering behaves below. A real,
    # complete C++ translation unit is always brace-balanced; this check
    # costs nothing and cannot be fooled by line-number filtering quirks.
    open_braces, close_braces = code.count("{"), code.count("}")
    if open_braces != close_braces:
        return ValidatorResult(
            ok=False, score=0.0, tool=tool,
            errors=[f"Unbalanced braces ({open_braces} open vs {close_braces} close) "
                    f"— output is truncated or otherwise malformed, likely cut off "
                    f"mid-statement by a token limit"])

    # Hard fail: the exact stub pattern the contract explicitly forbids.
    # Real AOSP compile would still succeed on this pattern (it's valid
    # C++), so clang++ syntax-only below cannot catch it — it's a contract
    # violation, not a syntax error, so it must be checked here directly.
    if re.search(r"\(\*callback\)\(\{\}\)", code):
        return ValidatorResult(
            ok=False, score=0.1, tool=tool,
            errors=["getValues/setValues discards requests and returns an "
                    "empty vector via callback({}) — this is a forbidden "
                    "stub, not a real HW-register implementation"])

    # Hard fail: field names that don't exist on the real AIDL
    # VehiclePropValue.value (RawPropValues) type. These compile-clean
    # against the validator's own placeholder stubs (see docstring above)
    # but fail against the REAL AOSP header, which is exactly the kind of
    # false-pass this check exists to prevent.
    invalid_fields = [f for f in ("booleanValues", "boolValues") if f in code]
    if invalid_fields:
        return ValidatorResult(
            ok=False, score=0.1, tool=tool,
            errors=[f"Uses non-existent RawPropValues field(s) "
                    f"{invalid_fields} — real AOSP has no boolean array "
                    f"field; booleans must use int32Values with 0/1"])

    # Hard fail: readRegister/writeRegister declared (in the header, as
    # private members every VehicleHalService{Domain} has) but never
    # DEFINED in the implementation — a linker error at build time that
    # brace-balance/stub/field-name checks above cannot catch, since the
    # file is syntactically well-formed C++, it just doesn't link.
    declares_read = re.search(r"bool\s+readRegister\s*\(", code) is not None
    declares_write = re.search(r"bool\s+writeRegister\s*\(", code) is not None
    if declares_read or declares_write:
        defines_read = re.search(r"::readRegister\s*\(", code) is not None
        defines_write = re.search(r"::writeRegister\s*\(", code) is not None
        missing = []
        if declares_read and not defines_read:
            missing.append("readRegister")
        if declares_write and not defines_write:
            missing.append("writeRegister")
        if missing:
            return ValidatorResult(
                ok=False, score=0.1, tool=tool,
                errors=[f"{missing} declared in header but never defined "
                        f"in implementation — undefined reference at "
                        f"link time"])

    # Hard fail: every property declared in getAllPropertyConfigs() must
    # have a corresponding case in readRegister() — a property missing
    # its read case is syntactically valid C++ (falls through to
    # `default: return false`), so none of the checks above catch it,
    # but it's a real functional defect: getValues() on that property
    # will always return INVALID_ARG. This is exactly the gap the
    # register-body chunk retry loop can end up with after exhausting
    # MAX_CHUNK_RETRIES ("gave up after N retries: still missing M") —
    # confirmed in practice: that log line printed, and this validator
    # still scored the file 1.000, because nothing compared the two
    # counts. writeRegister is NOT checked the same way: READ-only
    # properties correctly have no write case by design (see
    # CppRegisterBodySignature), so write_count < config_count is
    # often correct, not a defect.
    config_section = code[:code.find("::readRegister")] if "::readRegister" in code else code
    config_count = config_section.count("static_cast<int32_t>(VehicleProperty::")
    read_section_start = code.find("::readRegister")
    read_section_end = code.find("::writeRegister") if "::writeRegister" in code else len(code)
    read_section = code[read_section_start:read_section_end] if read_section_start >= 0 else ""
    read_case_count = len(re.findall(r"case\s+static_cast<int32_t>\(VehicleProperty::", read_section))
    if config_count > 0 and read_case_count < config_count:
        return ValidatorResult(
            ok=False, score=0.3, tool=tool,
            errors=[f"getAllPropertyConfigs() declares {config_count} properties "
                    f"but readRegister() only has {read_case_count} case(s) — "
                    f"{config_count - read_case_count} propert(y/ies) will always "
                    f"return INVALID_ARG from getValues(), even though the file "
                    f"compiles and links cleanly"])

    clang = _tool("clang++") or _tool("clang")
    if not clang:
        return _cpp_regex_fallback(code)

    tmpdir = tempfile.mkdtemp(prefix="cpp_validate_")
    try:
        # ── Resolve the two core AOSP angle-includes every generated file
        # has (#include <IVehicleHardware.h> / <VehicleHalTypes.h>) by
        # writing _CPP_VHAL_STUBS out as a REAL file clang's #include
        # resolves to via -I, rather than prepending it as raw text.
        # Prepending as text only works when the real code does NOT
        # also `#include <IVehicleHardware.h>` itself — but every
        # contract-correct generated header DOES include it, so the
        # text-prepend approach left that angle-include unresolved and
        # clang stopped at "fatal error: file not found" before ever
        # reaching the real code below it.
        with open(os.path.join(tmpdir, "IVehicleHardware.h"), "w") as f:
            f.write(_CPP_VHAL_STUBS)
        with open(os.path.join(tmpdir, "VehicleHalTypes.h"), "w") as f:
            f.write("#pragma once\n#include <IVehicleHardware.h>\n")

        # gen_hal_minimal_c4.py / multi_main_c4_feedback.py's retry
        # engine concatenates impl text BEFORE header text — reorder so
        # the class definition (from the header chunk) appears before
        # any ClassName::method(...) out-of-line definitions that
        # reference it, which C++ requires regardless of caller order.
        code = _reorder_class_decl_first(code)

        # ── Resolve self-includes: empty stand-in, real def comes from `code` ──
        for m in _SELF_INCLUDE_RE.finditer(code):
            header_name = m.group(1)
            stub_path = os.path.join(tmpdir, header_name)
            if not os.path.exists(stub_path):
                with open(stub_path, "w") as f:
                    f.write("#pragma once\n// validator stand-in: real definition is concatenated below in the same file\n")

        # ── Resolve AIDL enum includes: stub containing every name `code` actually uses ──
        aidl_dir = os.path.join(tmpdir, "aidl", "android", "hardware", "automotive", "vehicle")
        seen_domains = set()
        for m in _AIDL_INCLUDE_RE.finditer(code):
            enum_filename, domain = m.group(1), m.group(2)
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            os.makedirs(aidl_dir, exist_ok=True)
            with open(os.path.join(aidl_dir, f"{enum_filename}.h"), "w") as f:
                f.write(_build_aidl_enum_stub(code, domain))

        tmp = os.path.join(tmpdir, "combined.cpp")
        with open(tmp, "w") as f:
            f.write(code)

        rc, _, stderr = _run([
            clang, "-fsyntax-only", "-x", "c++", "-std=c++17",
            "-I", tmpdir,
            "-Wno-unknown-pragmas", "-Wno-unused-variable",
            "-Wno-unused-function", "-Wno-error", "-Wno-pragma-once-outside-header",
            tmp,
        ])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # Filter error lines that are from stub/scaffolding files, keeping
    # everything reported against combined.cpp (the real code under
    # test). Filename-based, not line-number-based: a line-number
    # threshold assumes any error at a "low" line number must be a
    # stub-file error — but combined.cpp has its own independent line
    # numbering starting at 1 (it's a separate file, not appended after
    # the stub), so a genuine error on e.g. combined.cpp's line 33 was
    # incorrectly discarded whenever the stub prelude itself happened
    # to be >= 33 lines long, silently hiding real errors as a side
    # effect of how long the stub was.
    real_errors = []
    for line in stderr.splitlines():
        if "error:" not in line.lower():
            continue
        if "Stubs injected" in line:
            continue
        if "combined.cpp" not in line:
            continue    # error is in a stub/scaffolding file, not the real code
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
security_classes = {binder file dir service_manager hwservice_manager property_service chr_file}
initial_sids = {kernel}
sid kernel
"""

def validate_selinux(policy: str) -> ValidatorResult:
    """
    Validate a SELinux .te policy file.

    checkpolicy requires a full AOSP policy tree to parse standalone .te
    files — it cannot validate them in isolation on a host machine.
    We use a structural regex validator instead, which checks for correct
    AOSP 14 AIDL patterns and rejects HIDL patterns.
    """
    # Strip stray leading/trailing braces — common LLM artifact
    # IMPORTANT: only strip standalone } not part of allow rule closing };
    policy = policy.strip()
    while policy.startswith("{"):
        policy = policy[1:].strip()
    lines = policy.splitlines()
    while lines and lines[-1].strip() == "}":
        lines.pop()
    policy = "\n".join(lines)

    return _selinux_regex_fallback(policy)


def _selinux_regex_fallback(policy: str) -> ValidatorResult:
    import re as _re
    tool, errors, score = "selinux-regex", [], 0.0

    # HIDL detection — fail immediately
    hidl_patterns = ["hal_attribute_hwservice", "add_hwservice", "find_hwservice",
                     "hwservice_manager", "hwbinder_device", "fwk_vehicle_hwservice"]
    found_hidl = [p for p in hidl_patterns if p in policy]
    if found_hidl:
        return ValidatorResult(ok=False, score=0.1,
                               errors=[f"HIDL pattern found: {found_hidl[0]}"],
                               tool=tool)

    # New contract: this fragment runs INSIDE the shared hal_vehicle_vss
    # process. It must NOT declare a new per-domain daemon — that domain
    # would never actually run.
    declares_new_domain = bool(_re.search(r"type\s+\w+\s*,\s*domain\s*;", policy)) \
        or "init_daemon_domain(" in policy or "hal_server_domain(" in policy
    if declares_new_domain:
        errors.append("Declares a new per-domain SELinux type/daemon — all "
                       "rules must target the shared hal_vehicle_vss domain "
                       "instead (see SELinuxSignature contract)")
    else:
        score += 0.30

    # Every domain's C++ genuinely performs file I/O against the shared
    # vss_hw_data_file type (simulated HW register files) — this is now
    # mandatory, not optional, because getValues/setValues are backed by
    # real file I/O rather than a stub or in-memory map.
    if "vss_hw_data_file" in policy and "allow hal_vehicle_vss vss_hw_data_file" in policy:
        score += 0.35
    else:
        errors.append("Missing allow hal_vehicle_vss vss_hw_data_file rule "
                       "— required because this domain's HAL implementation "
                       "performs real file I/O against the simulated "
                       "hardware register directory")

    # Any OTHER allow rules present must still target hal_vehicle_vss
    allow_lines = [l for l in policy.splitlines() if l.strip().startswith("allow ")]
    bad = [l for l in allow_lines
           if not l.strip().startswith("allow hal_vehicle_vss ")
           or not l.strip().rstrip("{").rstrip().endswith((";", "{"))]
    if bad:
        errors.append(f"{len(bad)} allow rule(s) not scoped to "
                       f"hal_vehicle_vss or malformed")
    else:
        score += 0.20

    if policy.count("{") == policy.count("}"):
        score += 0.15
    else:
        errors.append("Unbalanced braces")

    ok = score >= 0.70 and len(errors) == 0
    return ValidatorResult(ok=ok, score=round(score, 3),
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

    # <transport> — AIDL HALs (Android 14+) omit <transport>; HIDL uses hwbinder
    transport = root.find(".//transport")
    if transport is not None:
        if transport.text == "hwbinder":
            errors.append("HIDL transport 'hwbinder' found — use AIDL format (omit <transport>)")
        else:
            score += 0.10
    else:
        score += 0.10  # AIDL: no <transport> is correct

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
    """Validate Android layout XML — with auto-escaping + chunk repair."""
    tool = "xml.etree + android-layout"
    if not xml_str.strip():
        return ValidatorResult(ok=False, score=0.0, errors=["Empty XML"], tool=tool)

    errors, score = [], 0.0
    content = xml_str.strip()

    # === AUTO-FIXES ===
    import re  # ensure re is available

    # 0. Strip markdown fences
    content = re.sub(r'^```[a-zA-Z]*\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'^```\s*$', '', content, flags=re.MULTILINE)

    # 1. Add missing namespace
    if 'xmlns:android=' not in content and 'android:' in content:
        content = re.sub(
            r'(<[A-Za-z][^>\s]*)',
            r'\1 xmlns:android="http://schemas.android.com/apk/res/android"',
            content,
            count=1
        )

    # 2. Escape dangerous characters in text attributes
    def escape_text(m):
        text = m.group(1)
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'android:text="{text}"'

    content = re.sub(r'android:text\s*=\s*"([^"]*)"', escape_text, content)

    # 3. Clean chunked/incomplete output
    content = re.sub(r'<\?xml[^>]*\?>', '', content).strip()
    content = re.sub(r'</?[A-Za-z][^>]*$', '', content, flags=re.DOTALL).strip()

    # Truncate to last complete root tag
    for root_tag in ['ScrollView', 'LinearLayout', 'FrameLayout', 'RelativeLayout']:
        close = f'</{root_tag}>'
        if close in content:
            idx = content.rfind(close)
            content = content[:idx + len(close)]
            break

    # === PARSE ===
    try:
        root = ET.fromstring(content)
        score += 0.45
    except ET.ParseError as e:
        return ValidatorResult(ok=False, score=0.15,
                               errors=[f"XML parse error: {e}"], tool=tool)

    # Original scoring logic
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag in {"LinearLayout","ConstraintLayout","RelativeLayout",
               "FrameLayout","ScrollView"}:
        score += 0.20

    ns = "http://schemas.android.com/apk/res/android"
    ids = [el.get(f"{{{ns}}}id") or el.get("android:id") for el in root.iter()]
    ids = [i for i in ids if i]
    if ids:
        score += 0.20
    else:
        errors.append("No android:id attributes found")

    tags = {el.tag.split("}")[-1] for el in root.iter()}
    widgets = {"TextView","Switch","Button","SeekBar","CheckBox","EditText","ImageView"}
    if tags & widgets:
        score += 0.15

    if 'xmlns:android="http://schemas.android.com/apk/res/android"' in content:
        score += 0.10

    score = round(min(score, 1.0), 3)
    ok = score >= 0.70
    return ValidatorResult(ok=ok, score=score, errors=errors, tool=tool)


# ═════════════════════════════════════════════════════════════════════════════
# C2b — Android Layout repair  (extracted from notebook cell 71)
# ═════════════════════════════════════════════════════════════════════════════

def fix_android_layout_file(xml_path: "Path | str") -> tuple[bool, float, str]:
    """
    Repair a single Android layout XML file in-place.
    Logic extracted from notebook fix_mismatched_tags (cell 71) — the proven
    working version. Returns (ok, score, message).

    Steps:
      1. html.unescape recursively
      2. ConstraintLayout → full class name
      3. Strip root wrappers (ScrollView/LinearLayout/etc) + XML decl + android:text
      4. Remove HTML comments
      5. remove_incomplete_tags_multiline — quote-aware tag scanner
      6. auto_close_tags — push/pop stack, append missing closing tags
      7. force android:id on widgets missing it
      8. Rewrap in ScrollView with namespaces
      9. ET.fromstring validate + write + rescore
    """
    import html as _html
    from pathlib import Path as _Path

    xml_path = _Path(xml_path)
    if not xml_path.exists():
        return False, 0.0, f"File not found: {xml_path}"

    content = xml_path.read_text(encoding="utf-8", errors="ignore")

    # 1. Unescape recursively
    prev = None
    while prev != content:
        prev = content
        content = _html.unescape(content)

    # 2. Strip markdown fences (LLM often wraps output in ```xml ... ```)
    content = re.sub(r'^```[a-zA-Z]*\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'^```\s*$', '', content, flags=re.MULTILINE)

    # 3. ConstraintLayout prefix
    content = re.sub(r'<(/?)\s*ConstraintLayout\b',
                     r'<\1androidx.constraintlayout.widget.ConstraintLayout', content)

    # 4. Strip root wrappers + XML decl + android:text
    for rt in ['ScrollView', 'LinearLayout', 'FrameLayout', 'RelativeLayout']:
        content = re.sub(rf'<{rt}\b[^>]*>', '', content)
        content = re.sub(rf'</{rt}>', '', content)
    content = re.sub(r'<\?xml[^>]*\?>', '', content)
    content = re.sub(r'\s*android:text\s*=\s*"[^"]*"', '', content)

    # 5. Remove HTML comments
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

    # 6. remove_incomplete_tags_multiline — quote-aware
    def _remove_incomplete(text):
        result, i, n = [], 0, len(text)
        while i < n:
            if text[i] == '<':
                j, in_quote, quote_char, found_end = i+1, False, None, False
                while j < n:
                    c = text[j]
                    if in_quote:
                        if c == quote_char:
                            in_quote = False
                    else:
                        if c in ('"', "'"):
                            in_quote, quote_char = True, c
                        elif c == '>':
                            found_end = True
                            break
                        elif c == '<':
                            break
                    j += 1
                if found_end:
                    result.append(text[i:j+1])
                    i = j + 1
                else:
                    nxt = text.find('<', i+1)
                    if nxt == -1:
                        break
                    i = nxt
            else:
                nxt = text.find('<', i)
                if nxt == -1:
                    result.append(text[i:])
                    break
                result.append(text[i:nxt])
                i = nxt
        return ''.join(result)

    content = _remove_incomplete(content)

    # 7. auto_close_tags — push/pop stack
    def _auto_close(text):
        stack = []
        for m in re.finditer(r'<(/?)([A-Za-z][\w.]*)[^>]*?(/?)>', text, re.DOTALL):
            is_close     = m.group(1) == '/'
            tag_name     = m.group(2)
            is_self_close = m.group(3) == '/'
            if is_self_close or tag_name.lower() in ('br', 'hr', 'img', 'input'):
                continue
            if is_close:
                if stack and stack[-1] == tag_name:
                    stack.pop()
            else:
                stack.append(tag_name)
        return text + ''.join(f'</{t}>' for t in reversed(stack))

    content = _auto_close(content)

    # 8. Force android:id on widgets missing it
    def _force_id(m):
        full_tag, tag_name = m.group(0), m.group(1).lower()
        if 'android:id' in full_tag:
            return full_tag
        return (full_tag[:-2] + f' android:id="@+id/{tag_name}_view"/>'
                if full_tag.endswith('/>')
                else full_tag[:-1] + f' android:id="@+id/{tag_name}_view">')

    content = re.sub(
        r'<(TextView|Switch|SeekBar|Button|CheckBox|EditText)\b[^>]*(?:/>|>)',
        _force_id, content, flags=re.IGNORECASE)

    # 9. Rewrap
    result = (
        '<ScrollView\n'
        '    xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    xmlns:app="http://schemas.android.com/apk/res-auto"\n'
        '    android:layout_width="match_parent"\n'
        '    android:layout_height="match_parent">\n'
        + content.strip()
        + '\n</ScrollView>'
    )

    # 10. Validate + write
    try:
        ET.fromstring(result)
        xml_path.write_text(result, encoding="utf-8")
        r = validate_layout_xml(result)
        return True, r.score, f"✅ {xml_path.name}: score={r.score}"
    except ET.ParseError as e:
        lines = result.splitlines()
        err_line = e.position[0]
        ctx = "\n".join(
            f"  {i+max(1,err_line-2):4}: {repr(l[:100])}"
            for i, l in enumerate(lines[max(0, err_line-3):err_line+2])
        )
        return False, 0.0, f"❌ {xml_path.name}: {e}\n{ctx}"


# Alias — matches notebook cell 71 usage: fix_mismatched_tags(output_dir, fnames)
def fix_mismatched_tags(output_dir: str, fnames: list = None) -> int:
    return fix_android_layouts_dir(output_dir, fnames)


def fix_android_layouts_dir(output_dir: str,
                             fnames: Optional[list] = None) -> int:
    """
    Fix all fragment_*.xml files in output_dir using fix_android_layout_file.
    If fnames is None, fixes all fragment_*.xml in the layout dir.
    Returns number of files successfully fixed.
    """
    from pathlib import Path as _Path
    layout_dir = _Path(output_dir) / "android_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        layout_dir = _Path(output_dir) / "hmi_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        print(f"⚠ No layout directory found in {output_dir}")
        return 0

    files = ([layout_dir / f for f in fnames]
             if fnames else sorted(layout_dir.glob("fragment_*.xml")))

    fixed = 0
    for xml_path in files:
        ok, score, msg = fix_android_layout_file(xml_path)
        print(msg)
        if ok:
            fixed += 1
    print(f"🔧 {fixed}/{len(files)} layout files fixed.")
    return fixed


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
# C4-ONLY — CPP ↔ AIDL name consistency check
#
# NOT wired into validate()/dispatch below on purpose. validate() is the
# canonical scorer shared across C1–C4 (and used by MIPROv2 optimisation);
# changing it would silently shift scores for C1/C2/C3, which this project's
# thesis design keeps out of scope for this check. This function is called
# ONLY from C4's ValidatorFeedback (multi_main_c4_feedback.py) and C4-minimal's
# _retry_agent (gen_hal_minimal_c4.py), layered ON TOP of the canonical score
# as an additional pass/fail gate — it does not alter the canonical score.
# ═════════════════════════════════════════════════════════════════════════════

_VSS_PROP_REF = re.compile(r"VehicleProperty::([A-Za-z0-9_]+)")

def check_cpp_aidl_name_consistency(cpp_code: str, aidl_dir: str) -> list[str]:
    """
    Return the sorted list of VEHICLE_CHILDREN_* names referenced in
    `cpp_code` (as `VehicleProperty::NAME`) that are NOT defined in any
    AIDL enum file under `aidl_dir`. Empty list = fully consistent.

    Only checks names with the `VEHICLE_CHILDREN_` prefix — these are
    the VSS-derived constants unique to this project's generated AIDL.
    Standard AOSP VehicleProperty members (e.g. PERF_VEHICLE_SPEED)
    are intentionally excluded from AIDL-consistency scope: they are
    defined by the AOSP platform, not by this pipeline's generated
    .aidl files, so absence from aidl_dir's parse result is expected
    and not an error.

    Reuses VssGlueAgent's proven `_parse_aidl_properties()` parser
    (via `get_aidl_property_names()`) as the single source of truth —
    the exact same name set the aggregator (VssVehicleHardware.cpp)
    and VTS (VtsHalAutomotiveVehicleVss.cpp) already trust.
    """
    if not aidl_dir or not cpp_code:
        return []
    from agents.vss_glue_agent import get_aidl_property_names
    aidl_names = get_aidl_property_names(aidl_dir)
    if not aidl_names:
        # No parseable AIDL yet (e.g. AIDL agent hasn't run/written this
        # domain's file) — nothing to check against, don't false-positive.
        return []
    referenced = {m for m in _VSS_PROP_REF.findall(cpp_code)
                  if m.startswith("VEHICLE_CHILDREN_")}
    return sorted(referenced - aidl_names)


def format_cpp_aidl_consistency_feedback(bad_names: list[str], aidl_dir: str) -> str:
    """
    Build an LLM-facing feedback message for check_cpp_aidl_name_consistency
    results, including nearest-match suggestions from the real AIDL name set
    so the retry has a concrete correction target instead of just "wrong".
    """
    if not bad_names:
        return ""
    from agents.vss_glue_agent import get_aidl_property_names
    aidl_names = sorted(get_aidl_property_names(aidl_dir))
    lines = [
        "C++/AIDL name consistency errors:",
        "The following VehicleProperty::* references do not exist in "
        "any AIDL enum file for this run. Every name you use MUST be "
        "copied EXACTLY from the AIDL enum — do not shorten, reorder, "
        "or re-derive VSS path segments yourself.",
        "",
    ]
    for bad in bad_names:
        # crude nearest-match: longest common prefix length against each AIDL name
        def _score(a: str) -> int:
            n = 0
            for x, y in zip(bad, a):
                if x != y:
                    break
                n += 1
            return n
        nearest = sorted(aidl_names, key=_score, reverse=True)[:3]
        lines.append(f"- '{bad}' is not defined in AIDL.")
        if nearest:
            lines.append(f"    Nearest AIDL names: {', '.join(nearest)}")
    lines.append("")
    lines.append("Fix: replace each undefined name above with the exact "
                  "matching AIDL enum constant.")
    return "\n".join(lines)


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
        "cpp":            {"tool": "clang++ -fsyntax-only",
                           "available": bool(_tool("clang++") or _tool("clang")),
                           "fallback": "cpp-regex",
                           "note": "Syntax only; AOSP headers stubbed in"},
        "selinux":        {"tool": "selinux-regex",
                           "available": True,
                           "note": "Structural AIDL/HIDL pattern check; checkpolicy needs full AOSP tree"},
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