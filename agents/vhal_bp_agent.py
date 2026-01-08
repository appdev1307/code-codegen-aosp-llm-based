from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android build system expert.
Generate Android.bp for AIDL-based Vehicle HAL service.

Rules:
- cc_binary
- Install in /vendor/bin/hw
- Link libbinder_ndk
- Use correct AIDL interface
- Output only Android.bp
"""

_agent = BaseAgent(
    name="VHAL BP Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="Android.bp",
)

def generate_vhal_bp(spec: str):
    return _agent.run(spec)
