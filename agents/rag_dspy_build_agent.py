"""
agents/rag_dspy_build_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy Android.bp build file generation agent (condition 3).

Retrieves real Android.bp examples from ChromaDB (aosp_build
collection) and uses a DSPy-optimised prompt to generate the
Android.bp build file for a VHAL HAL module.

Interface: agent.run(module_spec) → str
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyBuildAgent(RAGDSPyMixin):
    """
    Generates Android.bp build files for VHAL modules using RAG + DSPy.

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    """

    AGENT_TYPE        = "build"
    DSPY_OUTPUT_FIELD = "build_file"

    # Standard VHAL shared libraries — used in prompt and as reference
    _STANDARD_DEPS = (
        "libvhalclient libbinder_ndk libbase liblog "
        "libutils android.hardware.automotive.vehicle-V2-ndk"
    )

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
        Generate an Android.bp build file for the given HAL module.

        Parameters
        ----------
        module_spec : ModuleSpec
            Contains .domain (str) and .properties (list).

        Returns
        -------
        str — complete Android.bp content, or "" on failure
        """
        domain      = module_spec.domain
        module_name = f"vendor.vss.{domain.lower()}"

        # Retrieve Android.bp examples relevant to AIDL HAL modules
        queries = [
            f"aidl_interface Android.bp vendor stability vintf HAL",
            f"cc_binary vendor VHAL service shared_libs {domain.lower()}",
            f"android.hardware.automotive.vehicle Android.bp aidl",
        ]
        aosp_context = self._retrieve_multi(queries)

        output = self._generate(
            module_name  = module_name,
            dependencies = self._STANDARD_DEPS,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output