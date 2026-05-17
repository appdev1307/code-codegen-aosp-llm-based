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

        # Use multi-query retrieval for Android 14 AIDL-based VHAL patterns
        queries = [
            f"DefaultVehicleHal IVehicleHardware getAllPropertyConfigs AIDL",
            f"VehiclePropValue getValues setValues ndk aidl automotive vehicle",
            f"VehiclePropertyStore onSetProperty onGetProperty android 14",
        ]
        aosp_context = self._retrieve_multi(queries)

        # ── Inject Android 14 AIDL C++ constraint ────────────────
        cpp_constraint = (
            "\n=== CRITICAL: Android 14 AIDL C++ Rules ===\n"
            "You MUST follow these rules for the generated .cpp file:\n"
            "- Use AIDL namespace: aidl::android::hardware::automotive::vehicle\n"
            "- Use ndk::ScopedAStatus (NOT Return<void>, NOT hidl Return)\n"
            "- Use std::vector (NOT hidl_vec)\n"
            "- DO NOT include <hidl/Status.h> or any hidl/ headers\n"
            "- DO NOT use Void(), _hidl_cb, HIDL_FETCH_*, BpHw, BnHw\n"
            "- DO NOT use Return<> template (that is HIDL)\n"
            "- Include <aidl/android/hardware/automotive/vehicle/BnVehicle.h>\n"
            "- Include <VehicleHalTypes.h> and <VehicleUtils.h>\n"
            "- Use VehiclePropConfig for property definitions\n"
            "- Property IDs should reference VehiclePropertyAdas enum values\n"
            "- Extend IVehicleHardware or DefaultVehicleHal\n"
            "- Module name: vendor.vss.adas (NOT android.hardware.automotive.vehicle-service)\n"
            "- This is Android 14 AIDL C++ — NOT HIDL C++\n"
            "=== END RULES ===\n"
        )
        aosp_context = cpp_constraint + aosp_context

        output = self._generate(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")

        return output