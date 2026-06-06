"""
agents/rag_dspy_cpp_agent.py
═══════════════════════════════════════════════════════════════════
Two classes live here intentionally:

  RAGDSPyCppAgent (mixin-based)
    — Used by RAGDSPyArchitectAgent via run(module_spec).
    — Plugs into the DSPy optimisation / saved-programs path.
    — DSPY_OUTPUT_FIELD = "cpp_impl" (matches ModernCppVehicleHardwareSignature).

  RagDspyCppAgent (standalone)
    — Used directly in multi_main_rag_dspy.py for the "Modern C++ VHAL Upgrade"
      section that generates the full 4-file set (header/impl/main/bp).
    — Does NOT use the mixin; calls AOSPRetriever directly.
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import dspy
from agents.rag_dspy_mixin import RAGDSPyMixin
from rag.aosp_retriever import AOSPRetriever, get_retriever
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions

# ── Shared constants (keep both classes in sync) ─────────────────────────────

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

_CONTRACT = """
=== Android 14 AIDL VHAL V3 contract — no exceptions ===
ARCHITECTURE:
  YourClass : public IVehicleHardware   ← you implement this (vendor seam)
  DefaultVehicleHal : public BnVehicle  ← AOSP owns this (binder layer)

  main() {
    auto hw   = std::make_unique<YourClass>();
    auto vhal = ndk::SharedRefBase::make<DefaultVehicleHal>(std::move(hw));
    AServiceManager_addService(vhal->asBinder().get(), instance.c_str());
  }

MANDATORY signatures (copy verbatim):
  std::vector<aidlvhal::VehiclePropConfig> getAllPropertyConfigs() const override;
  aidlvhal::StatusCode getValues(
      std::shared_ptr<const GetValuesCallback> callback,
      const std::vector<aidlvhal::GetValueRequest>& requests) const override;
  aidlvhal::StatusCode setValues(
      std::shared_ptr<const SetValuesCallback> callback,
      const std::vector<aidlvhal::SetValueRequest>& requests) override;
  void registerOnPropertyChangeEvent(
      std::unique_ptr<const PropertyChangeCallback> callback) override;
  void registerOnPropertySetErrorEvent(
      std::unique_ptr<const PropertySetErrorCallback> callback) override;
  DumpResult dump(const std::vector<std::string>& options) override;
  aidlvhal::StatusCode checkHealth() override;

getValues/setValues: invoke (*callback)(results) then return StatusCode::OK.

FORBIDDEN:
  HIDL_FETCH_*  |  #include <hidl/>  |  Return<>  |  BnVehicle base
  BnIVehicle    |  .valueType field  |  sync getValues(propIds, areas, out*)
=== END ===
"""


# ═══════════════════════════════════════════════════════════════════
# 1. Mixin-based agent — used by RAGDSPyArchitectAgent
# ═══════════════════════════════════════════════════════════════════

class RAGDSPyCppAgent(RAGDSPyMixin):
    AGENT_TYPE        = "cpp"
    DSPY_OUTPUT_FIELD = "cpp_impl"   # must match an OutputField of ModernCppVehicleHardwareSignature

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 6,
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

        aosp_context = self._retrieve_multi(_QUERIES)
        aosp_context = _CONTRACT + (aosp_context or "")

        output = self._generate(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )
        if not output:
            self._log(
                "DSPy returned empty — check that DSPY_OUTPUT_FIELD='cpp_impl' "
                "matches ModernCppVehicleHardwareSignature output fields"
            )
        return output


# ═══════════════════════════════════════════════════════════════════
# 2. Standalone agent — used directly in multi_main_rag_dspy.py
# ═══════════════════════════════════════════════════════════════════

class RagDspyCppAgent:
    """
    Standalone RAG+DSPy cpp agent (no mixin).
    Returns a dict with keys: header, impl, main_service, android_bp, violations.
    Used by the 'Modern C++ VHAL Upgrade' block in multi_main_rag_dspy.py.
    """

    def __init__(
        self,
        retriever:   AOSPRetriever = None,
        rag_db_path: str           = "rag/chroma_db",
        rag_top_k:   int           = 6,
        **kwargs,
    ):
        self.retriever  = retriever or get_retriever(db_path=rag_db_path)
        self.top_k      = rag_top_k
        self.predictor  = dspy.ChainOfThought(ModernCppVehicleHardwareSignature)
        self.assertions = CppVehicleAssertions(strict=False)

    def generate(
        self,
        vss_spec:      str,
        aidl_info:     dict,
        extra_context: str = "",
    ) -> dict:
        retrieved      = self.retriever.retrieve_multi(_QUERIES, agent_type="cpp",
                                                       top_k=self.top_k)
        retrieved_text = self.retriever.format_for_prompt(retrieved,
                                                          label="AOSP VHAL Reference")
        full_context   = _CONTRACT + "\n" + retrieved_text
        if extra_context:
            full_context = extra_context + "\n" + full_context

        result = self.predictor(
            vss_spec                = str(vss_spec),
            generated_aidl_info     = (
                f"Package: {aidl_info.get('package', '')}\n"
                f"Properties: {list(aidl_info.get('properties', {}).keys())}"
            ),
            retrieved_aosp_examples = full_context,
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

    def repair(
        self,
        previous_output: dict,
        violations:      list,
        vss_spec:        str,
        aidl_info:       dict,
    ) -> dict:
        summary = "\n".join(f"  [{i+1}] {v}" for i, v in enumerate(violations))
        extra   = (
            f"=== REPAIR — fix ALL violations ===\n{summary}\n"
            f"Regenerate complete files. Do not repeat any violation above.\n"
            f"=== END REPAIR ==="
        )
        return self.generate(vss_spec, aidl_info, extra_context=extra)