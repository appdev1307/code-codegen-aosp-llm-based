"""
agents/rag_dspy_cpp_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy VHAL C++ service implementation agent (condition 3).

Retrieves real .cpp/.h VHAL examples from ChromaDB (aosp_cpp
collection) and uses a DSPy-optimised prompt to generate the
C++ service implementation file.

Interface matches original VHALCppAgent.run(module_spec).
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyCppAgent(RAGDSPyMixin):
    """
    Generates VHAL C++ service implementation files using RAG + DSPy.

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    """

    AGENT_TYPE        = "cpp"
    DSPY_OUTPUT_FIELD = "cpp_code"

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
        Generate a VHAL C++ service implementation for the given module.

        Parameters
        ----------
        module_spec : ModuleSpec
            Contains .domain (str), .properties (list), .to_llm_spec()

        Returns
        -------
        str — complete .cpp file content, or "" on failure
        """
        domain     = module_spec.domain
        properties = module_spec.to_llm_spec()

        # Use multi-query retrieval: one for service class, one for property types
        prop_types = " ".join(
            getattr(p, "type", "") for p in module_spec.properties[:5]
        )
        queries = [
            f"{domain} VHAL C++ service IVehicleHardware implementation",
            f"getAllPropertyConfigs getValues setValues {prop_types} VehiclePropValue",
            f"android automotive vehicle hal {domain.lower()} cpp header",
        ]
        aosp_context = self._retrieve_multi(queries)

        output = self._generate(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output