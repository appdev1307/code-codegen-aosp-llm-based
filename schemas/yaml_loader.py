from __future__ import annotations

from typing import Any, Dict, List, Optional

from schemas.hal_spec import HalSpec, PropertySpec, Domain, PropType, Access


import re

def _strip_yaml_fences(text: str) -> str:
    """
    Removes Markdown code fences like:
      ```yaml
      ...
      ```
    and returns raw YAML content.
    """
    if not text:
        return text

    s = text.strip()

    # If it's fenced, extract the inner content
    if s.startswith("```"):
        # Remove opening fence line (``` or ```yaml)
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", s)
        # Remove closing fence
        s = re.sub(r"\n```$", "", s.strip())
        return s.strip()

    # Also handle accidental inline fences anywhere
    s = s.replace("```yaml", "").replace("```", "")
    return s.strip()


def _require(d: Dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ValueError(f"[YAML SPEC ERROR] Missing required key '{key}' in {ctx}")
    return d[key]


def _aaos_to_aosp_level(android_str: str) -> int:
    """
    Accepts:
      - "AAOS" -> default 14
      - "AAOS_14" / "AOSP_14" -> 14
      - "14" -> 14
    """
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

    # Best-effort inference from free text
    if "HVAC" in m or "CLIMATE" in m or "AIR" in m or "TEMP" in m:
        return "HVAC"
    if "ADAS" in m or "ACC" in m or "LKA" in m:
        return "ADAS"
    if "MEDIA" in m or "AUDIO" in m or "IVI" in m:
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

    yaml_text = _strip_yaml_fences(yaml_text)
    doc = yaml.safe_load(yaml_text)
    if not isinstance(doc, dict):
        raise ValueError("[YAML SPEC ERROR] YAML root must be a mapping/object")

    product = _require(doc, "product", "root")
    target = _require(doc, "target", "root")
    props = _require(doc, "properties", "root")

    if not isinstance(product, dict):
        raise ValueError("[YAML SPEC ERROR] 'product' must be an object")
    if not isinstance(target, dict):
        raise ValueError("[YAML SPEC ERROR] 'target' must be an object")
    if not isinstance(props, list):
        raise ValueError("[YAML SPEC ERROR] 'properties' must be a list")

    vendor = product.get(" hookupvendor")  # NOTE: will be fixed below
    # fix accidental typo if any
    vendor = product.get("vendor") or "AOSP"

    android = product.get("android") or "AAOS_14"
    # handle "Platform: AAOS" style results if converter outputs "AAOS"
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

        properties.append(
            PropertySpec(
                id=str(name),
                type=_normalize_type(str(ptype)),
                access=_normalize_access(str(access)),
                areas=_normalize_areas(areas),
            )
        )

    return HalSpec(
        domain=domain,
        aosp_level=aosp_level,
        vendor=vendor,
        properties=properties,
    )
