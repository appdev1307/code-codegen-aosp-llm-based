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
        lines = [
            "HAL Domain: " + domain,
            "Total Properties: " + str(len(chunk)),
            "",
            "Properties:",
        ]
        for p in chunk:
            if hasattr(p, "id"):
                lines.append("- Name: " + str(p.id))
                if hasattr(p, "type"):
                    lines.append("  Type: " + str(p.type))
                if hasattr(p, "access"):
                    lines.append("  Access: " + str(p.access))
            else:
                lines.append("- " + str(p))
        return "\n".join(lines)

    def run(self, module_spec) -> str:
        domain     = module_spec.domain
        properties = module_spec.to_llm_spec()
        prop_list  = module_spec.properties

        base          = DOMAIN_BASE.get(domain.lower(), 0x1000)
        base_hex      = hex(base)
        base_next_hex = hex(base + 1)

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
            "- Each enum constant is a property ID with hex value:\n"
            "    FIRST_PROPERTY = " + base_hex + ",\n"
            "    SECOND_PROPERTY = " + base_next_hex + ",\n"
            "- IMPORTANT: ALL property IDs MUST start at " + base_hex + " (domain base for " + domain.upper() + ")\n"
            "- DO NOT use 0x1000 as base unless domain is ADAS\n"
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
        else:
            self._log(
                "Large domain (" + str(len(prop_list)) + " props) — "
                "chunking into " + str(CHUNK_SIZE) + "-prop batches"
            )
            chunks = [prop_list[i:i+CHUNK_SIZE] for i in range(0, len(prop_list), CHUNK_SIZE)]
            enum_constants = []

            for i, chunk in enumerate(chunks):
                chunk_spec   = self._build_chunk_spec(domain, chunk)
                chunk_domain = domain + "_chunk" + str(i+1) + "of" + str(len(chunks))
                chunk_output = self._generate(
                    domain       = chunk_domain,
                    properties   = chunk_spec,
                    aosp_context = aosp_context,
                )
                if chunk_output:
                    constants = re.findall(
                        r"^\s+\w+\s*=\s*0x[0-9a-fA-F]+.*$",
                        chunk_output,
                        re.MULTILINE,
                    )
                    enum_constants.extend(constants)
                    self._log("  Chunk " + str(i+1) + ": " + str(len(constants)) + " constants")

            if enum_constants:
                output = (
                    "package android.hardware.automotive.vehicle;\n"
                    "@VintfStability\n"
                    '@Backing(type="int")\n'
                    "enum VehicleProperty" + domain.capitalize() + " {\n"
                    + "\n".join(enum_constants) + "\n"
                    + "}\n"
                )
                self._log("Merged " + str(len(enum_constants)) + " constants from " + str(len(chunks)) + " chunks")
            else:
                output = ""

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output