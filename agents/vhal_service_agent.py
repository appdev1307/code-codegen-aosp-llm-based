# FILE: agents/vhal_service_agent.py
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, Union
from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_SERVICE_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Generate a correct, generic, and highly scalable C++ service implementation using AIDL NDK backend.\n"
            "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
            "If you cannot produce perfect JSON, output exactly: {\"files\": []}"
        )

        self.impl_cpp = "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"
        self.required_files = [self.impl_cpp]

    def build_prompt(self, plan_text: str) -> str:
        return f"""
Generate the core AOSP Vehicle HAL service implementation in C++ (AIDL NDK backend).

MANDATORY OUTPUT FORMAT:
Return ONLY valid JSON:
{{
  "files": [
    {{
      "path": "{self.impl_cpp}",
      "content": "full C++ source with \\n for newlines and proper escaping"
    }}
  ]
}}

CRITICAL RULES:
- Output ONLY the JSON object. No extra text, no fences, no comments.
- Path must be exactly: {self.impl_cpp}
- Use proper string escaping (\\n for newlines, \\\" for quotes)

IMPLEMENTATION REQUIREMENTS (AAOS 14+):
- Include AIDL NDK headers:
  #include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
  #include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
  #include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>
- Inherit from BnIVehicle and override:
  get(), set(), registerCallback(), unregisterCallback()
- Use thread-safe generic storage: std::unordered_map<PropKey, VehiclePropValue, PropKeyHash>
- On set(): update store and notify ALL callbacks via onPropertyEvent()
- Use ndk::ScopedAStatus for status returns
- In main(): register service as "android.hardware.automotive.vehicle.IVehicle/default"
- Call ABinderProcess_startThreadPool() and join

SCALING (MANDATORY):
- MUST be fully generic — no switch/case on property IDs
- Treat all properties as runtime (propId, areaId) keys
- Supports 200+ VSS properties without code bloat

PLAN CONTEXT (use only for reference if needed):
{plan_text}

OUTPUT ONLY THE JSON NOW:
""".strip()

    def run(self, plan_text: str) -> bool:
        print(f"[DEBUG] {self.name}: start")

        prompt = self.build_prompt(plan_text)

        # Attempt 1
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

        # Attempt 2: Repair
        print(f"[DEBUG] {self.name}: first attempt failed → repair attempt")
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID OR MISSING CONTENT.\n" \
                                "You MUST output complete, valid JSON with the full VehicleHalService.cpp implementation.\n" \
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

        # Fallback
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

        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "").strip()
            content = item.get("content")

            if path != self.impl_cpp or not isinstance(content, str):
                continue

            content = content.rstrip() + "\n"  # Normalize
            self.writer.write(path, content)
            report["paths"] = [path]
            report["missing_required"] = []
            self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return True

        self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return False

    def _dump_raw(self, text: str, label: str) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_{label}.txt").write_text(text or "[EMPTY]", encoding="utf-8")

    def _write_fallback(self) -> None:
        """Production-ready, generic, thread-safe fallback implementation"""
        cpp_content = """// hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp
#include <android-base/logging.h>
#include <android/binder_interface_utils.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>

#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>

#include <mutex>
#include <unordered_map>
#include <vector>

using aidl::android::hardware::automotive::vehicle::BnIVehicle;
using aidl::android::hardware::automotive::vehicle::IVehicleCallback;
using aidl::android::hardware::automotive::vehicle::VehiclePropValue;

namespace {

struct PropKey {
    int32_t propId;
    int32_t areaId;
    bool operator==(const PropKey& o) const { return propId == o.propId && areaId == o.areaId; }
};

struct PropKeyHash {
    std::size_t operator()(const PropKey& k) const {
        return (static_cast<uint64_t>(k.propId) << 32) | (static_cast<uint64_t>(k.areaId) & 0xFFFFFFFFULL);
    }
};

class VehicleHalServiceImpl : public BnIVehicle {
public:
    ndk::ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return) override {
        if (!_aidl_return) return ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);

        std::lock_guard<std::mutex> lock(mMutex);
        PropKey key{propId, areaId};
        auto it = mProperties.find(key);
        if (it != mProperties.end()) {
            *_aidl_return = it->second;
        } else {
            _aidl_return->prop = propId;
            _aidl_return->areaId = areaId;
            _aidl_return->timestamp = 0;
        }
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus set(const VehiclePropValue& value) override {
        std::vector<std::shared_ptr<IVehicleCallback>> callbacksCopy;
        {
            std::lock_guard<std::mutex> lock(mMutex);
            PropKey key{value.prop, value.areaId};
            mProperties[key] = value;
            callbacksCopy = mCallbacks;
        }
        for (const auto& cb : callbacksCopy) {
            if (cb) (void)cb->onPropertyEvent(value);
        }
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus registerCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        if (callback) {
            std::lock_guard<std::mutex> lock(mMutex);
            mCallbacks.push_back(callback);
        }
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        if (callback) {
            std::lock_guard<std::mutex> lock(mMutex);
            mCallbacks.erase(std::remove(mCallbacks.begin(), mCallbacks.end(), callback), mCallbacks.end());
        }
        return ndk::ScopedAStatus::ok();
    }

private:
    std::mutex mMutex;
    std::unordered_map<PropKey, VehiclePropValue, PropKeyHash> mProperties;
    std::vector<std::shared_ptr<IVehicleCallback>> mCallbacks;
};

}  // namespace

int main(int argc, char** argv) {
    android::base::InitLogging(argv);
    ABinderProcess_setThreadPoolMaxThreadCount(4);
    ABinderProcess_startThreadPool();

    auto service = ndk::SharedRefBase::make<VehicleHalServiceImpl>();
    const std::string instance = "android.hardware.automotive.vehicle.IVehicle/default";
    binder_status_t status = AServiceManager_addService(service->asBinder().get(), instance.c_str());

    if (status != STATUS_OK) {
        LOG(ERROR) << "Failed to register Vehicle HAL service: " << status;
        return EXIT_FAILURE;
    }

    LOG(INFO) << "Vehicle HAL service registered: " << instance;
    ABinderProcess_joinThreadPool();
    return EXIT_SUCCESS;
}
"""

        self.writer.write(self.impl_cpp, cpp_content.lstrip() + "\n")


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