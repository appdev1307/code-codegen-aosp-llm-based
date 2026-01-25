# FILE: vss_to_yaml.py
# Deterministic VSS JSON -> YAML spec v1.1 (NO LLM)
# Supports raw VSS tree and labelled flat dict
import json
import re
from typing import Any, Dict, List, Optional, Tuple
import yaml


def vss_datatype_to_yaml_type(dt: Optional[str]) -> str:
    """Map VSS datatype to supported YAML types"""
    if not dt:
        return "INT"
    dt = str(dt).lower()
    if dt in ("float", "double"):
        return "FLOAT"
    if dt in ("boolean", "bool"):
        return "BOOLEAN"
    if dt.startswith(("int", "uint")):
        return "INT"
    return "INT"  # Fallback


def vss_type_to_access(vss_node: Dict[str, Any]) -> str:
    """actuator → READ_WRITE, else READ"""
    t = (vss_node.get("type") or "").lower()
    return "READ_WRITE" if t == "actuator" else "READ"


def path_to_property_name(vss_path: str) -> str:
    """Convert VSS path to stable property name"""
    s = re.sub(r"[^A-Za-z0-9]+", "_", vss_path).strip("_")
    return f"VSS_{s}".upper()


def infer_module_from_paths(paths: List[str]) -> str:
    """Simple heuristic for module name"""
    votes = {"HVAC": 0, "ADAS": 0, "BODY": 0, "CABIN": 0, "POWERTRAIN": 0, "CHASSIS": 0, "INFOTAINMENT": 0, "OTHER": 0}
    for p in paths:
        pl = p.lower()
        if "adas" in pl or "obstacle" in pl or "driver" in pl or "lane" in pl:
            votes["ADAS"] += 1
        elif "hvac" in pl or "climate" in pl or "fan" in pl or "temperature" in pl:
            votes["HVAC"] += 1
        elif "body" in pl or "light" in pl or "door" in pl or "mirror" in pl or "window" in pl:
            votes["BODY"] += 1
        elif "powertrain" in pl or "battery" in pl or "engine" in pl or "transmission" in pl:
            votes["POWERTRAIN"] += 1
        elif "chassis" in pl or "brake" in pl or "steering" in pl or "suspension" in pl:
            votes["CHASSIS"] += 1
        elif "infotainment" in pl or "audio" in pl or "navigation" in pl or "display" in pl:
            votes["INFOTAINMENT"] += 1
        else:
            votes["CABIN"] += 1
    return max(votes, key=votes.get) if paths else "HVAC"


def vss_to_yaml_spec(
    vss_json_path: Optional[str] = None,
    vss_json: Optional[Dict[str, Any]] = None,
    *,
    vendor: str = "AOSP",
    android: str = "AAOS_14",
    include_prefixes: Optional[List[str]] = None,
    max_props: Optional[int] = None,
    vendor_namespace: str = "vendor.vss",
    add_meta: bool = False,
) -> Tuple[str, int]:
    """
    Convert VSS to YAML spec v1.1
    Supports:
      - Raw VSS tree (with "Vehicle" root and "children")
      - Labelled flat dict (normalized_id → enhanced signal)
    """
    if vss_json is None:
        if vss_json_path is None:
            raise ValueError("Must provide either vss_json_path or vss_json")
        with open(vss_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = vss_json

    leaves = []

    # Case 1: Raw VSS tree
    if "Vehicle" in data:
        vehicle = data["Vehicle"]
        if not isinstance(vehicle, dict):
            raise ValueError('Invalid VSS: missing "Vehicle" object')

        def walk(node: Dict, prefix: str = "Vehicle"):
            for name, child in (node.get("children") or {}).items():
                if not isinstance(child, dict):
                    continue
                path = f"{prefix}.{name}"
                if "datatype" in child and child.get("type") != "branch":
                    leaves.append({"path": path, "node": child})
                if "children" in child:
                    walk(child, path)

        walk(vehicle)

    # Case 2: Labelled flat dict
    else:
        for norm_id, enhanced in data.items():
            # enhanced is the full enriched signal
            node = enhanced if isinstance(enhanced, dict) else enhanced.get("node", {})
            path = enhanced.get("vss_path", norm_id.replace("VSS_", "").replace("_", "."))
            if "datatype" in node:
                leaves.append({"path": path, "node": node, "enhanced": enhanced})

    # Filtering
    if include_prefixes:
        leaves = [l for l in leaves if any(l["path"].startswith(p) for p in include_prefixes)]
    if max_props is not None:
        leaves = leaves[:max_props]

    paths = [l["path"] for l in leaves]
    module = infer_module_from_paths(paths)

    props: List[Dict[str, Any]] = []
    for item in leaves:
        node = item.get("node", {}) or item.get("enhanced", {})
        path = item["path"]
        name = item.get("enhanced", {}).get("normalized_id", path_to_property_name(path))
        typ = vss_datatype_to_yaml_type(node.get("datatype"))
        access = vss_type_to_access(node)

        p_obj: Dict[str, Any] = {
            "name": name,
            "type": typ,
            "access": access,
            "areas": ["GLOBAL"],
            "aosp": {
                "standard": False,
                "kind": "vendor",
                "vendor_namespace": vendor_namespace,
            },
            "sdv": {
                "updatable_behavior": "READ_WRITE" in access,
                "cloud_control": {"allowed": False, "mode": "disabled"},
                "telemetry": {"publish": True, "rate_limit_hz": 1},
            },
        }

        if add_meta:
            labels = node.get("labels", {}) if isinstance(node, dict) else {}
            p_obj["meta"] = {
                "vss_path": path,
                "vss_datatype": node.get("datatype"),
                "vss_type": node.get("type"),
                "description": node.get("description"),
                "domain": labels.get("domain", module),
                "ui_widget": labels.get("ui_widget"),
                "safety_level": labels.get("safety_level"),
                "aosp_standard": labels.get("aosp_standard", False),
            }

        props.append(p_obj)

    spec = {
        "spec_version": 1.1,
        "product": {"vendor": vendor, "android": android},
        "target": {"module": module, "domains": ["vehicle_hal", "car_service", "sepolicy"]},
        "features": [{"name": "generated_from_vss", "description": "AI-generated from VSS"}],
        "properties": props,
    }

    yaml_text = yaml.safe_dump(spec, sort_keys=False, indent=2, allow_unicode=True)
    return yaml_text, len(props)