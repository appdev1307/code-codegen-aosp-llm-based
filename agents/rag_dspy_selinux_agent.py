"""
agents/rag_dspy_selinux_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy SELinux policy generation agent (condition 3/4).

Retrieves real AOSP 14 AIDL .te policy examples from ChromaDB and
uses a DSPy-optimised prompt to generate SELinux Type Enforcement
policy files for VHAL services.

FIX (2026-06): RAG queries updated to retrieve AIDL-based patterns
only (hal_server_domain, init_daemon_domain, binder_use).
Old queries referenced hwservice/add_hwservice (HIDL) which caused
the LLM to generate legacy HIDL SELinux policy despite Layer 1/2
filtering.
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

        Returns
        -------
        str — complete SELinux .te policy content, or "" on failure
        """
        domain       = getattr(full_spec, "domain", "VEHICLE")
        service_name = f"vendor.vss.{domain.lower()}"

        # FIX: queries now target AIDL-based SELinux patterns only.
        # Old queries ("add_hwservice find_hwservice hwservice") retrieved
        # HIDL examples from ChromaDB, causing HIDL output even after
        # Layer 1 (indexer) and Layer 2 (mixin) filtering.
        queries = [
            f"hal_vehicle SELinux AIDL Android 14 type enforcement binder",
            f"{domain.lower()} vendor hal selinux te hal_server_domain init_daemon_domain",
            f"binder_use binder_call vndbinder_device hal_vehicle type domain",
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