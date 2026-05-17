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

        # Build a targeted RAG query using domain + property type mix
        prop_types = " ".join(
            getattr(p, "type", "") for p in module_spec.properties[:5]
        )
        query = (
            f"{domain} VHAL AIDL interface definition "
            f"{prop_types} boolean int float android HAL"
        )

        # Retrieve AOSP .aidl examples as grounding context
        aosp_context = self._retrieve(query)

        # ── Inject explicit Android 14 AIDL-only constraint ──────
        # This prevents the LLM from generating HIDL patterns even if
        # its training data or (leaked) RAG context contains HIDL examples.
        aidl_constraint = (
            "\n=== CRITICAL: Android 14 AIDL-ONLY Rules ===\n"
            "You MUST follow these rules for the generated .aidl file:\n"
            "- Package: android.hardware.automotive.vehicle (NO .V2_0 suffix)\n"
            "- Use @VintfStability annotation on the interface\n"
            "- Return types directly: boolean getX(); NOT void getX(out bool)\n"
            "- NO 'oneway' keyword\n"
            "- NO 'out' parameters\n"
            "- NO 'throws RemoteException'\n"
            "- NO 'import android.os.RemoteException'\n"
            "- NO 'generates (...)' syntax (that is HIDL, not AIDL)\n"
            "- Use 'boolean' not 'bool' for boolean types\n"
            "- Use 'void setX(boolean val)' for setters\n"
            "- This is Android 14 AIDL — NOT HIDL, NOT Java\n"
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