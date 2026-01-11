import json
import re
from typing import Any, Dict, List, Optional

from agents.spec_yaml_converter_agent import SpecYamlConverterAgent
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from tools.aosp_layout import ensure_aosp_layout


# ============================================================
# 1) VSS JSON -> "spec text" (YAML-ish) that LLM will normalize
# ============================================================

def walk_vss_leaves(node: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    """Collect leaf signals that contain 'datatype'."""
    out: List[Dict[str, Any]] = []
    for name, child in (node.get("children") or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(child, dict) and "datatype" in child:
            out.append({"path": path, "node": child})
        if isinstance(child, dict) and child.get("children"):
            out.extend(walk_vss_leaves(child, path))
    return out


def vss_datatype_to_spec_type(dt: Optional[str]) -> str:
    """Map VSS datatype to your spec types used by converter."""
    if not dt:
        return "STRING"
    dt = dt.lower()
    if dt in ("float", "double"):
        return "FLOAT"
    if dt in ("int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"):
        return "INT32"
    if dt in ("boolean", "bool"):
        return "BOOLEAN"
    if dt in ("string",):
        return "STRING"
    return "STRING"


def vss_type_to_access(vss_node: Dict[str, Any]) -> str:
    """
    Heuristic:
      - actuator -> READ_WRITE
      - sensor/attribute -> READ
    """
    t = (vss_node.get("type") or "").lower()
    if t == "actuator":
        return "READ_WRITE"
    return "READ"


def path_to_property_id(vss_path: str) -> str:
    """
    Convert VSS path into a stable Property ID.
    Example: Vehicle.ADAS.ABS.IsEnabled -> VSS_VEHICLE_ADAS_ABS_ISENABLED
    """
    s = re.sub(r"[^A-Za-z0-9]+", "_", vss_path).strip("_")
    return f"VSS_{s}".upper()


def build_spec_text_from_vss(
    vss_json_path: str,
    system: str = "VSS Derived System",
    platform: str = "AAOS",
    include_prefixes: Optional[List[str]] = None,
    max_props: Optional[int] = 200,
) -> str:
    """
    Output is a YAML-ish text that the LLM converter can read and normalize.
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

    lines: List[str] = []
    lines.append(f"System: {system}")
    lines.append(f"Platform: {platform}")
    lines.append("")
    lines.append("Features:")
    lines.append("- Generated from VSS JSON (auto)")
    if include_prefixes:
        lines.append("- Filtered by:")
        for pfx in include_prefixes:
            lines.append(f"  - {pfx}")
    lines.append("")
    lines.append("Properties:")

    for item in leaves:
        path = item["path"]
        node = item["node"]

        prop_id = path_to_property_id(path)
        typ = vss_datatype_to_spec_type(node.get("datatype"))
        access = vss_type_to_access(node)

        unit = node.get("unit")
        desc = node.get("description")

        lines.append(f"- Property ID : {prop_id}")
        lines.append(f"  VSS Path    : {path}")  # helps LLM keep traceability
        lines.append(f"  Type        : {typ}")
        lines.append(f"  Access      : {access}")

        if isinstance(unit, str) and unit.strip():
            lines.append(f"  Unit        : {unit.strip()}")
        if isinstance(desc, str) and desc.strip():
            lines.append(f"  Description : {desc.strip()}")

        lines.append("")

    return "\n".join(lines).strip()


# ============================================================
# 2) NEW: Chunking to avoid Ollama timeout
# ============================================================

def split_spec_into_property_blocks(spec_text: str) -> (str, List[str]):
    """
    Returns (header, property_blocks)
    - header includes everything up to and including "Properties:"
    - property_blocks are each "- Property ID ..." block
    """
    lines = spec_text.splitlines()
    try:
        idx = lines.index("Properties:")
    except ValueError:
        raise ValueError('Spec text missing "Properties:"')

    header = "\n".join(lines[: idx + 1]).strip()

    blocks: List[str] = []
    current: List[str] = []
    for line in lines[idx + 1 :]:
        if line.strip().startswith("- Property ID"):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        # keep empty lines inside a block (but skip leading empties)
        if line.strip() or current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    return header, blocks


def chunk_property_blocks(header: str, blocks: List[str], chunk_size: int = 40) -> List[str]:
    """
    Combine header + up to chunk_size property blocks into chunk specs.
    """
    chunks: List[str] = []
    for i in range(0, len(blocks), chunk_size):
        part = blocks[i : i + chunk_size]
        chunk_text = header + "\n" + "\n\n".join(part)
        chunks.append(chunk_text.strip())
    return chunks


def extract_properties_section(yaml_text: str) -> str:
    """
    Extract everything after the line 'properties:'.
    Assumes SpecYamlConverterAgent outputs a YAML with top-level 'properties:'.
    """
    lines = yaml_text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*properties\s*:\s*$", line):
            return "\n".join(lines[i + 1 :]).rstrip()
    raise ValueError("Converted YAML missing top-level `properties:` section")


def extract_yaml_header(yaml_text: str) -> str:
    """
    Extract everything up to (but not including) 'properties:'.
    """
    lines = yaml_text.splitlines()
    out = []
    for line in lines:
        if re.match(r"^\s*properties\s*:\s*$", line):
            break
        out.append(line)
    return "\n".join(out).rstrip()


def merge_converted_yaml(yaml_parts: List[str]) -> str:
    """
    Merge multiple YAML docs:
      - header from first
      - concat all properties list items
    """
    if not yaml_parts:
        raise ValueError("No YAML parts to merge")

    header = extract_yaml_header(yaml_parts[0]).rstrip()
    merged_props: List[str] = []

    for part in yaml_parts:
        merged_props.append(extract_properties_section(part))

    return f"{header}\nproperties:\n" + "\n".join(p for p in merged_props if p.strip()) + "\n"


def convert_spec_with_chunking(
    converter: SpecYamlConverterAgent,
    spec_text: str,
    chunk_size: int = 40,
) -> str:
    """
    Split large spec text into chunks and convert each chunk via LLM, then merge.
    """
    header, blocks = split_spec_into_property_blocks(spec_text)
    if not blocks:
        raise ValueError("No properties found in spec text")

    chunks = chunk_property_blocks(header, blocks, chunk_size=chunk_size)
    yaml_parts: List[str] = []

    for i, chunk in enumerate(chunks, 1):
        print(f"[DEBUG] SpecYamlConverterAgent: converting chunk {i}/{len(chunks)} "
              f"(~{chunk.count('- Property ID')} properties)", flush=True)
        yaml_parts.append(converter.run(chunk))

    merged = merge_converted_yaml(yaml_parts)
    return merged


# ============================================================
# 3) Your existing pipeline (same, but uses chunked conversion)
# ============================================================

designer_simple_spec = build_spec_text_from_vss(
    vss_json_path="./dataset/vss.json",
    system="VSS -> HAL",
    platform="AAOS",
    include_prefixes=None,   # safest
    max_props=200,           # still start small; increase later
)

converter = SpecYamlConverterAgent(output_root="output")

# ✅ chunk conversion to avoid ReadTimeout(600)
yaml_spec = convert_spec_with_chunking(
    converter=converter,
    spec_text=designer_simple_spec,
    chunk_size=40,           # tune: 20/40/60 depending on your machine/model
)

spec = load_hal_spec_from_yaml_text(yaml_spec)

ensure_aosp_layout(spec)
ArchitectAgent().run(spec)
