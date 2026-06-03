"""
agents/rag_dspy_cpp_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy VHAL C++ service implementation agent.

Root-cause fix: the canonical AIDL VHAL reference files
(DefaultVehicleHal.cpp, FakeVehicleHardware.cpp, VehicleService.cpp)
ARE in the aosp_cpp corpus, but generic queries
("getValues setValues implementation") ranked generic HALs
(biometrics, bluetooth, sensors) above them. The LLM was therefore
grounded in the wrong subsystem and produced non-compiling hybrids
(HIDL_FETCH_*, wrong getValues signatures).

This version uses vehicle-anchored multi-queries so retrieval surfaces
the real VHAL reference code, and a prompt that tells the LLM to follow
the retrieved reference signatures rather than invent them.

Uses ONLY the mixin's public API: self._retrieve_multi(queries),
self._generate(**signature_inputs). No direct ChromaDB access.
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyCppAgent(RAGDSPyMixin):
    AGENT_TYPE        = "cpp"
    DSPY_OUTPUT_FIELD = "cpp_code"

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 4,
        rag_db_path:       str = "rag/chroma_db",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )

    def run(self, module_spec) -> str:
        domain     = module_spec.domain
        properties = module_spec.to_llm_spec()

        # Vehicle-anchored queries. Naming the canonical reference files and
        # vehicle-specific symbols pulls the real VHAL impl to the top of the
        # aosp_cpp ranking instead of generic biometrics/bluetooth/sensors HALs.
        queries = [
            "DefaultVehicleHal IVehicleHardware automotive vehicle getAllPropertyConfigs",
            "FakeVehicleHardware automotive vehicle getValues setValues VehiclePropValue",
            "VehicleService automotive vehicle main AServiceManager_addService IVehicle",
        ]
        aosp_context = self._retrieve_multi(queries)

        cpp_constraint = (
            "\n=== Android 14 AIDL VHAL C++ contract — FOLLOW the retrieved reference code ===\n"
            "Generate a vendor IVehicleHardware implementation that mirrors the structure of the\n"
            "retrieved DefaultVehicleHal / FakeVehicleHardware / VehicleService examples above.\n"
            "Hard requirements (all visible in the retrieved reference):\n"
            "- namespace aidl::android::hardware::automotive::vehicle\n"
            "- ndk::ScopedAStatus return type (never HIDL Return<>)\n"
            "- std::vector (never hidl_vec); AIDL 'boolean', not raw bool in interface types\n"
            "- Register the service with a main() calling AServiceManager_addService(...),\n"
            "  exactly as VehicleService.cpp does. DO NOT emit HIDL_FETCH_* — that is HIDL.\n"
            "- getValues/setValues are ASYNCHRONOUS: match the EXACT signatures in the retrieved\n"
            "  DefaultVehicleHal.cpp (callback + request parcelable). DO NOT invent a synchronous\n"
            "  (propIds, out-vector) form — that signature does not exist in the AIDL interface.\n"
            "- Property IDs reference the generated VehiclePropertyAdas enum constants.\n"
            "- No hidl/ headers, no BpHw/BnHw, no '@2.0' types, no 'generates (' syntax.\n"
            "Treat the retrieved reference files as the authoritative template for every signature\n"
            "and for service registration. Do not deviate from their structure.\n"
            "=== END ===\n"
        )
        aosp_context = cpp_constraint + (aosp_context or "")

        output = self._generate(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )

        if not output:
            self._log("DSPy returned empty — check module or optimizer")
        return output
