import os
from llm_client import call_llm


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_dir = "output/vhal_aidl"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are an Android Automotive OS expert.

Target:
- AAOS Vehicle HAL
- AIDL-based (Android 12+)
- NO HIDL

Generate COMPLETE, REALISTIC, COMPILABLE AIDL artifacts.

You MUST generate the following files:
1. android/hardware/automotive/vehicle/IVehicle.aidl
2. android/hardware/automotive/vehicle/IVehicleCallback.aidl
3. android/hardware/automotive/vehicle/VehiclePropValue.aidl
4. android/hardware/automotive/vehicle/VehicleProperty.aidl
5. Android.bp
6. manifest.xml

Rules:
- Use correct AIDL syntax
- Use parcelable where appropriate
- No placeholders
- No explanations

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Specification:
{spec}
"""

    def run(self, spec: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec))

        os.makedirs(self.output_dir, exist_ok=True)
        self._split_and_write_files(result)

        print(f"[DEBUG] {self.name}: output -> {self.output_dir}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _split_and_write_files(self, text: str):
        current_file = None
        buffer = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current_file:
                    self._write_file(current_file, buffer)
                current_file = line.replace("--- FILE:", "").replace("---", "").strip()
                buffer = []
            else:
                buffer.append(line)

        if current_file:
            self._write_file(current_file, buffer)

    def _write_file(self, relative_path: str, lines: list):
        full_path = os.path.join(self.output_dir, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write("\n".join(lines))


# ✅ MODULE-LEVEL WRAPPER (THIS IS THE KEY)
def generate_vhal_aidl(spec: str):
    agent = VHALAidlAgent()
    return agent.run(spec)
