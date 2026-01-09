import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are a PRINCIPAL Android Automotive OS Vehicle HAL engineer.

You are generating FINAL, PRODUCTION-READY AOSP code.
This code will be compiled directly by AOSP.
Any deviation from rules below is a BUILD FAILURE.

Target:
- Android Automotive OS (Android 12+)
- AIDL-based Vehicle HAL backend
- C++ (NDK Binder)
- Package: android.hardware.automotive.vehicle

====================================
MANDATORY – ABSOLUTE REQUIREMENTS
====================================

1. SERVICE CLASS (CRITICAL)
--------------------------
- Service class MUST be named EXACTLY:
    VehicleHal
- Service class MUST inherit EXACTLY from:
    aidl::android::hardware::automotive::vehicle::BnIVehicle

2. INTERFACE REFERENCE (CRITICAL)
--------------------------------
- IVehicle MUST be explicitly referenced in code
- BnIVehicle MUST be included and used

3. NAMESPACE (CRITICAL)
----------------------
ALL C++ files MUST reference this namespace:

    aidl::android::hardware::automotive::vehicle

You MUST include:
    using namespace aidl::android::hardware::automotive::vehicle;

4. REQUIRED AIDL METHODS (EXACT SIGNATURES)
------------------------------------------
You MUST implement ALL IVehicle methods EXACTLY:

- ndk::ScopedAStatus get(
      int32_t propId,
      int32_t areaId,
      VehiclePropValue* _aidl_return
  ) override;

- ndk::ScopedAStatus set(
      const VehiclePropValue& value
  ) override;

5. CALLBACK SUPPORT (CRITICAL)
-----------------------------
- IVehicleCallback MUST be included
- Service MUST store registered callbacks
- Service MUST notify callbacks when property changes
- The literal string "onPropertyChanged(" MUST appear in VehicleHal implementation

6. PROPERTY LOGIC (REAL IMPLEMENTATION)
--------------------------------------
- Implement REAL get/set logic
- Handle at least ONE real property (e.g. VEHICLE_SPEED)
- Store property state internally
- Populate VehiclePropValue correctly

7. THREAD SAFETY (MANDATORY)
---------------------------
- Include <mutex>
- Declare:
    std::mutex mMutex;
- Use:
    std::lock_guard<std::mutex>
  inside BOTH get() and set()

8. SERVICE REGISTRATION (CRITICAL)
---------------------------------
main.cpp MUST register service under EXACT name:

    "android.hardware.automotive.vehicle.IVehicle/default"

9. FILES TO GENERATE (ALL REQUIRED)
----------------------------------
You MUST generate ALL files:

1. VehicleHal.h
2. VehicleHal.cpp
3. main.cpp
4. Android.bp
5. vintf_manifest_fragment.xml

10. FORBIDDEN (ABSOLUTE)
-----------------------
- NO placeholders
- NO TODO
- NO pseudo code
- NO explanations
- NO markdown
- NO comments explaining intent

====================================
OUTPUT FORMAT (STRICT)
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
