from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive Vehicle HAL expert.
Generate correct, AOSP-compliant VHAL C++ code.
MANDATORY REQUIREMENTS (FAIL IF MISSING):
- MUST include <android/hardware/automotive/vehicle/2.0/types.hal>
- MUST use VehiclePropValue struct
- MUST implement get(), set(), subscribe()
- MUST compile against AOSP AAOS 13/14
- DO NOT provide example or pseudo code
"""

_agent = BaseAgent(
    name="VHAL Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="VHAL.cpp",
)

def generate_vhal(spec: str):
    return _agent.run(spec)
