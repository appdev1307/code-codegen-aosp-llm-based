import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec: str) -> str:
        return f"""
You are NOT an AI assistant.
You are an Android Automotive OS AIDL Vehicle HAL C++ COMPILER.

Your task is to EMIT FINAL AOSP-COMPILABLE SOURCE FILES.
Any missing symbol, missing override, or signature mismatch
is a HARD BUILD FAILURE.

Creativity is FORBIDDEN.
Deviation is FORBIDDEN.

====================================
TARGET
====================================
- Android Automotive OS (Android 12+)
- AIDL Vehicle HAL
- Backend: C++ NDK Binder
- Interface: IVehicle (AIDL)
- Package:
  aidl::android::hardware::automotive::vehicle

====================================
FROZEN GOLDEN SERVICE CONTRACT
====================================

SERVICE CLASS (ABSOLUTE)
-----------------------
- Class name MUST be EXACTLY:
    VehicleHal
- MUST inherit EXACTLY from:
    aidl::android::hardware::automotive::vehicle::BnIVehicle

NAMESPACE (ABSOLUTE)
-------------------
ALL .h and .cpp files MUST include and use:

namespace aidl::android::hardware::automotive::vehicle

AND include:
using namespace aidl::android::hardware::automotive::vehicle;

====================================
MANDATORY METHOD SET (EXACT)
====================================

VehicleHal MUST implement ALL of the following
with EXACT names and EXACT signatures:

1. ndk::ScopedAStatus get(
       int32_t propId,
       int32_t areaId,
       VehiclePropValue* _aidl_return
   ) override;

2. ndk::ScopedAStatus set(
       const VehiclePropValue& value
   ) override;

3. ndk::ScopedAStatus subscribe(
       const std::shared_ptr<IVehicleCallback>& callback,
       const std::vector<SubscribeOptions>& options
   ) override;

4. ndk::ScopedAStatus unsubscribe(
       const std::shared_ptr<IVehicleCallback>& callback,
       int32_t propId
   ) override;

5. void onPropertyChanged(
       const VehiclePropValue& value
   );

⚠️ The function name MUST be EXACTLY:
    onPropertyChanged

⚠️ The method MUST be DEFINED in VehicleHal.cpp
⚠️ The literal string "onPropertyChanged(" MUST appear
    as a real method definition

====================================
CALLBACK CONTRACT (VALIDATOR-CRITICAL)
====================================

- IVehicleCallback MUST be included
- VehicleHal MUST store callbacks internally
- subscribe() MUST store callbacks
- unsubscribe() MUST remove callbacks
- set() MUST call:
    onPropertyChanged(value);

- onPropertyChanged() MUST:
    iterate callbacks
    call IVehicleCallback::onPropertyEvent()

====================================
THREAD SAFETY (MANDATORY)
====================================

- Include <mutex>
- Declare:
    std::mutex mMutex;
- Use std::lock_guard<std::mutex>
  in get(), set(), subscribe(), unsubscribe()

====================================
PROPERTY IMPLEMENTATION (MINIMUM)
====================================

- Implement REAL state storage
- Handle at least ONE property (e.g. VEHICLE_SPEED)
- Store value internally
- Populate VehiclePropValue correctly

====================================
SERVICE REGISTRATION (ABSOLUTE)
====================================

main.cpp MUST:
- Create VehicleHal instance
- Register service with Binder
- Use EXACT service name:

  "android.hardware.automotive.vehicle.IVehicle/default"

====================================
FILES TO EMIT (ALL REQUIRED)
====================================

1. VehicleHal.h
2. VehicleHal.cpp
3. main.cpp
4. Android.bp
5. vintf_manifest_fragment.xml

====================================
FORBIDDEN (ABSOLUTE)
====================================

- NO placeholders
- NO TODO
- NO pseudo code
- NO explanations
- NO markdown
- NO comments describing intent
- NO missing overrides

====================================
OUTPUT FORMAT (STRICT)
====================================

--- FILE: <relative path> ---
<file content>

====================================
SPECIFICATION (INPUT ONLY)
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
