from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from schemas.hal_spec import HalSpec, PropertySpec, Domain, PropType, Access


# ----------------------------
# LLM output cleanup helpers
# ----------------------------

_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _strip_yaml_fences(text: str) -> str:
    """
    Backwards-compatible fence stripper.
    Keeps behavior but we now prefer _extract_yaml_text() which is stricter.
    """
    if not text:
        return text
    s = text.strip()

    # Extract fenced block content if present
    m = _FENCE_RE.search(s)
    if m:
        return m.group(1).strip()

    # Remove stray fences if model inserted them
    s = s.replace("```yaml", "").replace("```yml", "").replace("```", "")
    return s.strip()


def _extract_yaml_text(text: str) -> str:
    """
    Make LLM output safe for yaml.safe_load by extracting only YAML-like content.
    Priority:
      1) fenced ```yaml blocks
      2) first YAML document if '---' present (we still parse only first doc)
      3) heuristic: keep YAML-looking lines; stop at first prose line after YAML begins
    """
    if not text:
        return ""

    s = text.strip()

    # 1) Prefer fenced blocks
    m = _FENCE_RE.search(s)
    if m:
        return m.group(1).strip()

    # 2) If there are explicit YAML doc markers, keep from first marker onward.
    # We'll later parse the first doc only.
    if "---" in s:
        idx = s.find("---")
        return s[idx:].strip()

    # 3) Heuristic: retain YAML-ish lines, cut off trailing prose
    lines = s.splitlines()
    kept: List[str] = []
    started = False

    # YAML-ish patterns:
    key_line = re.compile(r"^\s*[A-Za-z0-9_.\-/]+\s*:\s*(?:#.*)?$")  # "key:" or "key: #comment"
    key_value_line = re.compile(r"^\s*[A-Za-z0-9_.\-/]+\s*:\s+.+$")  # "key: value"
    list_item_line = re.compile(r"^\s*-\s+.+$")  # "- item"
    comment_line = re.compile(r"^\s*#")

    for line in lines:
        raw = line.rstrip("\n")
        t = raw.strip()

        # Always allow blank lines / comments once started (and even before)
        if not t or comment_line.match(t):
            if started:
                kept.append(raw)
            continue

        is_yaml_like = bool(key_line.match(raw) or key_value_line.match(raw) or list_item_line.match(raw))

        if is_yaml_like:
            kept.append(raw)
            started = True
            continue

        # If we already started capturing YAML and we hit a prose line -> stop.
        if started:
            break

        # Otherwise, ignore leading non-YAML junk
        continue

    return "\n".join(kept).strip()


