from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive Vehicle HAL expert.
Generate correct, AOSP-compliant VHAL C++ code.
"""

_agent = BaseAgent(
    name="VHAL Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="VHAL.cpp",
)

def generate_vhal(spec: str):
    return _agent.run(spec)
