"""
agents/rag_dspy_selinux_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy SELinux policy generation agent (condition 3).

Retrieves real .te policy examples from ChromaDB (aosp_selinux
collection) and uses a DSPy-optimised prompt to generate the
SELinux Type Enforcement policy file for a VHAL service.

Interface matches original generate_selinux(full_spec) but as
an instantiable class for consistency with other RAG+DSPy agents.

Called from multi_main_rag_dspy.py:
    agent = RAGDSPySELinuxAgent(...)
    agent.run(full_spec)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPySELinuxAgent(RAGDSPyMixin):
    """
    Generates SELinux .te policy files for VHAL services using RAG + DSPy.

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    """

    AGENT_TYPE        = "selinux"
    DSPY_OUTPUT_FIELD = "policy"

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

    def run(self, full_spec) -> str:
        """
        Generate a SELinux .te policy file for all modules in full_spec.

        Parameters
        ----------
        full_spec : HALSpec
            Full pipeline spec with .domain and .properties attributes.
            Same object passed to the original generate_selinux(full_spec).

        Returns
        -------
        str — complete SELinux .te policy content, or "" on failure
        """
        domain       = getattr(full_spec, "domain", "VEHICLE")
        service_name = f"vendor.vss.{domain.lower()}"

        # Retrieve real AOSP SELinux policy examples
        queries = [
            f"hal_vehicle SELinux policy binder hwservice allow",
            f"{domain.lower()} vendor hal selinux te type attribute",
            f"add_hwservice find_hwservice binder_call vhal",
        ]
        aosp_context = self._retrieve_multi(queries)

        output = self._generate(
            domain       = domain,
            service_name = service_name,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output