# agents/rag_dspy_cpp_agent.py
import dspy
from rag.aosp_retriever import AOSPRetriever
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions

class RagDspyCppAgent:
    def __init__(self, retriever=None, rag_db_path="rag/chroma_db", rag_top_k=8):
        self.retriever = retriever or AOSPRetriever(db_path=rag_db_path)
        self.top_k = rag_top_k
        self.predictor = dspy.ChainOfThought(ModernCppVehicleHardwareSignature)
        self.assertions = CppVehicleAssertions(strict=False)

    def generate(self, domain: str, properties: str, aosp_context: str = "") -> dict:
        """Main generation method - matches signature fields"""
        query = "DefaultVehicleHal IVehicleHardware FakeVehicleHardware GetValueRequest GetValuesCallback SetValueRequest SetValuesCallback Android 14 VHAL"

        retrieved = self.retriever.retrieve_multi([query], agent_type="cpp", top_k=self.top_k)
        retrieved_text = "\n\n".join(doc.get("text", doc.get("page_content", "")) for doc in retrieved)

        result = self.predictor(
            domain=domain,
            properties=properties,
            aosp_context=aosp_context or retrieved_text,
        )
        result = self.assertions(result)

        return {
            "header": getattr(result, "cpp_header", ""),
            "impl": getattr(result, "cpp_impl", ""),
            "main_service": getattr(result, "main_service", ""),
            "android_bp": getattr(result, "android_bp", ""),
            "reasoning": getattr(result, "reasoning", ""),
            "violations": getattr(result, "violations", []),
        }

    # Compatibility for C4 retry engine
    def _generate(self, domain: str, properties: str, aosp_context: str = "", **kwargs):
        """Alias for retry engine compatibility"""
        return self.generate(domain=domain, properties=properties, aosp_context=aosp_context)

    def repair(self, previous_output: dict, violations: list, domain: str, properties: str) -> dict:
        violation_summary = "\n".join(f"- {v}" for v in violations)
        extra = f"=== REPAIR: Fix these violations ===\n{violation_summary}"
        return self.generate(domain=domain, properties=properties, aosp_context=extra)


# Minimal wrapper for architect compatibility
class RAGDSPyCppAgent:
    def __init__(self, **kwargs):
        self.inner = RagDspyCppAgent(**kwargs)

    def run(self, module_spec):
        return self.inner.generate(
            domain=module_spec.domain,
            properties=module_spec.to_llm_spec()
        )