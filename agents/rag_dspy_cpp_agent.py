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
#include <IVehicleHardware.h>
#include <android/log.h>
#include <android/binder_manager.h>
#include <android/binder_status.h>
#include <vector>
#include <memory>
#include <string>

NAMESPACE:
using namespace aidl::android::hardware::automotive::vehicle;

ARCHITECTURE:
  VehicleHalService{Domain} : public IVehicleHardware  ← you implement this (domain-specific)
  e.g. VehicleHalServiceAdas, VehicleHalServiceBody, VehicleHalServiceCabin, etc.
  Each domain has its OWN class with a UNIQUE name — never use VssVehicleHardware.
  DefaultVehicleHal  ← AOSP binder layer (do NOT subclass this)

CLASS NAMING CONVENTION:
  Domain ADAS        → class VehicleHalServiceAdas : public IVehicleHardware
  Domain BODY        → class VehicleHalServiceBody : public IVehicleHardware
  Domain CABIN       → class VehicleHalServiceCabin : public IVehicleHardware
  Domain CHASSIS     → class VehicleHalServiceChassis : public IVehicleHardware
  Domain HVAC        → class VehicleHalServiceHvac : public IVehicleHardware
  Domain INFOTAINMENT → class VehicleHalServiceInfotainment : public IVehicleHardware
  Domain POWERTRAIN  → class VehicleHalServicePowertrain : public IVehicleHardware

PROP IDs:
  Use enum constant names from the generated AIDL headers.
  ALWAYS include the domain header at the top of BOTH .h and .cpp files:
  #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyAdas.h>   // for ADAS
  #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyBody.h>    // for BODY
  etc.

  Then use enum names with static_cast:
  {.prop = static_cast<int32_t>(VehiclePropertyAdas::VEHICLE_CHILDREN_ADAS_CHILDREN_EBA_CHILDREN_ISENABLED),
   .access = VehiclePropertyAccess::READ_WRITE}

  The enum names come from the AIDL enum provided in the properties section.
  Do NOT use raw hex values like 0x1000 — those are 16-bit raw values, not valid VHAL prop IDs.
  Do NOT use placeholder IDs like 0x12345678.

HEADER FILE (VehicleHalService{Domain}.h):
  Declare class VehicleHalService{Domain} : public IVehicleHardware
  with getAllPropertyConfigs(), getValues(), setValues() etc.

IMPLEMENTATION FILE (VehicleHalService{Domain}.cpp):
  Implement getAllPropertyConfigs() returning ONLY the props for this domain.
  Use prop IDs from the AIDL enum — exact hex values.

MANDATORY signatures — Android 14 IVehicleHardware API (NOT Android 13):
Android 14 uses function types defined in IVehicleHardware.h:
  using GetValuesCallback = std::function<void(std::vector<GetValueResult>)>;
  using SetValuesCallback = std::function<void(std::vector<SetValueResult>)>;
  using PropertyChangeCallback = std::function<void(std::vector<VehiclePropValue>)>;
  using PropertySetErrorCallback = std::function<void(std::vector<SetValueErrorEvent>)>;

CORRECT Android 14 method signatures:
  std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;
  StatusCode getValues(
      std::shared_ptr<const GetValuesCallback> callback,
      const std::vector<GetValueRequest>& requests) const override;
  StatusCode setValues(
      std::shared_ptr<const SetValuesCallback> callback,
      const std::vector<SetValueRequest>& requests) override;
  StatusCode updateSampleRate(int32_t cookie, int32_t propId, float sampleRate) override;
  StatusCode subscribe(const SubscribeOptions& options) override;
  StatusCode unsubscribe(int32_t cookie, int32_t propId) override;
  DumpResult dump(const std::vector<std::string>& options) override;
  StatusCode checkHealth() override;
  void registerOnPropertyChangeEvent(
      std::unique_ptr<const PropertyChangeCallback> callback) override;
  void registerOnPropertySetErrorEvent(
      std::unique_ptr<const PropertySetErrorCallback> callback) override;

FORBIDDEN Android 13 types (do NOT use):
  IOnPropertyChangeCallback, IOnPropertySetErrorCallback — Android 13 only
  VssVehicleHardwareImpl — does not exist in Android 14

FORBIDDEN:
  #include <hidl/>          — HIDL headers
  #include <ndk/ScopedAStatus.h>  — wrong path, use <android/binder_status.h>
  #include <binder/AServiceManager.h>  — wrong, use <android/binder_manager.h>
  #include <aidl/android/hardware/automotive/vehicle/IVehicleHardware.h>  — wrong path, use <IVehicleHardware.h>
  #include <aidl/android/hardware/automotive/vehicle/DefaultVehicleHal.h>  — wrong path, use <DefaultVehicleHal.h>
  HIDL_FETCH_* | Return<> | BnVehicle | BnIVehicle | .valueType | sync getValues
  aidlvhal:: prefix — use namespace directly via using namespace

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

    def run(self, module_spec) -> dict:
        """Called by architect — returns dict with header + impl."""
        out = self.inner.generate(
            domain     = module_spec.domain,
            properties = module_spec.to_llm_spec(),
        )
        return out  # dict with header, impl, main_service, android_bp

    def _generate(self, domain: str, properties: str,
                  aosp_context: str = "", **kwargs) -> str:
        """Called by C4 retry engine."""
        return self.inner._generate(domain=domain, properties=properties,
                                    aosp_context=aosp_context)