def _require(d: Dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ValueError(f"[YAML SPEC ERROR] Missing required key '{key}' in {ctx}")
    return d[key]


def _aaos_to_aosp_level(android_str: str) -> int:
    s = (android_str or "").strip().upper()
    if s in ("AAOS", "AOSP", "ANDROID"):
        return 14
    if s.startswith("AAOS_"):
        s = s.replace("AAOS_", "", 1)
    if s.startswith("AOSP_"):
        s = s.replace("AOSP_", "", 1)
    try:
        return int(s)
    except Exception:
        raise ValueError(f"[YAML SPEC ERROR] Invalid product.android value: {android_str}")


def _normalize_domain(module: Optional[str]) -> Domain:
    m = (module or "").strip().upper()
    if m in ("HVAC", "ADAS", "MEDIA", "POWER"):
        return m  # type: ignore[return-value]
    if "CLIMATE" in m or "HVAC" in m:
        return "HVAC"
    if "ADAS" in m:
        return "ADAS"
    if "MEDIA" in m or "AUDIO" in m:
        return "MEDIA"
    if "POWER" in m or "BMS" in m or "BATTERY" in m:
        return "POWER"
    return "HVAC"


def _normalize_type(t: str) -> PropType:
    s = (t or "").strip().upper()
    if s in ("INT", "INT32", "INT64", "INTEGER"):
        return "INT"
    if s in ("FLOAT", "DOUBLE", "FP32", "FP64"):
        return "FLOAT"
    if s in ("BOOL", "BOOLEAN"):
        return "BOOLEAN"
    raise ValueError(f"[YAML SPEC ERROR] Unsupported property type: {t} (allowed: INT|FLOAT|BOOLEAN)")


def _normalize_access(a: str) -> Access:
    s = (a or "").strip().upper()
    if s in ("READ", "WRITE", "READ_WRITE"):
        return s  # type: ignore[return-value]
    raise ValueError(f"[YAML SPEC ERROR] Unsupported access: {a} (allowed: READ|WRITE|READ_WRITE)")


def _normalize_areas(areas_val: Any) -> List[str]:
    if areas_val is None:
        return []
    if isinstance(areas_val, str):
        s = areas_val.strip()
        if not s or s.upper() == "GLOBAL":
            return []
        return [x.strip() for x in s.split(",") if x.strip()]
    if isinstance(areas_val, list):
        cleaned: List[str] = []
        for x in areas_val:
            xs = str(x).strip()
            if not xs or xs.upper() == "GLOBAL":
                continue
            cleaned.append(xs)
        return cleaned
    raise ValueError("[YAML SPEC ERROR] 'areas' must be a list or string")


def load_hal_spec_from_yaml_text(yaml_text: str) -> HalSpec:
    try:
        import yaml  # PyYAML
    except Exception as e:
        raise RuntimeError("Missing dependency: PyYAML. Install with: pip install pyyaml") from e

    # NEW: strict extraction to prevent trailing prose from breaking parsing
    cleaned = _extract_yaml_text(yaml_text)

    # Keep your old fence-stripper as a fallback if extraction somehow returns empty
    if not cleaned.strip():
        cleaned = _strip_yaml_fences(yaml_text)

    if not cleaned.strip():
        raise ValueError("[YAML SPEC ERROR] No YAML content found in input text")

    # Parse (first doc only if multi-doc)
    try:
        if cleaned.lstrip().startswith("---"):
            doc = next(yaml.safe_load_all(cleaned))
        else:
            doc = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        tail = "\n".join(cleaned.splitlines()[-40:])
        raise ValueError(
            "[YAML SPEC ERROR] YAML parsing failed (after sanitizing LLM output).\n"
            "---- YAML (last 40 lines) ----\n"
            f"{tail}\n"
            "-----------------------------\n"
            f"{e}"
        ) from e

    if not isinstance(doc, dict):
        raise ValueError("[YAML SPEC ERROR] YAML root must be a mapping/object")

    product = _require(doc, "product", "root")
    target = _require(doc, "target", "root")
    props = _require(doc, "properties", "root")

    if not isinstance(product, dict) or not isinstance(target, dict) or not isinstance(props, list):
        raise ValueError("[YAML SPEC ERROR] Invalid product/target/properties types")

    vendor = product.get("vendor") or "AOSP"
    android = product.get("android") or "AAOS_14"
    aosp_level = _aaos_to_aosp_level(str(android))

    module = target.get("module") or "HVAC"
    domain = _normalize_domain(str(module))

    properties: List[PropertySpec] = []
    for i, p in enumerate(props):
        if not isinstance(p, dict):
            raise ValueError(f"[YAML SPEC ERROR] properties[{i}] must be an object")

        name = _require(p, "name", f"properties[{i}]")
        ptype = _require(p, "type", f"properties[{i}]")
        access = _require(p, "access", f"properties[{i}]")
        areas = p.get("areas", [])

        # ✅ carry everything else as meta (aosp/sdv/constraints/...)
        meta = {k: v for k, v in p.items() if k not in ("name", "type", "access", "areas")}

        properties.append(
            PropertySpec(
                id=str(name),
                type=_normalize_type(str(ptype)),
                access=_normalize_access(str(access)),
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
