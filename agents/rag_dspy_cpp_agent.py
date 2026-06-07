# agents/rag_dspy_cpp_agent.py
import dspy
from rag.aosp_retriever import AospRetriever
from dspy_opt.hal_signatures import ModernCppVehicleHardwareSignature, CppVehicleAssertions

class RagDspyCppAgent:
    """
    Modern standalone C++ agent for Android 14+ AIDL VHAL.
    Uses IVehicleHardware + DefaultVehicleHal wrapper.
    """
    def __init__(self, retriever: AospRetriever = None):
        self.retriever = retriever or AospRetriever()
        self.signature = ModernCppVehicleHardwareSignature()
        self.predictor = dspy.ChainOfThought(self.signature)
        self.assertions = CppVehicleAssertions(strict=False)

    def generate(self, vss_spec: dict, aidl_info: dict, extra_context: str = "") -> dict:
        """Generate modern Android 14+ C++ VHAL"""
        query = ("DefaultVehicleHal IVehicleHardware FakeVehicleHardware "
                 "GetValueRequest GetValuesCallback SetValueRequest SetValuesCallback "
                 "Android 14 VHAL")

        filter_dict = {"$and": [
            {"android_version": "14"},
            {"component": {"$in": ["default_vehicle_hal", "ivhicle_hardware", "fake_impl", "vhal"]}},
        ]}

        retrieved = self.retriever.retrieve(query=query, top_k=10, filter_dict=filter_dict)

        if not retrieved:
            print("[RagDspyCppAgent] WARNING: filtered retrieval empty, retrying without filter")
            retrieved = self.retriever.retrieve(query=query, top_k=10)

        retrieved_text = "\n\n".join(doc["page_content"] for doc in retrieved)

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
            "header": result.cpp_header,
            "impl": result.cpp_impl,
            "main_service": result.main_service,
            "android_bp": result.android_bp,
            "reasoning": getattr(result, "reasoning", ""),
            "violations": getattr(result, "violations", []),
        }

    def repair(self, previous_output: dict, violations: list, vss_spec: dict, aidl_info: dict) -> dict:
        """Repair using violations feedback"""
        violation_summary = "\n".join([f"- {v}" for v in violations])
        repair_context = f"""
=== REPAIR INSTRUCTIONS ===
Previous violations:
{violation_summary}

Fix ALL violations strictly according to Android 14+ AIDL V3 contract.
"""
        print("🔧 Repairing with violation feedback...")
        return self.generate(vss_spec, aidl_info, extra_context=repair_context)


print("✅ RagDspyCppAgent (Modern Android 14+ VHAL) loaded successfully")