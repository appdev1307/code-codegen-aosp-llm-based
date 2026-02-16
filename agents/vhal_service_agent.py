# agents/vhal_service_agent.py
from __future__ import annotations
import json
import re
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Union

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent (VSS-aware + strong prompt)"
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_SERVICE_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Generate a correct, realistic, production-grade C++ service using AIDL NDK backend.\n"
            "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
            "If you cannot produce perfect JSON, output exactly: {\"files\": []}"
        )

        self.base = "hardware/interfaces/automotive/vehicle/impl"
        self.impl_cpp = f"{self.base}/VehicleHalService.cpp"
        self.ids_header = f"{self.base}/VssPropertyIds.h"

        self.required_files = [self.impl_cpp, self.ids_header]

    def _parse_properties(self, plan_text: str):
        try:
            if plan_text.strip().startswith("spec_version"):
                spec = yaml.safe_load(plan_text)
            else:
                spec = json.loads(plan_text)
            return spec.get("properties", [])
        except Exception:
            return []

    def build_prompt(self, plan_text: str) -> str:
        props = self._parse_properties(plan_text)
        prop_summary = "\n".join(
            f"  - {p.get('name'): <60} {p.get('type'): <10} {p.get('access'): <12} "
            f"{p.get('sdv', {}).get('updatable_behavior', 'false'): <8} "
            f"{p.get('meta', {}).get('description', '')[:100]}"
            for p in props
        )

        few_shot_example = """
Example of high-quality getAllPropConfigs implementation:

ScopedAStatus getAllPropConfigs(std::vector<VehiclePropConfig>* _aidl_return) override {
    if (!_aidl_return) return ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
    _aidl_return->clear();

    VehiclePropConfig cfg;
    cfg.prop = VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED;
    cfg.access = VehiclePropertyAccess::READ_WRITE;
    cfg.changeMode = VehiclePropertyChangeMode::ON_CHANGE;
    cfg.minSampleRate = 0.0f;
    cfg.maxSampleRate = 2.0f;
    cfg.areaConfigs.emplace_back();
    cfg.areaConfigs.back().areaId = 0;  // GLOBAL
    _aidl_return->push_back(std::move(cfg));

    // Repeat pattern for other properties...
    return ndk::ScopedAStatus::ok();
}
"""

        return f"""\
You are an expert AAOS Vehicle HAL developer using AIDL NDK backend (Android 14/15 style).

Generate high-quality, realistic implementation of VehicleHalService.cpp + VssPropertyIds.h

MANDATORY REQUIREMENTS — you MUST implement ALL of these:

1. Correct includes:
   #include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
   #include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
   #include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>
   #include <aidl/android/hardware/automotive/vehicle/VehiclePropConfig.h>
   #include <aidl/android/hardware/automotive/vehicle/VehicleArea.h>
   #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyAccess.h>
   #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyChangeMode.h>
   #include "VssPropertyIds.h"

2. Class inherits BnIVehicle

3. Implement ALL these methods with correct signatures:
   - get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return)
   - set(const VehiclePropValue& value)
   - getAllPropConfigs(std::vector<VehiclePropConfig>* _aidl_return)
   - getPropConfigs(const std::vector<int32_t>& props, std::vector<VehiclePropConfig>* _aidl_return)
   - registerCallback(const std::shared_ptr<IVehicleCallback>& callback)
   - unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback)

4. Use thread-safe unordered_map<PropKey, VehiclePropValue> or vector

5. In constructor: set realistic default values for all WRITE/READ_WRITE properties (false for enabled flags, 0 for speeds/distances, etc.)

6. In set(): 
   - reject READ-only props with EX_UNSUPPORTED_OPERATION
   - basic type validation (boolValues.size()==1 for bool, floatValues.size()==1 for float, etc.)
   - notify all callbacks

7. In getAllPropConfigs(): return full list of VehiclePropConfig for ALL properties in spec
   - access from VSS 'access'
   - changeMode: ON_CHANGE for most sensors/actuators
   - sample rates: 0–10 Hz typical
   - area: GLOBAL (0)

8. Generate VssPropertyIds.h with constexpr int32_t for every property (sequential vendor IDs 0xF0000000+)

VSS PROPERTIES YOU MUST SUPPORT:
{prop_summary}

FULL VSS YAML / SPEC:
{plan_text}

{ few_shot_example }

Output ONLY valid JSON with exactly two files:
{{
  "files": [
    {{"path": "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp", "content": "..."}},
    {{"path": "hardware/interfaces/automotive/vehicle/impl/VssPropertyIds.h", "content": "..."}}
  ]
}}

No explanations. No markdown. Pure JSON only.
""".strip()

    # ────────────────────────────────────────────────
    # The rest of the class remains unchanged
    # (run(), _try_write_from_output(), _write_fallback(), etc.)
    # Copy your original implementation for these methods
    # ────────────────────────────────────────────────

    def run(self, plan_text: str) -> bool:
        print(f"[DEBUG] {self.name}: start")

        prompt = self.build_prompt(plan_text)

        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.25,           # ← increased for better quality
            top_p=0.95,
            response_format="json",
        )
        self._dump_raw(raw, "attempt1")
        if self._try_write_from_output(raw):
            print(f"[DEBUG] {self.name}: done (LLM success on first try)")
            return True

        # Repair attempt
        print(f"[DEBUG] {self.name}: first attempt failed → repair")
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID OR INCOMPLETE.\n" \
                                "You MUST output valid JSON with BOTH files and correct method implementations.\n" \
                                "Include getAllPropConfigs with real configs. Fix signatures. No excuses."

        raw2 = call_llm(
            prompt=repair_prompt,
            system=self.system_prompt,
            temperature=0.3,
            top_p=0.95,
            response_format="json",
        )
        self._dump_raw(raw2, "attempt2")
        if self._try_write_from_output(raw2):
            print(f"[DEBUG] {self.name}: success after repair")
            return True

        print(f"[WARN] {self.name}: LLM failed → fallback")
        self._write_fallback()
        return False

    # ... your original _try_write_from_output, _dump_raw, _sanitize_path, _write_fallback methods ...