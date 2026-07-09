"""
agents/rag_dspy_selinux_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy SELinux policy generation agent (condition 3/4).

Retrieves real AOSP 14 AIDL .te policy examples from ChromaDB and
uses a DSPy-optimised prompt to generate SELinux Type Enforcement
policy files for VHAL services.

FIX (2026-06): RAG queries updated to retrieve AIDL-based patterns
(hal_server_domain, init_daemon_domain, binder_use) instead of
HIDL patterns (hwservice/add_hwservice), which the old queries
retrieved and which caused legacy HIDL SELinux output despite
Layer 1/2 filtering. Superseded by the 2026-07 fix below.

FIX (2026-07): every VSS domain now runs inside the single shared
hal_vehicle_vss process (see agents/vss_glue_agent.py), so this
fragment must NOT declare a new per-domain daemon — the
hal_server_domain/init_daemon_domain pattern from the 2026-06 fix
above is now explicitly FORBIDDEN by SELinuxSignature. Queries
updated accordingly to retrieve allow-rule/vendor-data-file examples
instead, so retrieval no longer contradicts the current contract.
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

        # FIX (2026-07): queries updated again to match the current
        # SELinuxSignature contract — every VSS domain runs inside the
        # SHARED hal_vehicle_vss process (see agents/vss_glue_agent.py);
        # this fragment must NOT declare a new domain, so retrieving
        # "hal_server_domain"/"init_daemon_domain" examples (the OLD
        # per-domain-daemon pattern, now explicitly forbidden) actively
        # worked against the instruction. Queries now target real AOSP
        # examples of the pattern this fragment actually needs: allow
        # rules granting an existing domain access to a vendor data file
        # (matching the vss_hw_data_file permission the CPP agent's real
        # file-backed register store requires).
        queries = [
            f"SELinux allow rule vendor data file read write create Android 14",
            f"vendor data_file_type file_type declaration allow domain file dir",
            f"allow domain data_file_type dir search create file getattr open",
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