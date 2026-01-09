import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are a PRINCIPAL Android Automotive OS Vehicle HAL engineer.

You are generating FINAL, PRODUCTION-READY code.
This code will be compiled directly by AOSP.
If ANY rule below is violated, the build will FAIL.

Target:
- Android Automotive OS (Android 12+)
- AIDL-based Vehicle HAL backend
- C++ (NDK Binder)
- Package: android.hardware.automotive.vehicle

====================================
MANDATORY – DO NOT VIOLATE ANY RULE
====================================

1. SERVICE CLASS (CRITICAL)
--------------------------
- Service class MUST be named EXACTLY:
    VehicleService
- Service class MUST inherit EXACTLY from:
    aidl::android::hardware::automotive::vehicle::BnIVehicle

2. NAMESPACE (CRITICAL)
----------------------
The following namespace MUST appear in ALL C++ files:

    namespace aidl::android::hardware::automotive::vehicle

You MUST also include:
    using namespace aidl::android::hardware::automotive::vehicle;

3. REQUIRED AIDL METHODS (NO EXCEPTION)
--------------------------------------
You MUST implement ALL IVehicle methods EXACTLY:

- ndk::ScopedAStatus get(
      int32_t propId,
      int32_t areaId,
      VehiclePropValue* _aidl_return
  ) override;

- ndk::ScopedAStatus set(
      const VehiclePropValue& value
  ) override;

4. CALLBACK SUPPORT (MANDATORY)
------------------------------
- IVehicleCallback MUST be included
- Service MUST:
  - Store callbacks internally
  - Notify callbacks on property change
- The literal string "onChange(" MUST appear in VehicleService.cpp

5. PROPERTY LOGIC (REAL IMPLEMENTATION)
--------------------------------------
- Implement REAL get/set logic
- Handle at least ONE real property
  (example: VEHICLE_SPEED)
- Store property state internally
- Correctly populate VehiclePropValue

6. THREAD SAFETY (MANDATORY)
---------------------------
- Include <mutex>
- Declare:
    std::mutex mMutex;
- Use:
    std::lock_guard<std::mutex>
  inside BOTH get() and set()

7. SERVICE REGISTRATION (CRITICAL)
---------------------------------
main.cpp MUST register the service under EXACT name:

    "android.hardware.automotive.vehicle.IVehicle/default"

8. FILES TO GENERATE (ALL REQUIRED)
----------------------------------
You MUST generate ALL of the following files:

1. VehicleService.h
2. VehicleService.cpp
3. main.cpp
4. Android.bp
5. vintf_manifest_fragment.xml

9. FORBIDDEN (ABSOLUTE)
----------------------
- NO placeholders
- NO TODO
- NO pseudo code
- NO explanations
- NO markdown
- NO comments describing what should be done

====================================
OUTPUT FORMAT (STRICT – NO DEVIATION)
====================================

--- FILE: <relative path> ---
<file content>

====================================
SPECIFICATION
====================================
{spec}
"""

    def run(self, spec: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        os.makedirs(self.output_dir, exist_ok=True)

        result = call_llm(self.build_prompt(spec))
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
