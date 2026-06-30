"""
agents/rag_dspy_aidl_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy AIDL interface generation agent (condition 3).

Wraps the original VHAL AIDL agent. At generation time:
  1. Retrieves real .aidl examples from ChromaDB (aosp_aidl collection)
  2. Injects them as grounding context into the DSPy-optimised prompt
  3. Falls back to parent agent's generation if DSPy fails

Interface matches original VHALAIDLAgent.run(module_spec).
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import re
from agents.rag_dspy_mixin import RAGDSPyMixin

# Domain-specific base addresses for globally unique property IDs.
# These are 16-bit *local* offsets within a domain, used only to keep
# enum constants ordered and to give the LLM a concrete starting point.
# The FINAL emitted enum value is the full 32-bit AAOS-encoded prop ID
# (see _aaos_encode below) so domain C++ agents can `static_cast` it
# directly without any further re-encoding by VssGlueAgent.
DOMAIN_BASE = {
    "adas":          0x1000,
    "body":          0x2000,
    "cabin":         0x3000,
    "chassis":       0x4000,
    "hvac":          0x5000,
    "infotainment":  0x6000,
    "powertrain":    0x7000,
}

CHUNK_SIZE = 60  # Max properties per LLM call before chunking

# ── AAOS 32-bit property ID encoding ──────────────────────────────────
# Must stay in sync with VssGlueAgent._build_full_prop_id /
# agents/vss_glue_agent.py — both halves of the pipeline encode the
# SAME way so a domain enum value already IS the final 32-bit ID.
_VSS_GROUP = 0x20000000
_VSS_AREA  = 0x01000000
_TYPE_BITS = {
    "BOOLEAN": 0x00200000,
    "INT":     0x00400000,
    "FLOAT":   0x00600000,
    "STRING":  0x00100000,
    "BYTES":   0x00700000,
    "INT64":   0x00500000,
}
_TYPE_DEFAULT = 0x00e00000


def _aaos_encode(local_id: int, vtype: str = "INT") -> int:
    """Encode a 16-bit local property index into the full 32-bit AAOS
    VehicleProperty ID: VSS group | area:GLOBAL | type bits | local id.

    Keeping this identical to VssGlueAgent._build_full_prop_id means
    the enum constant the LLM (or fallback) emits in the .aidl file is
    already the correct value the C++ agents can static_cast directly —
    no second re-encoding pass is needed downstream.
    """
    type_bits = _TYPE_BITS.get((vtype or "INT").upper(), _TYPE_DEFAULT)
    return _VSS_GROUP | _VSS_AREA | type_bits | (local_id & 0xFFFF)


class RAGDSPyAIDLAgent(RAGDSPyMixin):

    AGENT_TYPE        = "aidl"
    DSPY_OUTPUT_FIELD = "aidl_code"

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )

    def _build_chunk_spec(self, domain: str, chunk: list) -> str:
        """Build compact property spec for one chunk.
        Format: NAME (TYPE, ACCESS) — one per line, easy for LLM to enumerate.
        """
        lines = [f"Domain: {domain}  |  {len(chunk)} properties in this chunk"]
        lines.append("Generate one enum constant per property, in order:")
        lines.append("")
        for p in chunk:
            if hasattr(p, "id"):
                name   = str(p.id)
                ptype  = str(getattr(p, "type",   "INT"))
                access = str(getattr(p, "access", "READ_WRITE"))
                lines.append(f"  {name}  ({ptype}, {access})")
            else:
                lines.append(f"  {p}")
        return "\n".join(lines)

    def _reencode_enum_output(self, output: str, domain: str, base: int, prop_list: list) -> str:
        """Force every enum constant in a single-shot LLM output to the
        correct full 32-bit AAOS ID, regardless of what the LLM wrote.

        This is the single-shot counterpart of the per-chunk re-encode
        done in the chunked branch of run() — keeping both paths
        deterministic means the final .aidl enum value is ALWAYS the
        real VHAL property ID the domain C++ agents can static_cast
        directly, never a bare 16-bit offset.
        """
        match = re.search(
            r"(enum\s+VehicleProperty\w+\s*\{)(.*?)(\})",
            output,
            re.DOTALL,
        )
        if not match:
            return output

        body = match.group(2)
        constants = re.findall(
            r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,?([^\n]*)$",
            body,
            re.MULTILINE,
        )
        if not constants:
            return output

        new_lines = []
        for j, (name, _llm_id, trailing) in enumerate(constants):
            prop_type = "INT"
            if j < len(prop_list):
                prop_type = str(getattr(prop_list[j], "type", "INT") or "INT")
            correct_id = hex(_aaos_encode(base + j, prop_type))
            trailing = trailing.strip()
            line = f"    {name} = {correct_id},"
            if trailing:
                if not trailing.startswith("//"):
                    trailing = "// " + trailing.lstrip("/").strip()
                line += f"  {trailing}"
            new_lines.append(line)

        new_body = "\n" + "\n".join(new_lines) + "\n"
        return output[:match.start(2)] + new_body + output[match.end(2):]

    def run(self, module_spec) -> str:
        domain     = module_spec.domain
        properties = module_spec.to_llm_spec()
        prop_list  = module_spec.properties

        base            = DOMAIN_BASE.get(domain.lower(), 0x1000)
        # Emit the FULL 32-bit AAOS-encoded ID in the prompt/example, not
        # the bare 16-bit local offset — the .aidl enum value becomes the
        # literal C++ constant the domain agents static_cast<int32_t>(),
        # so it must already be the real VHAL property ID.
        first_type     = (prop_list[0].type if prop_list and hasattr(prop_list[0], "type") else "INT") if prop_list else "INT"
        second_type    = (prop_list[1].type if len(prop_list) > 1 and hasattr(prop_list[1], "type") else "INT") if len(prop_list) > 1 else "INT"
        base_hex        = hex(_aaos_encode(base, first_type))
        base_next_hex   = hex(_aaos_encode(base + 1, second_type))

        query = (
            "VehicleProperty enum AIDL @Backing @VintfStability "
            "property constants " + domain + " android automotive vehicle"
        )
        aosp_context = self._retrieve(query)

        aidl_constraint = (
            "\n=== CRITICAL: Android 14 AIDL Property Enum Rules ===\n"
            "You MUST follow these rules for the generated .aidl file:\n"
            "- Package: android.hardware.automotive.vehicle (NO .V2_0 suffix)\n"
            "- Use @VintfStability annotation\n"
            '- Use @Backing(type="int") annotation\n'
            "- Declare an ENUM, NOT an interface: 'enum VehicleProperty" + domain.capitalize() + " { ... }'\n"
            "- The enum name MUST match the filename (VehicleProperty" + domain.capitalize() + ")\n"
            "- Each enum constant is the FULL 32-bit AAOS VehicleProperty ID (NOT a bare 16-bit offset):\n"
            "    FIRST_PROPERTY = " + base_hex + ",\n"
            "    SECOND_PROPERTY = " + base_next_hex + ",\n"
            "- IMPORTANT: ALL property IDs for this domain MUST start at " + base_hex + "\n"
            "  and increment only in the low 16 bits — the upper bits (group/area/type) stay fixed.\n"
            "- DO NOT use a bare 16-bit value like 0x1000 — that is NOT a valid VHAL prop ID on its own.\n"
            "- DO NOT generate 'interface IVehicleAdas' — that is WRONG\n"
            "- DO NOT generate getter/setter methods\n"
            "- DO NOT use 'oneway', 'out', 'throws', or 'import'\n"
            "- This file defines PROPERTY IDs only, like VehicleProperty.aidl\n"
            "- Access mode (READ/WRITE/READ_WRITE) goes in a comment, not in the enum\n"
            "- This is Android 14 AIDL — NOT HIDL, NOT Java\n"
            "\nExample of CORRECT output:\n"
            "package android.hardware.automotive.vehicle;\n"
            "@VintfStability\n"
            '@Backing(type="int")\n'
            "enum VehicleProperty" + domain.capitalize() + " {\n"
            "    FIRST_PROPERTY = " + base_hex + ", // READ_WRITE, GLOBAL, boolean\n"
            "    SECOND_PROPERTY = " + base_next_hex + ", // READ, GLOBAL, boolean\n"
            "}\n"
            "=== END RULES ===\n"
        )
        aosp_context = aidl_constraint + aosp_context

        # ── Chunked generation for large property sets ──────────────
        # LLM truncates output when property list > CHUNK_SIZE items.
        # Split into chunks, extract constants from each, merge into one enum.
        if len(prop_list) <= CHUNK_SIZE:
            output = self._generate(
                domain       = domain,
                properties   = properties,
                aosp_context = aosp_context,
            )
            if output:
                output = self._reencode_enum_output(output, domain, base, prop_list)
        else:
            self._log(
                f"Large domain ({len(prop_list)} props) — "
                f"chunking into {CHUNK_SIZE}-prop batches"
            )
            chunks = [prop_list[i:i+CHUNK_SIZE]
                      for i in range(0, len(prop_list), CHUNK_SIZE)]
            enum_constants = []   # list of (name, hex_id, comment) tuples
            global_index = 0      # tracks offset across all chunks for ID continuity

            for i, chunk in enumerate(chunks):
                chunk_base     = base + global_index
                chunk_base_hex = hex(chunk_base)
                chunk_base_next = hex(chunk_base + 1)

                # Build per-chunk aosp_context with correct base address
                chunk_constraint = (
                    f"\n=== CHUNK {i+1}/{len(chunks)}: {domain.upper()} domain ===\n"
                    f"Generate ONLY enum constants for these {len(chunk)} properties.\n"
                    f"CRITICAL: First constant MUST be {chunk_base_hex}, incrementing by 1.\n"
                    f"Do NOT output package/annotation/enum wrapper — constants ONLY.\n"
                    f"Format (one per line):\n"
                    f"    PROPERTY_NAME = {chunk_base_hex}, // TYPE, ACCESS, GLOBAL\n"
                    f"    NEXT_PROPERTY = {chunk_base_next}, // TYPE, ACCESS, GLOBAL\n"
                    f"=== END CHUNK RULES ===\n"
                )
                chunk_spec = self._build_chunk_spec(domain, chunk)
                chunk_output = self._generate(
                    domain       = f"{domain}_chunk{i+1}of{len(chunks)}",
                    properties   = chunk_spec,
                    aosp_context = chunk_constraint + aosp_context,
                )

                if chunk_output:
                    # Strip any wrapper the LLM added (package/enum/braces)
                    chunk_output = re.sub(r"^```[a-zA-Z]*\s*", "", chunk_output, flags=re.MULTILINE)
                    chunk_output = re.sub(r"^```\s*$", "", chunk_output, flags=re.MULTILINE)
                    chunk_output = re.sub(r"^\s*package\s+[\w.]+;\s*$", "", chunk_output, flags=re.MULTILINE)
                    chunk_output = re.sub(r"^\s*@\w+.*$", "", chunk_output, flags=re.MULTILINE)
                    chunk_output = re.sub(r"^\s*enum\s+\w+\s*\{", "", chunk_output, flags=re.MULTILINE)
                    chunk_output = re.sub(r"^\s*\}\s*$", "", chunk_output, flags=re.MULTILINE)

                    # Extract constant lines: NAME = 0xHEX, // ...
                    constants = re.findall(
                        r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+)([\s,//].*)?$",
                        chunk_output,
                        re.MULTILINE,
                    )

                    # Re-encode with correct sequential 32-bit AAOS IDs
                    # (override whatever (possibly bare 16-bit) value the
                    # LLM emitted). Type comes from the matching VSS
                    # property in `chunk` by position — falls back to INT
                    # if the LLM dropped/added a constant and positions
                    # drift, which is the safest default per
                    # _TYPE_DEFAULT/_TYPE_BITS above.
                    for j, (name, _llm_id, comment) in enumerate(constants):
                        prop_type = "INT"
                        if j < len(chunk):
                            prop_type = str(getattr(chunk[j], "type", "INT") or "INT")
                        correct_id = hex(_aaos_encode(chunk_base + j, prop_type))
                        comment_str = comment.strip().lstrip(",").strip() if comment else ""
                        if comment_str and not comment_str.startswith("//"):
                            comment_str = "// " + comment_str
                        line = f"    {name} = {correct_id},"
                        if comment_str:
                            line += f"  {comment_str}"
                        enum_constants.append(line)

                    parsed_count = len(constants)
                    global_index += parsed_count
                    self._log(
                        f"  Chunk {i+1}/{len(chunks)}: {parsed_count} constants "
                        f"(IDs {chunk_base_hex}..{hex(chunk_base + parsed_count - 1)})"
                    )

                    # Warn if chunk returned fewer constants than expected
                    if parsed_count < len(chunk):
                        self._log(
                            f"  ⚠ Chunk {i+1}: expected {len(chunk)}, got {parsed_count} "
                            f"— {len(chunk) - parsed_count} signals lost to LLM truncation"
                        )
                else:
                    self._log(f"  ⚠ Chunk {i+1}: empty output — skipping")

            if enum_constants:
                output = (
                    "package android.hardware.automotive.vehicle;\n"
                    "@VintfStability\n"
                    '@Backing(type="int")\n'
                    "enum VehicleProperty" + domain.capitalize() + " {\n"
                    + "\n".join(enum_constants) + "\n"
                    + "}\n"
                )
                self._log(
                    f"Merged {len(enum_constants)}/{len(prop_list)} constants "
                    f"from {len(chunks)} chunks (IDs {base_hex}..{hex(base + global_index - 1)})"
                )
            else:
                output = ""

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output