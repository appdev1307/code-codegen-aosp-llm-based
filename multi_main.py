import json
import re
from typing import Any, Dict, List, Optional, Tuple

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
# 2) Chunking to avoid Ollama timeout
# ============================================================

def split_spec_into_property_blocks(spec_text: str) -> Tuple[str, List[str]]:
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
        if line.strip() or current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    return header, blocks


def chunk_property_blocks(header: str, blocks: List[str], chunk_size: int = 40) -> List[str]:
    """Combine header + up to chunk_size property blocks into chunk specs."""
    chunks: List[str] = []
    for i in range(0, len(blocks), chunk_size):
        part = blocks[i : i + chunk_size]
        chunk_text = header + "\n" + "\n\n".join(part)
        chunks.append(chunk_text.strip())
    return chunks


# ============================================================
# 2b) NEW: Robust YAML merge (handles LLM format variations)
# ============================================================

def _find_section_start(lines: List[str], keys: List[str]) -> Optional[int]:
    """
    Return line index of a section header matching any key in keys (case-insensitive),
    where the line looks like:  key:
    """
    keyset = {k.lower() for k in keys}
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*$", line)
        if m and m.group(1).lower() in keyset:
            return i
    return None


def extract_yaml_header_and_props(yaml_text: str) -> Tuple[str, str]:
    """
    Robustly extract:
      - header: everything above the properties section (best-effort)
      - props_body: YAML list items under properties (best-effort)
    Supports variants: properties:, Properties:, property:, props:, signals:
    """
    lines = yaml_text.splitlines()

    prop_keys = ["properties", "property", "props", "signals"]
    idx = _find_section_start(lines, prop_keys)

    if idx is not None:
        header = "\n".join(lines[:idx]).rstrip()
        body = "\n".join(lines[idx + 1 :]).rstrip()
        return header, body

    # Fallback: if it looks like a YAML list, treat whole doc as properties body
    looks_like_list = any(re.match(r"^\s*-\s+", ln) for ln in lines)
    if looks_like_list:
        return "", yaml_text.rstrip()

    preview = "\n".join(lines[:40])
    raise ValueError(
        "Converted YAML missing a recognizable properties section.\n"
        "---- YAML preview (first 40 lines) ----\n"
        f"{preview}\n"
        "--------------------------------------\n"
        "Fix: enforce converter output to include a top-level `properties:` list."
    )


def merge_converted_yaml(yaml_parts: List[str]) -> str:
    """
    Merge multiple YAML docs into one:
      - take first non-empty header encountered
      - always output:
            <header>
            properties:
              <all list items from chunks>
    """
    if not yaml_parts:
        raise ValueError("No YAML parts to merge")

    chosen_header = ""
    merged_bodies: List[str] = []

    for i, part in enumerate(yaml_parts, 1):
        header, body = extract_yaml_header_and_props(part)

        if not chosen_header and header.strip():
            chosen_header = header.strip()

        if body.strip():
            merged_bodies.append(body.rstrip())

        # Helpful warning (doesn't fail)
        if "properties:" not in part.lower():
            print(f"[WARN] Chunk {i} YAML has no explicit 'properties:' key; using fallback parser.", flush=True)

    if not chosen_header:
        # Minimal valid header if model omitted it in every chunk
        chosen_header = "system: VSS -> HAL\nplatform: AAOS"

    # Ensure merged bodies are indented under `properties:`
    indented_bodies: List[str] = []
    for body in merged_bodies:
        body_lines = body.splitlines()

        # If body already starts like "  - ..." or "- ...", normalize to "  - ..."
        normalized_lines: List[str] = []
        for ln in body_lines:
            if not ln.strip():
                normalized_lines.append(ln)
                continue
            if re.match(r"^\s*-\s+", ln):
                normalized_lines.append("  " + ln.lstrip())
            elif re.match(r"^\s{2,}-\s+", ln):
                # already indented enough
                normalized_lines.append(ln)
            else:
                # general line, indent by 2
                normalized_lines.append("  " + ln)

        indented_bodies.append("\n".join(normalized_lines).rstrip())

    merged_props = "\n".join(x for x in indented_bodies if x.strip()).rstrip()

    return f"{chosen_header}\nproperties:\n{merged_props}\n"


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
        print(
            f"[DEBUG] SpecYamlConverterAgent: converting chunk {i}/{len(chunks)} "
            f"(~{chunk.count('- Property ID')} properties)",
            flush=True,
        )
        yaml_parts.append(converter.run(chunk))

    return merge_converted_yaml(yaml_parts)


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

yaml_spec = convert_spec_with_chunking(
    converter=converter,
    spec_text=designer_simple_spec,
    chunk_size=40,           # tune: 20/40/60 depending on your machine/model
)

spec = load_hal_spec_from_yaml_text(yaml_spec)

ensure_aosp_layout(spec)
ArchitectAgent().run(spec)
