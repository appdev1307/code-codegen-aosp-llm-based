import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are an Android Automotive HAL engineer.

Target:
- Vehicle HAL AIDL backend
- C++ implementation
- android.hardware.automotive.vehicle

Generate REAL, COMPILABLE C++ service code.

You MUST generate:
1. VehicleService.h
2. VehicleService.cpp
3. main.cpp
4. Android.bp
5. vintf_manifest_fragment.xml

Rules:
- Implement IVehicle.Stub
- Handle get/set for at least one property
- Use VehiclePropValue correctly
- No placeholders
- No explanations

Output format EXACTLY:

--- FILE: <relative path> ---
<content>

Specification:
{spec}
"""

    def run(self, spec: str):
        print(f"[DEBUG] {self.name}: start", flush=True)
        result = call_llm(self.build_prompt(spec))
        self._write_files(result)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.startswith("--- FILE:"):
                if current:
                    self._flush(current, buf)
                current = line.split("FILE:")[1].strip(" -")
                buf = []
            else:
                buf.append(line)

        if current:
            self._flush(current, buf)

    def _flush(self, rel, buf):
        path = os.path.join(self.output_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(buf))
