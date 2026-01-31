from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import yaml  # PyYAML

from schemas.hal_spec import HalSpec, PropertySpec, Domain, PropType, Access

# ----------------------------
# LLM output cleanup helpers
# ----------------------------
_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_yaml_text(text: str) -> str:
    """
    Extract clean YAML content from LLM output, preferring:
    1. Content inside ```yaml ... ``` fences
    2. First YAML document if --- present
    3. Return as-is if it looks like clean YAML (no heuristic filtering needed)
    4. Heuristic filtering of YAML-looking lines (stops at first non-YAML prose)
    """
    if not text:
        return ""

    s = text.strip()

    # 1. Prefer fenced block
    m = _FENCE_RE.search(s)
    if m:
        return m.group(1).strip()

    # 2. If multi-doc marker, take from first ---
    if "---" in s:
        idx = s.find("---")
        return s[idx:].strip()

    # 3. If it starts with YAML structure markers, assume it's clean YAML
    # (this avoids overly aggressive heuristic filtering)
    first_line = s.split('\n', 1)[0].strip()
    if first_line and (
        ':' in first_line or  # YAML key-value
        first_line.startswith('-') or  # YAML list
        first_line.startswith('#')  # YAML comment
    ):
        # Looks like clean YAML, return as-is
        return s

    # 4. Heuristic: keep only YAML-ish lines, stop at prose
    lines = s.splitlines()
    kept: List[str] = []
    started = False
    yaml_line_patterns = [
        re.compile(r"^\s*[A-Za-z0-9_.\-/]+\s*:\s*(?:#.*)?$"),  # key:
        re.compile(r"^\s*[A-Za-z0-9_.\-/]+\s*:\s+.+$"),         # key: value
        re.compile(r"^\s*-\s+.+$"),                             # - item
        re.compile(r"^\s*#"),                                   # comment
    ]

    for line in lines:
        raw = line.rstrip("\n")
        t = raw.strip()

        if not t or yaml_line_patterns[3].match(t):  # blank or comment
            if started:
                kept.append(raw)
            continue

        is_yaml_like = any(pat.match(raw) for pat in yaml_line_patterns[:3])
        if is_yaml_like:
            kept.append(raw)
            started = True
            continue

        if started:
            # Stop capturing once we hit non-YAML after starting
            break

    return "\n".join(kept).strip()


def _require(d: Dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ValueError(f"[YAML SPEC ERROR] Missing required key '{key}' in {ctx}")
    return d[key]


def _aaos_to_aosp_level(android_str: str | None) -> int:
    s = (android_str or "").strip().upper()
    if s in ("AAOS", "AOSP", "ANDROID"):
        return 14
    if s.startswith(("AAOS_", "AOSP_")):
        s = re.sub(r"^(AAOS|AOSP)_", "", s, 1)
    try:
        return int(s)
    except (ValueError, TypeError):
        raise ValueError(f"[YAML SPEC ERROR] Invalid product.android value: {android_str!r}")


def _normalize_domain(module: str | None) -> Domain:
    m = (module or "").strip().upper()
    mapping = {
        "HVAC": "HVAC",
        "CLIMATE": "HVAC",
        "ADAS": "ADAS",
        "MEDIA": "MEDIA",
        "AUDIO": "MEDIA",
        "POWER": "POWER",
        "BMS": "POWER",
        "BATTERY": "POWER",
    }
    for k, v in mapping.items():
        if k in m:
            return v  # type: ignore[return-value]
    return "HVAC"  # fallback


def _normalize_type(t: str | None) -> PropType:
    s = (t or "").strip().upper()
    if s in ("INT", "INT32", "INT64", "INTEGER"):
        return "INT"
    if s in ("FLOAT", "DOUBLE", "FP32", "FP64"):
        return "FLOAT"
    if s in ("BOOL", "BOOLEAN"):
        return "BOOLEAN"
    raise ValueError(f"[YAML SPEC ERROR] Unsupported property type: {t!r} (allowed: INT|FLOAT|BOOLEAN)")


def _normalize_access(a: str | None) -> Access:
    s = (a or "").strip().upper()
    if s in ("READ", "WRITE", "READ_WRITE"):
        return s  # type: ignore[return-value]
    raise ValueError(f"[YAML SPEC ERROR] Unsupported access: {a!r} (allowed: READ|WRITE|READ_WRITE)")


def _normalize_areas(areas_val: Any) -> List[str]:
    if areas_val is None:
        return []
    if isinstance(areas_val, str):
        cleaned = [x.strip() for x in areas_val.split(",") if x.strip() and x.strip().upper() != "GLOBAL"]
        return cleaned
    if isinstance(areas_val, list):
        cleaned = []
        for x in areas_val:
            xs = str(x).strip()
            if xs and xs.upper() != "GLOBAL":
                cleaned.append(xs)
        return cleaned
    raise ValueError(f"[YAML SPEC ERROR] 'areas' must be string or list, got {type(areas_val).__name__}")


def load_hal_spec_from_yaml_text(yaml_text: str) -> HalSpec:
    cleaned = _extract_yaml_text(yaml_text)
    if not cleaned.strip():
        raise ValueError("[YAML SPEC ERROR] No valid YAML content found after cleaning")

    try:
        if cleaned.lstrip().startswith("---"):
            doc = next(yaml.safe_load_all(cleaned))
        else:
            doc = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        tail = "\n".join(cleaned.splitlines()[-40:])
        raise ValueError(
            "[YAML SPEC ERROR] YAML parsing failed.\n"
            f"---- Last 40 lines of cleaned input ----\n{tail}\n"
            "---------------------------------------\n"
            f"{e}"
        ) from e

    if not isinstance(doc, dict):
        raise ValueError("[YAML SPEC ERROR] Root must be a mapping (dict)")

    product = _require(doc, "product", "root")
    target = _require(doc, "target", "root")
    props_list = _require(doc, "properties", "root")

    if not isinstance(product, dict) or not isinstance(target, dict) or not isinstance(props_list, list):
        raise ValueError("[YAML SPEC ERROR] Invalid structure: product/target must be dict, properties must be list")

    vendor = product.get("vendor", "AOSP")
    android = product.get("android", "AAOS_14")
    aosp_level = _aaos_to_aosp_level(android)
    module_name = target.get("module", "DEFAULT")
    domain = _normalize_domain(module_name)

    properties: List[PropertySpec] = []

    for idx, p in enumerate(props_list):
        if not isinstance(p, dict):
            raise ValueError(f"[YAML SPEC ERROR] properties[{idx}] is not a dict")

        name = _require(p, "name", f"properties[{idx}]")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"[YAML SPEC ERROR] Invalid/missing name in properties[{idx}]")

        ptype = _require(p, "type", f"properties[{idx}]")
        access = _require(p, "access", f"properties[{idx}]")
        areas = p.get("areas", [])

        meta = {
            k: v for k, v in p.items()
            if k not in ("name", "type", "access", "areas")
        }

        properties.append(
            PropertySpec(
                id=name,                               # ← stable string key (full name)
                type=_normalize_type(ptype),
                access=_normalize_access(access),
                areas=_normalize_areas(areas),
                meta=meta,
            )
        )

    return HalSpec(
        domain=domain,
        aosp_level=aosp_level,
        vendor=vendor,
        properties=properties,
    )