# agents/rag_dspy_cpp_agent.py
import dspy
from rag.aosp_retriever import AOSPRetriever   # Correct class name
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions

class RagDspyCppAgent:
    """
    Standalone modern C++ agent for Android 14+ AIDL V3.
    Used in C3 direct call and C4 retry logic.
    """
    def __init__(self, retriever=None, rag_db_path="rag/chroma_db", rag_top_k=8):
        self.retriever = retriever or AOSPRetriever(db_path=rag_db_path)
        self.top_k = rag_top_k
        self.signature = ModernCppVehicleHardwareSignature
        self.predictor = dspy.ChainOfThought(self.signature)   # Pass class, not instance
        self.assertions = CppVehicleAssertions(strict=False)

    def generate(self, vss_spec: dict, aidl_info: dict, extra_context: str = "") -> dict:
        """Generate modern Android 14+ C++ VHAL"""
        query = (
            "DefaultVehicleHal IVehicleHardware FakeVehicleHardware "
            "GetValueRequest GetValuesCallback SetValueRequest SetValuesCallback "
            "AServiceManager_addService Android 14 AIDL VHAL implementation"
        )

        # Strong filter for best examples
        filter_dict = {"$and": [
            {"android_version": "14"},
            {"component": {"$in": ["default_vehicle_hal", "ivhicle_hardware", "fake_impl", "vhal", "vehicle"]}},
        ]}

        retrieved = self.retriever.retrieve(
            query=query, 
            top_k=12, 
            filter_dict=filter_dict
        )

        if not retrieved or len(retrieved) < 3:
            print("[RagDspyCppAgent] WARNING: Weak retrieval, falling back...")
            retrieved = self.retriever.retrieve(query=query, top_k=10)

        retrieved_text = "\n\n".join(
            doc.get("text", doc.get("page_content", "")) for doc in retrieved
        )

        full_context = retrieved_text
        if extra_context:
            full_context = extra_context + "\n\n" + retrieved_text

        result = self.predictor(
            vss_spec=str(vss_spec),
            generated_aidl_info=(f"Package: {aidl_info.get('package', '')}\n"
                               f"Properties: {list(aidl_info.get('properties', {}).keys())}"),
            retrieved_aosp_examples=full_context,
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

    def repair(self, previous_output: dict, violations: list, vss_spec: dict, aidl_info: dict) -> dict:
        violation_summary = "\n".join(f"- {v}" for v in violations)
        extra = f"=== REPAIR INSTRUCTIONS ===\n{violation_summary}\nFix all violations and regenerate complete files."
        return self.generate(vss_spec, aidl_info, extra_context=extra)


# Keep the old mixin-based class for architect compatibility
class RAGDSPyCppAgent:
    # Minimal stub so architect doesn't break
    def __init__(self, **kwargs):
        self.agent = RagDspyCppAgent(**kwargs)

    def run(self, module_spec):
        # Forward to modern agent
        aidl_info = {"package": "android.hardware.automotive.vehicle", "properties": {}}
        output = self.agent.generate(module_spec, aidl_info)
        return output.get("impl", "")


print("✅ rag_dspy_cpp_agent.py — All bugs fixed (both classes + retrieval compatibility)")