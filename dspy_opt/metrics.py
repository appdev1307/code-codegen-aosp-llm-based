"""
dspy_opt/metrics.py
═══════════════════════════════════════════════════════════════════
Evaluation metric functions — one per generation agent output type.

These functions serve two purposes:
  1. MIPROv2 optimisation signal — MIPROv2 calls them during
     optimisation to score candidate prompts and demonstrations
  2. Thesis evaluation metrics — run_comparison.py calls them
     to generate the per-condition comparison table

Each metric follows the DSPy metric signature:
    def metric(example, prediction, trace=None) -> float

    example    : dspy.Example (contains inputs; may have gold outputs
                 if you add them — not required here since we have no
                 ground truth HAL code, only structural checks)
    prediction : dspy.Prediction (output of the module's forward())
    trace      : internal DSPy trace object (ignore in most cases)
    return     : float in [0.0, 1.0], higher = better

Design philosophy:
  Since we have no gold-standard HAL code to compare against, all
  metrics are structural/syntactic validators. They check whether the
  generated code has the correct structure, required keywords, and
  non-trivial content. This is the standard approach for code
  generation evaluation without ground truth.

  Metric weights within each function are tunable — see the
  WEIGHT constants at the top of each function.
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────

def _score(checks: list[tuple[bool, float]]) -> float:
    """
    Weighted score from a list of (condition, weight) tuples.
    Returns float in [0.0, 1.0].

    Example:
        _score([
            ("interface" in code, 0.4),
            ("package"   in code, 0.3),
            (len(code) > 100,     0.3),
        ])
    """
    total_weight = sum(w for _, w in checks)
    earned       = sum(w for cond, w in checks if cond)
    if total_weight == 0:
        return 0.0
    return round(earned / total_weight, 4)


def _get_output(prediction, field: str) -> str:
    """Safely extract a string output field from a DSPy Prediction."""
    value = getattr(prediction, field, None)
    if value is None:
        return ""
    return str(value).strip()


def _balanced_braces(text: str) -> bool:
    """Return True if all brace types are balanced."""
    return (
        text.count("{") == text.count("}")
        and text.count("[") == text.count("]")
        and text.count("(") == text.count(")")
    )


def _signal_coverage(example, code: str) -> float:
    """
    Estimate what fraction of expected VSS property names appear
    in the generated code. Uses the last segment of the property
    path (e.g. 'ISENABLED' from 'VEHICLE_ADAS_ABS_ISENABLED').
    Returns 0.0 if example has no properties field.
    """
    props_text = getattr(example, "properties", "") if example else ""
    if not props_text:
        return 1.0  # no ground truth → don't penalise

    # Extract property short names from "Name: VEHICLE_..._ISENABLED" lines
    names = re.findall(r"Name:\s*\S+_(\w+)", props_text)
    if not names:
        return 1.0

    code_lower  = code.lower()
    covered     = sum(1 for n in names if n.lower() in code_lower)
    return round(covered / len(names), 4)


# ═══════════════════════════════════════════════════════════════════
# A. HAL LAYER METRICS  (5)
# ═══════════════════════════════════════════════════════════════════

def metric_aidl(example, prediction, trace=None) -> float:
    """
    Score an AIDL interface definition.
    Checks: package declaration, interface block, method signatures,
    AIDL types, brace balance, non-trivial length, signal coverage.
    """
    code = _get_output(prediction, "aidl_code")
    if not code:
        return 0.0

    structural = _score([
        ("package "    in code,                                      0.20),
        ("interface "  in code,                                      0.20),
        (_balanced_braces(code),                                     0.15),
        (any(t in code for t in
             ["boolean", "int", "float", "String", "byte[]"]),       0.15),
        (any(k in code for k in
             ["void ", "oneway ", "ParcelableHolder"]),               0.10),
        ("@VintfStability" in code or "@JavaDerive" in code,         0.05),
        (len(code) > 150,                                            0.15),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.70 * structural + 0.30 * coverage, 4)


def metric_cpp(example, prediction, trace=None) -> float:
    """
    Score a VHAL C++ implementation file.
    Checks: includes, namespace, class definition, key VHAL methods,
    property type handling, brace balance.
    """
    code = _get_output(prediction, "cpp_code")
    if not code:
        return 0.0

    structural = _score([
        ("#include"     in code,                                      0.15),
        ("namespace"    in code,                                      0.10),
        ("class "       in code,                                      0.15),
        (_balanced_braces(code),                                      0.15),
        (any(m in code for m in
             ["getAllPropertyConfigs", "getValues", "setValues"]),     0.25),
        (any(t in code for t in
             ["int32_t", "float", "bool", "VehiclePropValue"]),       0.10),
        (len(code) > 300,                                             0.10),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.75 * structural + 0.25 * coverage, 4)


def metric_selinux(example, prediction, trace=None) -> float:
    """
    Score a SELinux .te policy file.
    Checks: type declarations, allow rules, HAL-specific macros,
    binder permissions, non-trivial length.
    """
    policy = _get_output(prediction, "policy")
    if not policy:
        return 0.0

    return _score([
        ("type "        in policy,                                    0.20),
        ("allow "       in policy,                                    0.25),
        (any(k in policy for k in
             ["hal_vehicle", "vhal", "hal_attribute"]),                0.20),
        (any(k in policy for k in
             ["binder_call", "hwservice_use", "add_hwservice"]),       0.20),
        (len(policy.splitlines()) >= 5,                               0.15),
    ])


def metric_build(example, prediction, trace=None) -> float:
    """
    Score an Android.bp build file.
    Checks: correct block types, name field, srcs field,
    vendor partition flag, balanced brackets.
    """
    bp = _get_output(prediction, "build_file")
    if not bp:
        return 0.0

    return _score([
        (any(b in bp for b in
             ["aidl_interface", "cc_binary", "cc_library_shared",
              "cc_library"]),                                          0.25),
        ("name:"        in bp,                                        0.20),
        ("srcs:"        in bp,                                        0.15),
        ("vendor: true" in bp or "vendor:"  in bp,                   0.15),
        (_balanced_braces(bp),                                        0.15),
        (len(bp) > 80,                                                0.10),
    ])


def metric_vintf(example, prediction, trace=None) -> float:
    """
    Score a VINTF manifest XML + init.rc output.
    Checks: HAL XML structure, init.rc service block, transport type,
    name and version fields.
    """
    manifest = _get_output(prediction, "manifest")
    if not manifest:
        return 0.0

    return _score([
        ("<hal"         in manifest,                                  0.20),
        ("<name>"       in manifest,                                  0.15),
        ("<transport>"  in manifest or "transport" in manifest,       0.15),
        ("<version>"    in manifest or "version"   in manifest,       0.10),
        ("service "     in manifest,                                  0.15),  # init.rc
        ("class hal"    in manifest or "user "     in manifest,       0.10),  # init.rc
        (len(manifest)  > 100,                                        0.15),
    ])


# ═══════════════════════════════════════════════════════════════════
# B. DESIGN LAYER METRICS  (2)
# ═══════════════════════════════════════════════════════════════════

def metric_design_doc(example, prediction, trace=None) -> float:
    """
    Score a Markdown design document.
    Checks: Markdown headings, required sections, HAL domain mention,
    table presence, minimum length.
    """
    doc = _get_output(prediction, "design_doc")
    if not doc:
        return 0.0

    # Check for required section headings
    required_sections = [
        "overview", "architecture", "propert", "security",
        "build", "data flow",
    ]
    section_score = sum(
        1 for s in required_sections
        if s.lower() in doc.lower()
    ) / len(required_sections)

    structural = _score([
        ("## "          in doc,                                        0.20),
        ("HAL"          in doc or "hal" in doc.lower(),               0.10),
        ("|"            in doc,                                        0.15),  # table
        (len(doc.splitlines()) >= 20,                                  0.20),
        (len(doc) >= 500,                                              0.15),
    ])

    return round(0.50 * structural + 0.50 * section_score, 4)


def metric_puml(example, prediction, trace=None) -> float:
    """
    Score a PlantUML diagram.
    Checks: startuml/enduml markers, arrows, component definitions,
    non-trivial content.
    """
    puml = _get_output(prediction, "puml")
    if not puml:
        return 0.0

    return _score([
        ("@startuml"    in puml,                                      0.25),
        ("@enduml"      in puml,                                      0.25),
        (any(a in puml for a in ["->", "-->", "<--", "=>"]),          0.20),
        (any(c in puml for c in
             ["component", "package", "node", "rectangle", "class"]), 0.15),
        (len(puml.splitlines()) >= 8,                                  0.15),
    ])


# ═══════════════════════════════════════════════════════════════════
# C. ANDROID APP LAYER METRICS  (2)
# ═══════════════════════════════════════════════════════════════════

def metric_kotlin(example, prediction, trace=None) -> float:
    """
    Score an Android Automotive Kotlin Fragment.
    Checks: Car API usage, Fragment structure, lifecycle methods,
    property event callbacks, error handling, length.
    """
    code = _get_output(prediction, "kotlin_code")
    if not code:
        return 0.0

    structural = _score([
        (any(c in code for c in
             ["CarPropertyManager", "Car.createCar", "Car.CAR_"]),     0.25),
        ("Fragment"     in code,                                       0.15),
        (any(m in code for m in
             ["onViewCreated", "onCreateView", "onResume"]),            0.15),
        (any(cb in code for cb in
             ["CarPropertyEventCallback", "onChangeEvent",
              "registerCallback"]),                                     0.15),
        ("fun "         in code,                                       0.10),
        (_balanced_braces(code),                                       0.10),
        (len(code) > 250,                                              0.10),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.70 * structural + 0.30 * coverage, 4)


def metric_layout_xml(example, prediction, trace=None) -> float:
    """
    Score an Android layout XML file.
    Checks: XML declaration or root layout, android:id attributes,
    view widgets, balanced tags.
    """
    xml = _get_output(prediction, "layout_xml")
    if not xml:
        return 0.0

    # Count opening vs closing tags (rough balance check)
    open_tags  = len(re.findall(r"<[A-Za-z]",  xml))
    close_tags = len(re.findall(r"</[A-Za-z]|/>", xml))
    tag_balance = abs(open_tags - close_tags) <= 2

    return _score([
        (any(r in xml for r in
             ["LinearLayout", "ConstraintLayout",
              "RelativeLayout", "ScrollView"]),                         0.20),
        ("android:id="  in xml,                                        0.20),
        (any(v in xml for v in
             ["TextView", "Switch", "Button",
              "SeekBar", "CheckBox"]),                                  0.20),
        (tag_balance,                                                   0.20),
        (len(xml) > 150,                                               0.20),
    ])


# ═══════════════════════════════════════════════════════════════════
# D. BACKEND LAYER METRICS  (3)
# ═══════════════════════════════════════════════════════════════════

def metric_backend_api(example, prediction, trace=None) -> float:
    """
    Score a FastAPI REST server implementation.
    Checks: FastAPI usage, async endpoints, route decorators,
    Pydantic models, utility endpoints, length.
    """
    code = _get_output(prediction, "api_code")
    if not code:
        return 0.0

    structural = _score([
        (any(f in code for f in ["FastAPI", "fastapi"]),              0.20),
        ("async def"    in code,                                      0.15),
        (any(r in code for r in
             ["@app.get", "@app.post", "@app.put",
              "@router.get", "@router.post"]),                         0.20),
        (any(m in code for m in
             ["BaseModel", "pydantic"]),                               0.10),
        (any(u in code for u in
             ["/health", "/properties", "websocket"]),                 0.15),
        (_balanced_braces(code),                                       0.10),
        (len(code) > 250,                                              0.10),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.75 * structural + 0.25 * coverage, 4)


def metric_backend_models(example, prediction, trace=None) -> float:
    """
    Score Pydantic model definitions for HAL properties.
    Checks: BaseModel usage, class definitions, type annotations,
    Field usage, non-trivial length.
    """
    code = _get_output(prediction, "models_code")
    if not code:
        return 0.0

    structural = _score([
        (any(b in code for b in ["BaseModel", "pydantic"]),           0.25),
        ("class "       in code,                                      0.20),
        (any(t in code for t in
             ["bool", "float", "int", "str", "Optional"]),             0.20),
        ("Field("       in code or ": "    in code,                   0.15),
        (len(code) > 80,                                              0.20),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.70 * structural + 0.30 * coverage, 4)


def metric_simulator(example, prediction, trace=None) -> float:
    """
    Score a Python HAL property simulator.
    Checks: class definition, async usage, realistic value generation
    (random/sleep), start/stop interface, length.
    """
    code = _get_output(prediction, "simulator_code")
    if not code:
        return 0.0

    structural = _score([
        ("class "       in code,                                      0.20),
        ("def "         in code,                                      0.10),
        (any(a in code for a in
             ["async def", "asyncio", "await"]),                       0.20),
        (any(r in code for r in
             ["random", "randint", "uniform", "choice"]),              0.20),
        (any(s in code for s in ["start", "stop", "run"]),            0.15),
        (len(code) > 150,                                              0.15),
    ])

    coverage = _signal_coverage(example, code)
    return round(0.70 * structural + 0.30 * coverage, 4)


# ═══════════════════════════════════════════════════════════════════
# Master registry
# Maps agent_type → metric function
# Used by: optimizer.py, run_comparison.py, _generate_one_module()
# ═══════════════════════════════════════════════════════════════════

METRIC_REGISTRY: dict[str, callable] = {
    # HAL layer
    "aidl":           metric_aidl,
    "cpp":            metric_cpp,
    "selinux":        metric_selinux,
    "build":          metric_build,
    "vintf":          metric_vintf,
    # Design layer
    "design_doc":     metric_design_doc,
    "puml":           metric_puml,
    # App layer
    "android_app":    metric_kotlin,
    "android_layout": metric_layout_xml,
    # Backend layer
    "backend":        metric_backend_api,
    "backend_model":  metric_backend_models,
    "simulator":      metric_simulator,
}


def score_all(example, predictions: dict[str, object]) -> dict[str, float]:
    """
    Convenience function — score multiple agent outputs at once.

    Parameters
    ----------
    example     : dspy.Example or None
    predictions : dict mapping agent_type → prediction object

    Returns
    -------
    dict mapping agent_type → score (float 0-1)

    Example
    -------
        scores = score_all(example, {
            "aidl":    aidl_result,
            "selinux": selinux_result,
        })
        print(scores)  # {"aidl": 0.82, "selinux": 0.75}
    """
    scores = {}
    for agent_type, prediction in predictions.items():
        metric_fn = METRIC_REGISTRY.get(agent_type)
        if metric_fn is None:
            scores[agent_type] = 0.0
        else:
            try:
                scores[agent_type] = metric_fn(example, prediction)
            except Exception as e:
                scores[agent_type] = 0.0
    return scores