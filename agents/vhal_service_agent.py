# agents/vhal_service_agent.py
from __future__ import annotations
import json
import re
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Union, List

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

    def _parse_properties(self, plan_text: str) -> List[Dict[str, Any]]:
        try:
            if plan_text.strip().startswith("spec_version"):
                spec = yaml.safe_load(plan_text)
            else:
                spec = json.loads(plan_text)
            return spec.get("properties", [])
        except Exception as e:
            print(f"[ERROR] Failed to parse VSS spec: {e}")
            return []

    def build_prompt(self, plan_text: str) -> str:
        props = self._parse_properties(plan_text)
        prop_summary = "\n".join(
            f"  - {p.get('name','<missing name>'): <60} "
            f"{p.get('type','?'): <10} {p.get('access','?'): <12} "
            f"{p.get('sdv', {}).get('updatable_behavior', 'false'): <8} "
            f"{p.get('meta', {}).get('description', 'no desc')[:100]}"
            for p in props
        )

        few_shot_example = r"""
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

{few_shot_example}

Output ONLY valid JSON with exactly two files:
{{
  "files": [
    {{"path": "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp", "content": "..."}},
    {{"path": "hardware/interfaces/automotive/vehicle/impl/VssPropertyIds.h", "content": "..."}}
  ]
}}

No explanations. No markdown. Pure JSON only.
""".strip()

    def run(self, plan_text: str) -> bool:
        print(f"[DEBUG] {self.name}: start")
        prompt = self.build_prompt(plan_text)

        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.25,
            top_p=0.95,
            response_format="json",
        )
        self._dump_raw(raw, "attempt1")

        if self._try_write_from_output(raw):
            print(f"[DEBUG] {self.name}: done (LLM success on first try)")
            return True

        print(f"[DEBUG] {self.name}: first attempt failed → repair")
        repair_prompt = (
            prompt
            + "\n\nPREVIOUS OUTPUT WAS INVALID OR INCOMPLETE.\n"
            "You MUST output valid JSON with BOTH files and correct method implementations.\n"
            "Include getAllPropConfigs with real configs. Fix signatures. No excuses."
        )

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

    # ────────────────────────────────────────────────
    # Minimal implementations for missing methods
    # (replace with your original if you have better ones)
    # ────────────────────────────────────────────────

    def _dump_raw(self, text: str, label: str) -> None:
        if not text:
            text = "[EMPTY RESPONSE]"
        path = self.raw_dir / f"VHAL_SERVICE_RAW_{label}.txt"
        path.write_text(text, encoding="utf-8")

    def _sanitize_path(self, rel_path: str) -> Optional[str]:
        if not rel_path:
            return None
        p = rel_path.replace("\\", "/").strip("/")
        p = re.sub(r"/+", "/", p)
        if ".." in p.split("/") or not p.startswith("hardware/interfaces/automotive/vehicle/impl/"):
            return None
        return p

    def _write_fallback(self) -> None:
        cpp = """// Fallback minimal C++ implementation
#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
// ... placeholder code ...
"""
        header = """#pragma once
constexpr int32_t VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED = 0xF0000000;
// more placeholders...
"""
        self.writer.write(self.impl_cpp, cpp)
        self.writer.write(self.ids_header, header)

    def _try_write_from_output(self, text: str) -> bool:
        if not text or not text.strip().startswith("{"):
            return False

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            return False

        if "files" not in data or not isinstance(data["files"], list):
            return False

        written = 0
        for item in data["files"]:
            path = item.get("path", "").strip()
            content = item.get("content")
            if not path or not isinstance(content, str):
                continue

            safe_path = self._sanitize_path(path)
            if safe_path not in self.required_files:
                continue

            self.writer.write(safe_path, content.rstrip() + "\n")
            written += 1

        return written == len(self.required_files)


# ────────────────────────────────────────────────
# Top-level entry point — this is what the pipeline expects
# ────────────────────────────────────────────────

def generate_vhal_service(plan_or_spec: Union[str, Dict[str, Any], Any]) -> bool:
    """
    Main entry point for the pipeline / architect_agent.py.
    Converts input to plan_text and runs the agent.
    """
    if isinstance(plan_or_spec, str):
        plan_text = plan_or_spec
    elif isinstance(plan_or_spec, dict):
        plan_text = json.dumps(plan_or_spec, separators=(",", ":"))
    else:
        try:
            plan_text = plan_or_spec.to_llm_spec()
        except Exception:
            plan_text = "{}"

    agent = VHALServiceAgent()
    return agent.run(plan_text)