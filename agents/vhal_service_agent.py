import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are a senior Android Automotive OS HAL engineer.

Target:
- Android Automotive Vehicle HAL
- AIDL backend (Android 12+)
- C++ implementation
- Namespace: android.hardware.automotive.vehicle

You are implementing the **AIDL Vehicle HAL backend**.

======================
MANDATORY REQUIREMENTS
======================

You MUST generate REAL, COMPILABLE C++ code that satisfies ALL rules below.

1. The service class MUST be named:
   - VehicleHal
   - It MUST inherit from:
     BnIVehicle

2. The service MUST implement at least these methods:
   - ndk::ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* outValue)
   - ndk::ScopedAStatus set(const VehiclePropValue& value)

3. You MUST:
   - Include <mutex>
   - Declare std::mutex mLock;
   - Use std::lock_guard<std::mutex> inside get() and set()

4. VehiclePropValue MUST be:
   - Used as defined by AIDL
   - Read/write at least ONE property (e.g. VEHICLE_SPEED)

5. Namespace usage MUST include:
   using aidl::android::hardware::automotive::vehicle::VehiclePropValue;
   using aidl::android::hardware::automotive::vehicle::BnIVehicle;

6. Files to generate:
   - VehicleService.h
   - VehicleService.cpp
   - main.cpp
   - Android.bp
   - vintf_manifest_fragment.xml

7. NO placeholders
8. NO explanations
9. Code MUST look like real AOSP HAL code

======================
OUTPUT FORMAT (STRICT)
======================

--- FILE: <relative path> ---
<file content>

======================
SPECIFICATION
======================
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
                current = line.replace("--- FILE:", "").replace("---", "").strip()
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


def generate_vhal_service(spec: str):
    agent = VHALServiceAgent()
    return agent.run(spec)
