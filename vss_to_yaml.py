import json
import re
from typing import Any, Dict, List, Optional

import yaml


def walk_vss_leaves(node: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, child in (node.get("children") or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(child, dict) and "datatype" in child:
            out.append({"path": path, "node": child})
        if isinstance(child, dict) and child.get("children"):
            out.extend(walk_vss_leaves(child, path))
    return out


def vss_datatype_to_yaml_type(dt: Optional[str]) -> str:
    if not dt:
        return "STRING"
    dt = dt.lower()
    if dt in ("float", "double"):
        return "FLOAT"
    if dt in ("boolean", "bool"):
        return "BOOLEAN"
    if dt in ("string",):
        return "STRING"
    if dt.startswith("int") or dt.startswith("uint"):
        return "INT"
    return "STRING"


def vss_type_to_access(vss_node: Dict[str, Any]) -> str:
    t = (vss_node.get("type") or "").lower()
    if t == "actuator":
        return "READ_WRITE"
    # attributes are often writable in OEM trees, but safest default is READ
    return "READ"


def path_to_property_name(vss_path: str) -> str:
    # Stable vendor name
    s = re.sub(r"[^A-Za-z0-9]+", "_", vss_path).strip("_")
    return f"VSS_{s}".upper()


def infer_module_from_path(path: str) -> str:
    p = path.lower()
    if ".adas." in p:
        return "ADAS"
    if ".cabin." in p and ("hvac" in p or "climate" in p):
        return "HVAC"
    if ".infotainment." in p or ".media." in p:
        return "MEDIA"
    if ".powertrain." in p or ".energy." in p:
        return "POWER"
    return "HVAC"


def vss_to_yaml_spec(
    vss_json_path: str,
    vendor: str = "AOSP",
    android: str = "AAOS_14",
    include_prefixes: Optional[List[str]] = None,
    max_props: Optional[int] = 200,
    vendor_namespace: str = "vendor.vss",
) -> str:
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

    # Infer module from majority of paths (simple, robust)
    module_votes: Dict[str, int] = {}
    for x in leaves:
        m = infer_module_from_path(x["path"])
        module_votes[m] = module_votes.get(m, 0) + 1
    module = max(module_votes, key=module_votes.get) if module_votes else "HVAC"

    props: List[Dict[str, Any]] = []
    for item in leaves:
        path = item["path"]
        node = item["node"]

        name = path_to_property_name(path)
        typ = vss_datatype_to_yaml_type(node.get("datatype"))
        access = vss_type_to_access(node)

        # Areas: VSS doesn’t encode AAOS zones; use GLOBAL unless you have mapping logic
        areas = ["GLOBAL"]

        # SDV defaults
        updatable = access in ("WRITE", "READ_WRITE")

        p_obj: Dict[str, Any] = {
            "name": name,
            "type": typ,
            "access": access,
            "areas": areas,

            # Keep traceability (optional; if your loader rejects unknown keys, remove this)
            "meta": {
                "vss_path": path,
                "vss_datatype": node.get("datatype"),
                "vss_type": node.get("type"),
                "unit": node.get("unit"),
                "description": node.get("description"),
            },

            "aosp": {
                "standard": False,
                "kind": "vendor",
                "vendor_namespace": vendor_namespace,
            },
            "sdv": {
                "updatable_behavior": bool(updatable),
                "cloud_control": {
                    "allowed": False,
                    "mode": "disabled",
                },
                "telemetry": {
                    "publish": True,
                    "rate_limit_hz": 1,
                },
            },
        }

        props.append(p_obj)

    spec = {
        "spec_version": 1.1,
        "product": {
            "vendor": vendor,
            "android": android,
        },
        "target": {
            "module": module,
            "domains": ["vehicle_hal", "car_service", "sepolicy"],
        },
        "features": [
            {
                "name": "generated_from_vss",
                "description": "Generated deterministically from VSS JSON",
            }
        ],
        "properties": props,
    }

    return yaml.safe_dump(spec, sort_keys=False, indent=2)
