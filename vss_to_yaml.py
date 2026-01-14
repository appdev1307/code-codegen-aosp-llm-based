# FILE: vss_to_yaml.py
# Deterministic VSS JSON -> YAML spec v1.1 (NO LLM)

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml


def walk_vss_leaves(node: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    """
    Collect all VSS leaves under node. A leaf is any node that has "datatype".
    Always recurse into children (some trees are mixed).
    """
    leaves: List[Dict[str, Any]] = []
    for name, child in (node.get("children") or {}).items():
        if not isinstance(child, dict):
            continue

        path = f"{prefix}.{name}" if prefix else name

        if "datatype" in child:
            leaves.append({"path": path, "node": child})

        if child.get("children"):
            leaves.extend(walk_vss_leaves(child, path))

    return leaves


def vss_datatype_to_yaml_type(dt: Optional[str]) -> str:
    """
    IMPORTANT:
    Your current yaml_loader only supports: INT|FLOAT|BOOLEAN
    So we MUST NOT output STRING.

    Mapping strategy:
      - float/double -> FLOAT
      - boolean/bool -> BOOLEAN
      - int*/uint*   -> INT
      - string/unknown/missing -> INT (fallback to keep pipeline working)
    """
    if not dt:
        return "INT"

    dt = str(dt).lower()
    if dt in ("float", "double"):
        return "FLOAT"
    if dt in ("boolean", "bool"):
        return "BOOLEAN"
    if dt.startswith("int") or dt.startswith("uint"):
        return "INT"
    if dt in ("string",):
        return "INT"  # loader doesn't support STRING
    return "INT"


def vss_type_to_access(vss_node: Dict[str, Any]) -> str:
    """
    VSS node "type" is typically: sensor | actuator | attribute | branch
    - actuator -> READ_WRITE
    - otherwise -> READ
    """
    t = (vss_node.get("type") or "").lower()
    if t == "actuator":
        return "READ_WRITE"
    return "READ"


def path_to_property_name(vss_path: str) -> str:
    """
    Stable vendor property name from VSS path.
    """
    s = re.sub(r"[^A-Za-z0-9]+", "_", vss_path).strip("_")
    return f"VSS_{s}".upper()


def infer_module_from_paths(paths: List[str]) -> str:
    """
    Choose module by majority vote.
    """
    votes: Dict[str, int] = {"HVAC": 0, "ADAS": 0, "MEDIA": 0, "POWER": 0}
    for p in paths:
        pl = p.lower()
        if ".adas." in pl:
            votes["ADAS"] += 1
        elif ".cabin." in pl and ("hvac" in pl or "climate" in pl):
            votes["HVAC"] += 1
        elif ".infotainment." in pl or ".media." in pl:
            votes["MEDIA"] += 1
        elif ".powertrain." in pl or ".energy." in pl:
            votes["POWER"] += 1
        else:
            votes["HVAC"] += 1
    return max(votes, key=votes.get) if paths else "HVAC"


def vss_to_yaml_spec(
    vss_json_path: str,
    *,
    vendor: str = "AOSP",
    android: str = "AAOS_14",
    include_prefixes: Optional[List[str]] = None,
    max_props: Optional[int] = 200,
    vendor_namespace: str = "vendor.vss",
    add_meta: bool = False,
) -> Tuple[str, int]:
    """
    Returns (yaml_text, leaf_count_used)

    add_meta=False by default to avoid breaking strict YAML loaders.
    Turn on only if your loader allows extra keys.
    """
    with open(vss_json_path, "r", encoding="utf-8") as f:
        tree = json.load(f)

    vehicle = tree.get("Vehicle")
    if not isinstance(vehicle, dict):
        raise ValueError('Invalid VSS JSON: missing top-level "Vehicle" object')

    leaves = walk_vss_leaves(vehicle, "Vehicle")

    if include_prefixes:
        leaves = [x for x in leaves if any(x["path"].startswith(pfx) for pfx in include_prefixes)]

    if max_props is not None:
        leaves = leaves[:max_props]

    paths = [x["path"] for x in leaves]
    module = infer_module_from_paths(paths)

    props: List[Dict[str, Any]] = []
    for item in leaves:
        path = item["path"]
        node = item["node"]

        name = path_to_property_name(path)
        typ = vss_datatype_to_yaml_type(node.get("datatype"))
        access = vss_type_to_access(node)

        # Guardrail: never output unsupported types
        if typ not in ("INT", "FLOAT", "BOOLEAN"):
            typ = "INT"

        p_obj: Dict[str, Any] = {
            "name": name,
            "type": typ,                 # INT|FLOAT|BOOLEAN (ONLY)
            "access": access,            # READ|READ_WRITE
            "areas": ["GLOBAL"],         # VSS doesn't carry AAOS zones
            "aosp": {
                "standard": False,
                "kind": "vendor",
                "vendor_namespace": vendor_namespace,
            },
            "sdv": {
                "updatable_behavior": bool(access in ("WRITE", "READ_WRITE")),
                "cloud_control": {"allowed": False, "mode": "disabled"},
                "telemetry": {"publish": True, "rate_limit_hz": 1},
            },
        }

        if add_meta:
            p_obj["meta"] = {
                "vss_path": path,
                "vss_datatype": node.get("datatype"),
                "vss_type": node.get("type"),
                "unit": node.get("unit"),
                "description": node.get("description"),
            }

        props.append(p_obj)

    spec = {
        "spec_version": 1.1,
        "product": {"vendor": vendor, "android": android},
        "target": {"module": module, "domains": ["vehicle_hal", "car_service", "sepolicy"]},
        "features": [{"name": "generated_from_vss", "description": "Generated deterministically from VSS JSON"}],
        "properties": props,
    }

    yaml_text = yaml.safe_dump(spec, sort_keys=False, indent=2)
    return yaml_text, len(props)
