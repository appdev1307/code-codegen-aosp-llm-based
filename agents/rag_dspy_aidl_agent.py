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
from agents.rag_dspy_mixin import RAGDSPyMixin

# Domain-specific base addresses for globally unique property IDs.
# Each domain occupies a non-overlapping hex range so CarPropertyManager
# can distinguish properties across domains without conflict.
DOMAIN_BASE = {
    "adas":          0x1000,
    "body":          0x2000,
    "cabin":         0x3000,
    "chassis":       0x4000,
    "hvac":          0x5000,
    "infotainment":  0x6000,
    "powertrain":    0x7000,
}


class RAGDSPyAIDLAgent(RAGDSPyMixin):
    """
    Generates AOSP VHAL AIDL interface definitions using RAG + DSPy.

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    """

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

    def run(self, module_spec) -> str:
        """
        Generate an AIDL interface definition for the given module.

        Parameters
        ----------
        module_spec : ModuleSpec
            Contains .domain (str) and .properties (list) and .to_llm_spec()

        Returns
        -------
        str — complete .aidl file content, or "" on failure
        """
        domain     = module_spec.domain
        properties = module_spec.to_llm_spec()

        # Compute domain-specific base address for globally unique property IDs
        base = DOMAIN_BASE.get(domain.lower(), 0x1000)
        base_hex = hex(base)
        base_next_hex = hex(base + 1)

        # Build a targeted RAG query — request property ENUM examples,
        # NOT interface definitions. The word "interface" biases retrieval
        # toward IVehicle.aidl (the only interface), when we actually need
        # VehicleProperty.aidl (the enum pattern).
        query = (
            f"VehicleProperty enum AIDL @Backing @VintfStability "
            f"property constants {domain} android automotive vehicle"
        )

        # Retrieve AOSP .aidl examples as grounding context
        aosp_context = self._retrieve(query)

        # ── Inject explicit Android 14 AIDL-only constraint ──────
        # The AIDL file defines a PROPERTY ENUM (constant IDs), NOT a
        # service interface. The IVehicle interface already exists in AOSP.
        # This file adds new property constants in the same pattern as
        # VehicleProperty.aidl — an @Backing(type="int") enum.
        aidl_constraint = (
            "\n=== CRITICAL: Android 14 AIDL Property Enum Rules ===\n"
            "You MUST follow these rules for the generated .aidl file:\n"
            "- Package: android.hardware.automotive.vehicle (NO .V2_0 suffix)\n"
            "- Use @VintfStability annotation\n"
            "- Use @Backing(type=\"int\") annotation\n"
            f"- Declare an ENUM, NOT an interface: 'enum VehicleProperty{domain.capitalize()} {{ ... }}'\n"
            f"- The enum name MUST match the filename (VehicleProperty{domain.capitalize()})\n"
            "- Each enum constant is a property ID with hex value:\n"
            f"    FIRST_PROPERTY = {base_hex},\n"
            f"    SECOND_PROPERTY = {base_next_hex},\n"
            f"- IMPORTANT: ALL property IDs MUST start at {base_hex} (domain base for {domain.upper()})\n"
            f"- DO NOT use 0x1000 as base unless domain is ADAS — each domain has unique base\n"
            "- DO NOT generate 'interface IVehicleAdas' — that is WRONG\n"
            "- DO NOT generate getter/setter methods (boolean getX(), void setX())\n"
            "- DO NOT use 'oneway', 'out', 'throws', or 'import'\n"
            "- This file defines PROPERTY IDs only, like VehicleProperty.aidl\n"
            "- Access mode (READ/WRITE/READ_WRITE) goes in a comment, not in the enum\n"
            "- This is Android 14 AIDL — NOT HIDL, NOT Java\n"
            f"\nExample of CORRECT output (domain={domain.upper()}, base={base_hex}):\n"
            "package android.hardware.automotive.vehicle;\n"
            "@VintfStability\n"
            "@Backing(type=\"int\")\n"
            f"enum VehicleProperty{domain.capitalize()} {{\n"
            f"    FIRST_PROPERTY = {base_hex}, // READ_WRITE, GLOBAL, boolean\n"
            f"    SECOND_PROPERTY = {base_next_hex}, // READ, GLOBAL, boolean\n"
            "}\n"
            "=== END RULES ===\n"
        )
        aosp_context = aidl_constraint + aosp_context

        # Generate via DSPy optimised prompt
        output = self._generate(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output