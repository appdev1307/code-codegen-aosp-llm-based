"""
dspy_opt/metrics.py
═══════════════════════════════════════════════════════════════════════════════
Compile-aware evaluation metrics for all HAL generation agents.

Each metric blends three independently-scored components:

  score = w_struct * structural  +  w_syntax * syntax_valid  +  w_cov * coverage

  structural   : keyword/pattern heuristics (fast, always runs)
  syntax_valid : real validator from validators.py (parser/compiler per type)
  coverage     : fraction of input VSS signal names present in output

Weight rationale — syntax_valid is weighted highest for types where the
validator is authoritative (checkpolicy for SELinux, ast.parse for Python,
xml.etree for XML); lower for types with only a fallback parser available:

  Agent             struct  syntax  coverage  Primary validator
  ─────────────────────────────────────────────────────────────────────────────
  aidl              0.30    0.50    0.20      Python AIDL grammar parser
  cpp               0.35    0.45    0.20      clang++ --syntax-only (stub hdrs)
  selinux           0.25    0.65    0.10      checkpolicy (native Linux, full)
  build             0.35    0.55    0.10      Python Android.bp JSON5 parser
  vintf             0.30    0.60    0.10      xml.etree (always available)
  design_doc        0.50    0.30    0.20      markdown structure check
  puml              0.40    0.40    0.20      plantuml.jar or regex fallback
  android_app       0.30    0.40    0.30      kotlinc (Android API refs filtered)
  android_layout    0.30    0.50    0.20      xml.etree (always available)
  backend           0.25    0.50    0.25      ast.parse (full Python AST)
  backend_model     0.25    0.50    0.25      ast.parse (full Python AST)
  simulator         0.35    0.45    0.20      ast.parse (full Python AST)

Thesis framing:
  "Generated code is scored along three dimensions: structural completeness
   (required syntactic elements), syntactic validity (verified using the
   highest-fidelity tool available per output type — from full SELinux policy
   compilation to Python AST parsing), and signal coverage (fraction of
   input VSS signals represented in output). Tool availability is reported
   in Table X via validators.print_availability_report()."
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re

from dspy_opt.validators import validate, ValidatorResult


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_output(prediction, field: str) -> str:
    value = getattr(prediction, field, None)
    return str(value).strip() if value else ""


def _heuristic(checks: list[tuple[bool, float]]) -> float:
    """Weighted heuristic score → [0, 1]."""
    total  = sum(w for _, w in checks)
    earned = sum(w for cond, w in checks if cond)
    return round(earned / total, 4) if total else 0.0


def _balanced_braces(text: str) -> bool:
    return (text.count("{") == text.count("}")
            and text.count("[") == text.count("]")
            and text.count("(") == text.count(")"))


def _signal_coverage(example, code: str) -> float:
    """
    Fraction of expected VSS property short-names found in generated code.
    e.g. 'VEHICLE_ADAS_ABS_ISENABLED' → looks for 'ISENABLED' in code.
    Returns 1.0 if no ground-truth properties are available.
    """
    props_text = getattr(example, "properties", "") if example else ""
    if not props_text:
        return 1.0
    names = re.findall(r"Name:\s*\S+_(\w+)", props_text)
    if not names:
        return 1.0
    code_lower = code.lower()
    covered    = sum(1 for n in names if n.lower() in code_lower)
    return round(covered / len(names), 4)


def _blend(
    structural: float,
    syntax_res: ValidatorResult,
    coverage:   float,
    w_struct:   float,
    w_syntax:   float,
    w_cov:      float,
) -> float:
    """
    Weighted blend of the three score components.
    Uses syntax_res.score (not just .ok) so partial credit flows through
    when a validator finds errors but not a total failure.
    """
    return round(
        w_struct * structural
        + w_syntax * syntax_res.score
        + w_cov   * coverage,
        4,
    )


def _make_pred(field: str, code: str):
    """Build a minimal prediction-like object for score_file()."""
    class _P:
        pass
    p = _P()
    setattr(p, field, code)
    return p


# Verbose logging — set True to see per-call breakdown during optimisation
_METRICS_VERBOSE = False

def _log(agent_type: str, structural: float, syntax_res: ValidatorResult,
         coverage: float, final: float) -> None:
    if not _METRICS_VERBOSE:
        return
    s = "✓" if syntax_res.ok else "✗"
    e = f" — {syntax_res.errors[0][:55]}" if syntax_res.errors else ""
    print(f"  [metric:{agent_type:<15}] "
          f"struct={structural:.3f}  "
          f"syntax={syntax_res.score:.3f}({s},{syntax_res.tool}){e}  "
          f"cov={coverage:.3f}  → {final:.3f}")


# ═════════════════════════════════════════════════════════════════════════════
# A. HAL LAYER METRICS
# ═════════════════════════════════════════════════════════════════════════════

def metric_aidl(example, prediction, trace=None) -> float:
    """AIDL — Python grammar parser (host-native, always available)."""
    code = _get_output(prediction, "aidl_code")
    if not code:
        return 0.0
    structural = _heuristic([
        ("package "        in code,                                          0.20),
        ("interface "      in code,                                          0.20),
        (_balanced_braces(code),                                             0.15),
        (any(t in code for t in ["boolean","int","float","String","byte[]"]),0.20),
        (any(k in code for k in ["void ","oneway ","ParcelableHolder"]),     0.15),
        ("@VintfStability" in code,                                          0.10),
    ])
    syntax_res = validate("aidl", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.30, 0.50, 0.20)
    _log("aidl", structural, syntax_res, coverage, score)
    return score


def metric_cpp(example, prediction, trace=None) -> float:
    """C++ VHAL — clang++ --syntax-only with stub AOSP headers (or regex)."""
    code = _get_output(prediction, "cpp_code")
    if not code:
        return 0.0
    structural = _heuristic([
        ("#include"   in code,                                               0.15),
        ("namespace"  in code,                                               0.10),
        ("class "     in code,                                               0.15),
        (_balanced_braces(code),                                             0.15),
        (any(m in code for m in
             ["getAllPropertyConfigs","getValues","setValues"]),              0.30),
        (any(t in code for t in
             ["int32_t","float","bool","VehiclePropValue"]),                  0.15),
    ])
    syntax_res = validate("cpp", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.35, 0.45, 0.20)
    _log("cpp", structural, syntax_res, coverage, score)
    return score


def metric_selinux(example, prediction, trace=None) -> float:
    """
    SELinux .te — checkpolicy (native Linux tool, full policy compile).
    syntax_valid weighted highest (0.65) because checkpolicy is authoritative.
    """
    policy = _get_output(prediction, "policy")
    if not policy:
        return 0.0
    structural = _heuristic([
        ("type "  in policy,                                                 0.20),
        ("allow " in policy,                                                 0.30),
        (any(k in policy for k in
             ["hal_vehicle","vhal","hal_attribute"]),                         0.25),
        (any(k in policy for k in
             ["binder_call","hwservice_use","add_hwservice"]),                0.25),
    ])
    syntax_res = validate("selinux", policy)
    coverage   = _signal_coverage(example, policy)
    score      = _blend(structural, syntax_res, coverage, 0.25, 0.65, 0.10)
    _log("selinux", structural, syntax_res, coverage, score)
    return score


def metric_build(example, prediction, trace=None) -> float:
    """Android.bp — Python JSON5 parser (Soong not available on host)."""
    bp = _get_output(prediction, "build_file")
    if not bp:
        return 0.0
    structural = _heuristic([
        (any(b in bp for b in
             ["aidl_interface","cc_binary","cc_library_shared","cc_library"]),0.30),
        ("name:"        in bp,                                               0.20),
        ("srcs:"        in bp,                                               0.15),
        ("vendor: true" in bp or "vendor:" in bp,                            0.20),
        (_balanced_braces(bp),                                                0.15),
    ])
    syntax_res = validate("build", bp)
    coverage   = _signal_coverage(example, bp)
    score      = _blend(structural, syntax_res, coverage, 0.35, 0.55, 0.10)
    _log("build", structural, syntax_res, coverage, score)
    return score


def metric_vintf(example, prediction, trace=None) -> float:
    """VINTF XML + init.rc — xml.etree (Python stdlib, always available)."""
    manifest = _get_output(prediction, "manifest")
    if not manifest:
        return 0.0
    structural = _heuristic([
        ("<hal"        in manifest,                                          0.25),
        ("<n>"      in manifest,                                          0.20),
        ("<transport>" in manifest or "transport" in manifest,               0.20),
        ("service "    in manifest,                                          0.20),
        ("class hal"   in manifest or "user " in manifest,                   0.15),
    ])
    syntax_res = validate("vintf", manifest)
    coverage   = _signal_coverage(example, manifest)
    score      = _blend(structural, syntax_res, coverage, 0.30, 0.60, 0.10)
    _log("vintf", structural, syntax_res, coverage, score)
    return score


# ═════════════════════════════════════════════════════════════════════════════
# B. DESIGN LAYER METRICS
# ═════════════════════════════════════════════════════════════════════════════

def metric_design_doc(example, prediction, trace=None) -> float:
    """Design document — markdown structure check."""
    doc = _get_output(prediction, "design_doc")
    if not doc:
        return 0.0
    required = ["overview","architecture","propert","security","build","data flow"]
    section_score = sum(1 for s in required if s.lower() in doc.lower()) / len(required)
    structural = round(
        0.50 * _heuristic([
            ("## " in doc or "# " in doc,                                    0.25),
            ("|" in doc,                                                     0.20),
            (len(doc.splitlines()) >= 20,                                    0.25),
            (len(doc) >= 500,                                                0.30),
        ])
        + 0.50 * section_score, 4,
    )
    syntax_res = validate("design_doc", doc)
    coverage   = _signal_coverage(example, doc)
    score      = _blend(structural, syntax_res, coverage, 0.50, 0.30, 0.20)
    _log("design_doc", structural, syntax_res, coverage, score)
    return score


def metric_puml(example, prediction, trace=None) -> float:
    """PlantUML — plantuml.jar -syntax if available, regex fallback."""
    puml = _get_output(prediction, "puml")
    if not puml:
        return 0.0
    structural = _heuristic([
        ("@startuml" in puml,                                                0.25),
        ("@enduml"   in puml,                                                0.25),
        (any(a in puml for a in ["->","-->","<--","=>"]),                    0.25),
        (any(c in puml for c in
             ["component","package","node","rectangle","class"]),             0.25),
    ])
    syntax_res = validate("puml", puml)
    coverage   = _signal_coverage(example, puml)
    score      = _blend(structural, syntax_res, coverage, 0.40, 0.40, 0.20)
    _log("puml", structural, syntax_res, coverage, score)
    return score


# ═════════════════════════════════════════════════════════════════════════════
# C. ANDROID APP LAYER METRICS
# ═════════════════════════════════════════════════════════════════════════════

def metric_kotlin(example, prediction, trace=None) -> float:
    """Kotlin Fragment — kotlinc syntax check (Android API refs filtered)."""
    code = _get_output(prediction, "kotlin_code")
    if not code:
        return 0.0
    structural = _heuristic([
        (any(c in code for c in
             ["CarPropertyManager","Car.createCar","Car.CAR_"]),              0.25),
        ("Fragment"    in code,                                              0.15),
        (any(m in code for m in
             ["onViewCreated","onCreateView","onResume"]),                    0.15),
        (any(cb in code for cb in
             ["CarPropertyEventCallback","onChangeEvent","registerCallback"]),0.20),
        ("fun "        in code,                                              0.10),
        (_balanced_braces(code),                                             0.15),
    ])
    syntax_res = validate("android_app", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.30, 0.40, 0.30)
    _log("android_app", structural, syntax_res, coverage, score)
    return score


def metric_layout_xml(example, prediction, trace=None) -> float:
    """Android layout XML — xml.etree (always available)."""
    xml = _get_output(prediction, "layout_xml")
    if not xml:
        return 0.0
    open_tags  = len(re.findall(r"<[A-Za-z]", xml))
    close_tags = len(re.findall(r"</[A-Za-z]|/>", xml))
    structural = _heuristic([
        (any(r in xml for r in
             ["LinearLayout","ConstraintLayout","RelativeLayout","ScrollView"]),0.25),
        ("android:id=" in xml,                                               0.25),
        (any(v in xml for v in
             ["TextView","Switch","Button","SeekBar","CheckBox"]),            0.25),
        (abs(open_tags - close_tags) <= 2,                                   0.25),
    ])
    syntax_res = validate("android_layout", xml)
    coverage   = _signal_coverage(example, xml)
    score      = _blend(structural, syntax_res, coverage, 0.30, 0.50, 0.20)
    _log("android_layout", structural, syntax_res, coverage, score)
    return score


# ═════════════════════════════════════════════════════════════════════════════
# D. BACKEND LAYER METRICS
# ═════════════════════════════════════════════════════════════════════════════

def metric_backend_api(example, prediction, trace=None) -> float:
    """FastAPI server — ast.parse (full Python AST, always available)."""
    code = _get_output(prediction, "api_code")
    if not code:
        return 0.0
    structural = _heuristic([
        (any(f in code for f in ["FastAPI","fastapi"]),                      0.20),
        ("async def"   in code,                                              0.20),
        (any(r in code for r in
             ["@app.get","@app.post","@app.put",
              "@router.get","@router.post"]),                                 0.25),
        (any(m in code for m in ["BaseModel","pydantic"]),                   0.10),
        (any(u in code for u in ["/health","/properties","websocket"]),      0.15),
        (_balanced_braces(code),                                              0.10),
    ])
    syntax_res = validate("backend", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.25, 0.50, 0.25)
    _log("backend", structural, syntax_res, coverage, score)
    return score


def metric_backend_models(example, prediction, trace=None) -> float:
    """Pydantic models — ast.parse."""
    code = _get_output(prediction, "models_code")
    if not code:
        return 0.0
    structural = _heuristic([
        (any(b in code for b in ["BaseModel","pydantic"]),                   0.30),
        ("class "      in code,                                              0.25),
        (any(t in code for t in ["bool","float","int","str","Optional"]),    0.25),
        ("Field("      in code or ": " in code,                              0.20),
    ])
    syntax_res = validate("backend_model", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.25, 0.50, 0.25)
    _log("backend_model", structural, syntax_res, coverage, score)
    return score


def metric_simulator(example, prediction, trace=None) -> float:
    """Simulator — ast.parse."""
    code = _get_output(prediction, "simulator_code")
    if not code:
        return 0.0
    structural = _heuristic([
        ("class "      in code,                                              0.20),
        ("def "        in code,                                              0.10),
        (any(a in code for a in ["async def","asyncio","await"]),            0.25),
        (any(r in code for r in ["random","randint","uniform","choice"]),    0.25),
        (any(s in code for s in ["start","stop","run"]),                     0.20),
    ])
    syntax_res = validate("simulator", code)
    coverage   = _signal_coverage(example, code)
    score      = _blend(structural, syntax_res, coverage, 0.35, 0.45, 0.20)
    _log("simulator", structural, syntax_res, coverage, score)
    return score


# ═════════════════════════════════════════════════════════════════════════════
# Registry and helpers
# ═════════════════════════════════════════════════════════════════════════════

METRIC_REGISTRY: dict[str, callable] = {
    "aidl":           metric_aidl,
    "cpp":            metric_cpp,
    "selinux":        metric_selinux,
    "build":          metric_build,
    "vintf":          metric_vintf,
    "design_doc":     metric_design_doc,
    "puml":           metric_puml,
    "android_app":    metric_kotlin,
    "android_layout": metric_layout_xml,
    "backend":        metric_backend_api,
    "backend_model":  metric_backend_models,
    "simulator":      metric_simulator,
}

_FIELD_MAP = {
    "aidl":           "aidl_code",
    "cpp":            "cpp_code",
    "selinux":        "policy",
    "build":          "build_file",
    "vintf":          "manifest",
    "design_doc":     "design_doc",
    "puml":           "puml",
    "android_app":    "kotlin_code",
    "android_layout": "layout_xml",
    "backend":        "api_code",
    "backend_model":  "models_code",
    "simulator":      "simulator_code",
}


def score_all(example, predictions: dict[str, object]) -> dict[str, float]:
    """Score multiple agent outputs. Returns {agent_type: float}."""
    scores = {}
    for agent_type, prediction in predictions.items():
        fn = METRIC_REGISTRY.get(agent_type)
        scores[agent_type] = fn(example, prediction) if fn else 0.0
    return scores


def score_file(agent_type: str, code: str, example=None) -> float:
    """
    Score a raw code string directly — no DSPy Prediction wrapper needed.
    Used by run_comparison.py when rescoring generated files from disk.

    Example:
        from dspy_opt.metrics import score_file
        s = score_file("selinux", Path("output/vendor_hal.te").read_text())
        print(f"SELinux score: {s:.3f}")
    """
    field = _FIELD_MAP.get(agent_type, agent_type)
    pred  = _make_pred(field, code)
    fn    = METRIC_REGISTRY.get(agent_type)
    return fn(example, pred) if fn else 0.0