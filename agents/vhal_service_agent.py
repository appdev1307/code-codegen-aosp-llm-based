import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are a PRINCIPAL Android Automotive OS Vehicle HAL engineer.

Target:
- Android Automotive OS (Android 12+)
- AIDL-based Vehicle HAL backend
- C++ (NDK Binder)
- Package: android.hardware.automotive.vehicle

====================================
MANDATORY – DO NOT VIOLATE ANY RULE
====================================

1. SERVICE CLASS
----------------
- Service class MUST be named:
    VehicleService
- Service class MUST inherit EXACTLY from:
    aidl::android::hardware::automotive::vehicle::BnIVehicle

2. NAMESPACE (MANDATORY)
-----------------------
The following namespace MUST appear in code:
    aidl::android::hardware::automotive::vehicle

You MUST use:
    using namespace aidl::android::hardware::automotive::vehicle;

3. REQUIRED AIDL METHODS
------------------------
You MUST implement ALL AIDL methods from IVehicle:

- ndk::ScopedAStatus get(
      int32_t propId,
      int32_t areaId,
      VehiclePropValue* _aidl_return
  ) override;

- ndk::ScopedAStatus set(
      const VehiclePropValue& value
  ) override;

4. CALLBACK SUPPORT (CRITICAL)
------------------------------
- IVehicleCallback MUST be referenced
- Service MUST:
  - Store callbacks
  - Call callback->onChange(...)
- The literal string "onChange(" MUST exist in service code

5. PROPERTY LOGIC
-----------------
- Implement REAL get/set logic
- Handle at least ONE property (e.g. VEHICLE_SPEED)
- Store property internally
- Use VehiclePropValue correctly

6. THREAD SAFETY (MANDATORY)
----------------------------
- Include <mutex>
- Declare:
    std::mutex mMutex;
- Protect get() and set() using:
    std::lock_guard<std::mutex>

7. FILES TO GENERATE (ALL REQUIRED)
----------------------------------
You MUST generate ALL files below:

1. VehicleService.h
2. VehicleService.cpp
3. main.cpp
4. Android.bp
5. vintf_manifest_fragment.xml

8. FORBIDDEN
------------
- NO placeholders
- NO TODO
- NO pseudo code
- NO explanations
- NO markdown

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
        result = call_llm(self.build_prompt(spec))
        self._write_files(result)
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
