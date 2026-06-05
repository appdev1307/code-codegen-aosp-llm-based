"""
agents/rag_dspy_cpp_agent.py
"""
from __future__ import annotations

import dspy
from rag.aosp_retriever import AOSPRetriever, get_retriever
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions


class RAGDSPyCppAgent:
    """
    Standalone RAG+DSPy agent for VHAL C++ generation.
    Uses AOSPRetriever directly (no mixin dependency).
    Class alias RagDspyCppAgent provided for backward compatibility.
    """

    def __init__(self, retriever: AOSPRetriever = None,
                 rag_db_path: str = "rag/chroma_db",
                 rag_top_k: int = 6, **kwargs):
        self.retriever  = retriever or get_retriever(db_path=rag_db_path)
        self.top_k      = rag_top_k
        # Pass the CLASS (not an instance) to ChainOfThought
        self.predictor  = dspy.ChainOfThought(ModernCppVehicleHardwareSignature)
        self.assertions = CppVehicleAssertions(strict=False)

    # ── generation ───────────────────────────────────────────────────────────

    def generate(self, vss_spec: dict, aidl_info: dict,
                 extra_context: str = "") -> dict:
        queries = [
            "IVehicleHardware getAllPropertyConfigs getValues setValues "
            "registerOnPropertyChangeEvent DumpResult checkHealth",
            "GetValuesCallback GetValueRequest SetValuesCallback SetValueRequest "
            "VehiclePropValue automotive vehicle async",
            "DefaultVehicleHal AServiceManager_addService IVehicle main "
            "ndk SharedRefBase make automotive vehicle",
            "FakeVehicleHardware getAllPropertyConfigs VehiclePropConfig "
            "areaConfigs automotive vehicle implementation",
        ]

        retrieved     = self.retriever.retrieve_multi(queries, agent_type="cpp",
                                                      top_k=self.top_k)
        retrieved_text = self.retriever.format_for_prompt(retrieved,
                                                          label="AOSP VHAL Reference")

        contract = """
=== Android 14 AIDL VHAL V3 contract — no exceptions ===
ARCHITECTURE:
  YourClass : public IVehicleHardware   ← you implement this
  DefaultVehicleHal : public BnVehicle  ← AOSP owns this; wrap yours inside it

MANDATORY signatures (copy verbatim):
  std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;
  StatusCode getValues(std::shared_ptr<const GetValuesCallback> callback,
      const std::vector<GetValueRequest>& requests) const override;
  StatusCode setValues(std::shared_ptr<const SetValuesCallback> callback,
      const std::vector<SetValueRequest>& requests) override;
  void registerOnPropertyChangeEvent(
      std::unique_ptr<const PropertyChangeCallback> callback) override;
  void registerOnPropertySetErrorEvent(
      std::unique_ptr<const PropertySetErrorCallback> callback) override;
  DumpResult dump(const std::vector<std::string>& options) override;
  StatusCode checkHealth() override;

main(): auto vhal = ndk::SharedRefBase::make<DefaultVehicleHal>(std::move(hw));
        AServiceManager_addService(vhal->asBinder().get(), instance.c_str());

FORBIDDEN — any of these = wrong generation:
  HIDL_FETCH_*  |  #include <hidl/>  |  Return<>  |  BnVehicle base
  BnIVehicle    |  .valueType field  |  sync getValues(propIds, areas, out*)
=== END ===
"""
        full_context = contract + "\n" + retrieved_text
        if extra_context:
            full_context = extra_context + "\n" + full_context

        result = self.predictor(
            vss_spec=str(vss_spec),
            generated_aidl_info=(
                f"Package: {aidl_info.get('package', '')}\n"
                f"Properties: {list(aidl_info.get('properties', {}).keys())}"
            ),
            retrieved_aosp_examples=full_context,
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

    def repair(self, previous_output: dict, violations: list,
               vss_spec: dict, aidl_info: dict) -> dict:
        summary = "\n".join(f"  [{i+1}] {v}" for i, v in enumerate(violations))
        extra   = (
            f"=== REPAIR — fix ALL violations before anything else ===\n"
            f"{summary}\n"
            f"Regenerate the complete files. Do not repeat any violation above.\n"
            f"=== END REPAIR ==="
        )
        return self.generate(vss_spec, aidl_info, extra_context=extra)


# backward-compat alias
RagDspyCppAgent = RAGDSPyCppAgent