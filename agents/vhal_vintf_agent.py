from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive expert.
Generate VINTF manifest XML for AIDL-based Vehicle HAL.

Rules:
- Use android.hardware.automotive.vehicle
- Format must match AOSP VINTF schema
- Only output XML
"""

_agent = BaseAgent(
    name="VHAL VINTF Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="android.hardware.automotive.vehicle-service.xml",
)

def generate_vhal_vintf(spec: str):
    return _agent.run(spec)
