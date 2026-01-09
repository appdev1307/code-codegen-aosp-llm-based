from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_dir = "output/vhal_aidl"
        self.writer = SafeWriter(self.output_dir)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android Automotive OS architect.

Your task:
Generate AIDL definitions for Vehicle HAL based strictly on AOSP standards.

Rules:
- Package MUST be: android.hardware.automotive.vehicle
- AIDL only (Android 12+), NO HIDL
- IVehicle MUST declare EXACTLY:
    VehiclePropValue get(int propId, int areaId);
    void set(in VehiclePropValue value);
- IVehicleCallback MUST exist
- VehiclePropValue MUST be declared as parcelable
- Use correct AIDL syntax
- No placeholders
- No explanations
- No comments describing intent

IMPORTANT:
- ALL file paths MUST be RELATIVE
- DO NOT generate absolute paths (/system, /vendor, /etc, /)
- Use AOSP-style relative paths only
  (e.g. android/hardware/automotive/vehicle/IVehicle.aidl)

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Specification:
{spec_text}
"""

    def run(self, spec_text: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec_text))
        if not result.strip():
            raise RuntimeError("[LLM ERROR] Empty VHAL AIDL output")

        self._write_files(result)

        print(f"[DEBUG] {self.name}: output -> {self.output_dir}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self.writer.write(current, "\n".join(buf))
                current = (
                    line.replace("--- FILE:", "")
                        .replace("---", "")
                        .strip()
                )
                buf = []
            else:
                buf.append(line)

        if current:
            self.writer.write(current, "\n".join(buf))


def generate_vhal_aidl(spec):
    return VHALAidlAgent().run(spec.to_llm_spec())
