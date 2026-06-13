# agents/rag_dspy_cpp_agent.py
import dspy
from rag.aosp_retriever import AOSPRetriever
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions

# 4 vehicle-anchored queries — surface DefaultVehicleHal/FakeVehicleHardware
# above generic biometrics/bluetooth/sensors HALs in the corpus
_QUERIES = [
    "IVehicleHardware getAllPropertyConfigs getValues setValues "
    "registerOnPropertyChangeEvent DumpResult checkHealth",
    "GetValuesCallback GetValueRequest SetValuesCallback SetValueRequest "
    "VehiclePropValue automotive vehicle async",
    "DefaultVehicleHal AServiceManager_addService IVehicle main "
    "ndk SharedRefBase make automotive vehicle",
    "FakeVehicleHardware getAllPropertyConfigs VehiclePropConfig "
    "areaConfigs automotive vehicle implementation",
]

# Injected into every generation — explicit AIDL V3 contract
_CONTRACT = """
=== Android 14 AIDL VHAL V3 contract — NO EXCEPTIONS ===

CRITICAL INCLUDES — ALWAYS ADD THESE AT THE TOP OF HEADER FILE:
#include <aidl/android/hardware/automotive/vehicle/IVehicleHardware.h>
#include <android/log.h>
#include <ndk/ScopedAStatus.h>
#include <vector>
#include <memory>
#include <string>

NAMESPACE:
using namespace aidl::android::hardware::automotive::vehicle;

ARCHITECTURE:
  YourClass : public IVehicleHardware ← you implement this (vendor seam)
  DefaultVehicleHal : public BnVehicle ← AOSP owns this (binder layer)

MAIN SERVICE:
  auto hw = std::make_unique<YourClass>();
  auto vhal = ndk::SharedRefBase::make<DefaultVehicleHal>(std::move(hw));
  AServiceManager_addService(vhal->asBinder().get(), instance.c_str());

MANDATORY signatures (copy style exactly):
  std::vector<aidlvhal::VehiclePropConfig> getAllPropertyConfigs() const override;
  aidlvhal::StatusCode getValues(
      std::shared_ptr<const GetValuesCallback> callback,
      const std::vector<aidlvhal::GetValueRequest>& requests) const override;
  aidlvhal::StatusCode setValues(
      std::shared_ptr<const SetValuesCallback> callback,
      const std::vector<aidlvhal::SetValueRequest>& requests) override;

FORBIDDEN:
  HIDL_FETCH_* | #include <hidl/> | Return<> | BnVehicle | BnIVehicle | .valueType | sync getValues

Start every .h file with the includes above. Do not omit them.
"""


class RagDspyCppAgent:
    def __init__(self, retriever=None, rag_db_path="rag/chroma_db", rag_top_k=8, **kwargs):
        self.retriever  = retriever or AOSPRetriever(db_path=rag_db_path)
        self.top_k      = rag_top_k
        self.predictor  = dspy.ChainOfThought(ModernCppVehicleHardwareSignature)
        self.assertions = CppVehicleAssertions(strict=False)

    def generate(
        self,
        domain:        str,
        properties:    str,
        extra_context: str = "",
    ) -> dict:
        # Always retrieve with all 4 vehicle-anchored queries
        retrieved     = self.retriever.retrieve_multi(_QUERIES, agent_type="cpp",
                                                      top_k=self.top_k)
        retrieved_text = "\n\n".join(
            doc.get("text", doc.get("page_content", "")) for doc in retrieved
        )

        # Always combine: contract + RAG + any repair/extra context
        # Never use "or" — repair must have BOTH violation feedback AND RAG grounding
        aosp_context = _CONTRACT + "\n" + retrieved_text
        if extra_context:
            aosp_context = extra_context + "\n" + aosp_context

        result = self.predictor(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )
        result = self.assertions(result)

        return {
            "header":       getattr(result, "cpp_header",   "") or "",
            "impl":         getattr(result, "cpp_impl",     "") or "",
            "main_service": getattr(result, "main_service", "") or "",
            "android_bp":   getattr(result, "android_bp",   "") or "",
            "reasoning":    getattr(result, "reasoning",    "") or "",
            "violations":   getattr(result, "violations",   []),
        }

    def _generate(self, domain: str, properties: str, aosp_context: str = "", **kwargs) -> str:
        """Alias for C4 retry engine — returns impl string, not dict."""
        out = self.generate(domain=domain, properties=properties,
                            extra_context=aosp_context)
        return out.get("impl", "")

    def repair(
        self,
        previous_output: dict,
        violations:      list,
        domain:          str,
        properties:      str,
    ) -> dict:
        summary = "\n".join(f"  [{i+1}] {v}" for i, v in enumerate(violations))
        extra   = (
            f"=== REPAIR — fix ALL violations before anything else ===\n"
            f"{summary}\n"
            f"Regenerate complete files. Do not repeat any violation.\n"
            f"=== END REPAIR ==="
        )
        return self.generate(domain, properties, extra_context=extra)


class RAGDSPyCppAgent:
    """Thin wrapper for RAGDSPyArchitectAgent and C4 retry engine compatibility."""

    def __init__(self, **kwargs):
        self.inner = RagDspyCppAgent(**kwargs)

    def run(self, module_spec) -> str:
        """Called by architect — returns impl string (not dict)."""
        out = self.inner.generate(
            domain     = module_spec.domain,
            properties = module_spec.to_llm_spec(),
        )
        return out.get("impl", "")

    def _generate(self, domain: str, properties: str,
                  aosp_context: str = "", **kwargs) -> str:
        """Called by C4 retry engine."""
        return self.inner._generate(domain=domain, properties=properties,
                                    aosp_context=aosp_context)