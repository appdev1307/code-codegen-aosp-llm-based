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
        self.name = "VHAL C++ Service Agent (VSS-aware)"
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_SERVICE_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Generate a correct, realistic, and production-grade C++ service using AIDL NDK.\n"
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
        prop_lines = []
        for p in props:
            name = p.get('name', 'UNKNOWN')
            typ = p.get('type', 'UNKNOWN')
            access = p.get('access', 'READ_WRITE')
            desc = p.get('meta', {}).get('description', '').replace('\n', ' ').strip()[:140]
            prop_lines.append(f"- {name:<68} {typ:<10} {access:<12} {desc}")

        return f"""
Generate realistic Vehicle HAL service implementation (AIDL NDK backend) based on VSS spec.

MANDATORY OUTPUT FORMAT:
Return ONLY valid JSON:
{{
  "files": [
    {{"path": "{self.impl_cpp}", "content": "full C++ code with \\n for newlines"}},
    {{"path": "{self.ids_header}", "content": "header with constexpr IDs and comments"}}
  ]
}}

CRITICAL RULES:
- Output ONLY the JSON object. No extra text, no fences, no comments.
- Use proper escaping (\\n for newlines, \\\" for quotes)

REQUIRED FILES:
- {self.impl_cpp}          → main implementation
- {self.ids_header}        → constexpr int32_t property IDs + comments

IMPLEMENTATION REQUIREMENTS:
- Include correct AIDL NDK headers
- Inherit from BnIVehicle
- Implement: get, set, getAllPropConfigs, getPropConfigs, registerCallback, unregisterCallback
- Use thread-safe storage (mutex + map or vector)
- Implement getAllPropConfigs() with realistic VehiclePropConfig entries derived from VSS
- Add default values in constructor (e.g. *_ISENABLED = false, distances = 0.0f)
- In set(): reject READ-only properties, validate types/sizes/ranges where reasonable
- Use IDs from VssPropertyIds.h
- Support realistic ADAS behavior (changeMode ON_CHANGE or STATIC, sample rates 0-10 Hz, areas GLOBAL)

VSS PROPERTIES (use for configs, defaults, validation):
{'\n'.join(prop_lines)}

FULL VSS SPEC (parse types, access, descriptions, sdv.updatable_behavior, safety_level):
{plan_text}

OUTPUT ONLY THE JSON NOW:
""".strip()

    def run(self, plan_text: str) -> bool:
        print(f"[DEBUG] {self.name}: start")

        prompt = self.build_prompt(plan_text)

        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",
        )
        self._dump_raw(raw, "attempt1")
        if self._try_write_from_output(raw):
            print(f"[DEBUG] {self.name}: done (LLM success on first try)")
            return True

        print(f"[DEBUG] {self.name}: first attempt failed → repair attempt")
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID OR MISSING CONTENT.\n" \
                                "You MUST output complete, valid JSON with BOTH required files.\n" \
                                "Fix all issues immediately. No explanations."

        raw2 = call_llm(
            prompt=repair_prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",
        )
        self._dump_raw(raw2, "attempt2")
        if self._try_write_from_output(raw2):
            print(f"[DEBUG] {self.name}: done (LLM success after repair)")
            return True

        print(f"[WARN] {self.name}: LLM failed → using deterministic fallback")
        self._write_fallback()
        return False

    def _try_write_from_output(self, text: str) -> bool:
        if not text.strip():
            return False

        data, err = parse_json_object(text.strip())
        report = {
            "parse_error": err,
            "valid_json": data is not None,
            "files_found": 0,
            "paths": [],
            "missing_required": self.required_files[:],
        }

        if not data or "files" not in data or not isinstance(data["files"], list):
            self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return False

        files = data["files"]
        report["files_found"] = len(files)
        written_paths = []

        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "").strip()
            content = item.get("content")

            if not path or not isinstance(content, str):
                continue

            safe_path = self._sanitize_path(path)
            if safe_path not in self.required_files:
                continue

            content = content.rstrip() + "\n"
            self.writer.write(safe_path, content)
            written_paths.append(safe_path)

        missing = [p for p in self.required_files if p not in written_paths]
        report["paths"] = written_paths
        report["missing_required"] = missing

        self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return len(missing) == 0

    def _dump_raw(self, text: str, label: str) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_{label}.txt").write_text(text or "[EMPTY]", encoding="utf-8")

    def _sanitize_path(self, rel_path: str) -> Optional[str]:
        if not rel_path:
            return None
        p = rel_path.replace("\\", "/").strip("/")
        p = re.sub(r"/+", "/", p)
        if ".." in p.split("/") or not p.startswith("hardware/interfaces/automotive/vehicle/impl/"):
            return None
        return p

    def _write_fallback(self) -> None:
        cpp_content = """// hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp
#include <android-base/logging.h>
#include <android/binder_interface_utils.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>

#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropConfig.h>

#include <mutex>
#include <unordered_map>
#include <vector>

using namespace aidl::android::hardware::automotive::vehicle;
using ndk::ScopedAStatus;
using ndk::SharedRefBase;

struct PropKey {
    int32_t prop;
    int32_t areaId;
    bool operator==(const PropKey& o) const { return prop == o.prop && areaId == o.areaId; }
};

struct PropKeyHash {
    std::size_t operator()(const PropKey& k) const {
        return (static_cast<uint64_t>(k.prop) << 32) | static_cast<uint32_t>(k.areaId);
    }
};

class VehicleHalServiceImpl : public BnIVehicle {
public:
    VehicleHalServiceImpl() {
        // Default values - expand from VSS in real generation
        VehiclePropValue val;
        val.prop = 0xF0000000;  // example ABS_ISENABLED
        val.areaId = 0;  // GLOBAL
        val.boolValues = {false};
        mStore[{val.prop, val.areaId}] = val;
    }

    ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return) override {
        std::lock_guard<std::mutex> lock(mMutex);
        auto it = mStore.find({propId, areaId});
        if (it != mStore.end()) {
            *_aidl_return = it->second;
        } else {
            _aidl_return->prop = propId;
            _aidl_return->areaId = areaId;
            _aidl_return->timestamp = 0;
        }
        return ScopedAStatus::ok();
    }

    ScopedAStatus set(const VehiclePropValue& value) override {
        std::vector<std::shared_ptr<IVehicleCallback>> cbs;
        {
            std::lock_guard<std::mutex> lock(mMutex);
            mStore[{value.prop, value.areaId}] = value;
            cbs = mCallbacks;
        }
        for (auto& cb : cbs) {
            if (cb) cb->onPropertyEvent({value});
        }
        return ScopedAStatus::ok();
    }

    ScopedAStatus getAllPropConfigs(std::vector<VehiclePropConfig>* _aidl_return) override {
        // Placeholder - real LLM will generate from VSS
        _aidl_return->clear();
        return ScopedAStatus::ok();
    }

    ScopedAStatus registerCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        std::lock_guard<std::mutex> lock(mMutex);
        mCallbacks.push_back(callback);
        return ScopedAStatus::ok();
    }

    ScopedAStatus unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        std::lock_guard<std::mutex> lock(mMutex);
        mCallbacks.erase(std::remove(mCallbacks.begin(), mCallbacks.end(), callback), mCallbacks.end());
        return ScopedAStatus::ok();
    }

private:
    std::mutex mMutex;
    std::unordered_map<PropKey, VehiclePropValue, PropKeyHash> mStore;
    std::vector<std::shared_ptr<IVehicleCallback>> mCallbacks;
};

int main() {
    ABinderProcess_startThreadPool();
    auto service = SharedRefBase::make<VehicleHalServiceImpl>();
    AServiceManager_addService(service->asBinder().get(), "android.hardware.automotive.vehicle.IVehicle/default");
    ABinderProcess_joinThreadPool();
    return 0;
}
"""

        header_content = """// hardware/interfaces/automotive/vehicle/impl/VssPropertyIds.h
#pragma once

namespace android::hardware::automotive::vehicle::vss {
constexpr int32_t VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED = 0xF0000000;
// more IDs will be generated by LLM
}
"""

        for path, content in [
            (self.impl_cpp, cpp_content),
            (self.ids_header, header_content),
        ]:
            self.writer.write(path, content.rstrip() + "\n")


def generate_vhal_service(plan_or_spec: Union[str, Dict[str, Any], Any]) -> bool:
    if isinstance(plan_or_spec, str):
        plan_text = plan_or_spec
    elif isinstance(plan_or_spec, dict):
        plan_text = json.dumps(plan_or_spec, separators=(",", ":"))
    else:
        try:
            plan_text = plan_or_spec.to_llm_spec()
        except:
            plan_text = "{}"

    return VHALServiceAgent().run(plan_text)