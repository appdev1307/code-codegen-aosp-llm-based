from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android build system expert for Android 14+ AIDL HAL.
Generate Android.bp for AIDL-based Vehicle HAL service.

Rules:
- cc_binary
- vendor: true (REQUIRED — HAL modules must be on vendor partition)
- relative_install_path: "hw"
- Link libbinder_ndk (NOT libhidlbase or libhwbinder)
- Use correct AIDL interface: android.hardware.automotive.vehicle-V3-ndk
- Do NOT link HIDL libraries (libhidltransport, android.hardware.automotive.vehicle@2.0)
- Output only Android.bp
"""

_agent = BaseAgent(
    name="VHAL BP Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="Android.bp",
)

def generate_vhal_bp(spec: str):
    return _agent.run(spec)
