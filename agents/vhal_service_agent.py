# FILE: agents/vhal_service_agent.py

import json
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"

        # Stage-1 goes to draft
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.report_path = Path(self.output_root) / "VHAL_SERVICE_VALIDATE_REPORT.json"

        self.system = (
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"files\": []}\n"
        )

        self.impl_cpp = "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"
        self.required_files = [self.impl_cpp]

    def build_prompt(self, spec_text: str) -> str:
        # IMPORTANT: ask for a PLAN-DRIVEN TEMPLATE, not a 200-property giant file
        return f"""
OUTPUT CONTRACT (MANDATORY):
Return ONLY valid JSON matching this schema:

{{
  "files": [
    {{"path": "hardware/...", "content": "..."}}
  ]
}}

HARD RULES:
- Output ONLY JSON. No other text.
- NO markdown, NO code fences, NO headings.
- DO NOT generate app-layer code (no AndroidManifest, no Java Service, no com.example).
- TARGET IS AOSP AAOS VHAL SERVICE (AIDL NDK Binder).
- Paths MUST start with: hardware/interfaces/automotive/vehicle/impl/

YOU MUST GENERATE EXACTLY THIS FILE:
- {self.impl_cpp}

C++ REQUIREMENTS:
- Android Automotive OS 14
- Implement AIDL NDK Binder service for:
  android.hardware.automotive.vehicle.IVehicle/default
- Use generated AIDL NDK headers:
  <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
  <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
  <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>

- Implement:
  get(propId, areaId, _aidl_return)
  set(value)
  registerCallback(callback)
  unregisterCallback(callback)

- Thread-safe store keyed by (propId, areaId)
- Notify callbacks on set()
- Must compile (correct includes; correct overrides; no TODOs)

IMPORTANT SCALING RULE:
- Do NOT generate per-property switch/case for hundreds of VSS ids.
- Implement a generic store and treat propId/areaId as runtime keys.
- The service should work for any property id.

SPEC CONTEXT (do not repeat):
{spec_text}

RETURN JSON NOW.
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt(spec_text)

        # Attempt 1
        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, 1)
        ok1, report1 = self._validate_llm(out1)
        self.report_path.write_text(json.dumps(report1, indent=2), encoding="utf-8")

        if ok1 and self._write_json_files(out1):
            print(f"[DEBUG] {self.name}: LLM wrote service files (draft)", flush=True)
            return out1

        # Attempt 2 (targeted repair)
        missing = report1.get("missing_required_paths", [])
        repair = (
            prompt
            + "\nREPAIR (MANDATORY): Return ONLY JSON matching schema.\n"
              "You MUST include required path exactly.\n"
              f"MISSING REQUIRED PATHS: {missing}\n"
              "Do NOT wrap in code fences. Do NOT add commentary.\n"
        )

        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        ok2, report2 = self._validate_llm(out2)
        (Path(self.output_root) / "VHAL_SERVICE_VALIDATE_REPORT_attempt2.json").write_text(
            json.dumps(report2, indent=2), encoding="utf-8"
        )

        if ok2 and self._write_json_files(out2):
            print(f"[DEBUG] {self.name}: LLM wrote service files (draft, after repair)", flush=True)
            return out2

        print(f"[WARN] {self.name}: LLM did not produce valid JSON. Using deterministic fallback (draft).", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic VHAL service generated (draft)."

    def _validate_llm(self, text: str) -> Tuple[bool, Dict[str, Any]]:
        data, err = parse_json_object(text or "")
        report: Dict[str, Any] = {
            "parse_error": err,
            "has_files": False,
            "file_count": 0,
            "paths": [],
            "missing_required_paths": [],
        }
        if err or not data:
            return False, report

        files = data.get("files")
        if not isinstance(files, list):
            report["parse_error"] = report["parse_error"] or "Top-level key 'files' must be a list."
            return False, report

        report["has_files"] = True
        report["file_count"] = len(files)

        paths = []
        for f in files:
            if isinstance(f, dict) and isinstance(f.get("path"), str):
                paths.append(f["path"].strip())
        report["paths"] = paths

        missing = [p for p in self.required_files if p not in set(paths)]
        report["missing_required_paths"] = missing
        return (len(missing) == 0), report

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _write_json_files(self, text: str) -> bool:
        data, err = parse_json_object(text or "")
        if err or not data:
            return False

        files = data.get("files")
        if not isinstance(files, list) or not files:
            return False

        paths = {(f.get("path") or "").strip() for f in files if isinstance(f, dict)}
        if any(req not in paths for req in self.required_files):
            return False

        wrote = 0
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = f.get("content")
            if not path or not isinstance(content, str):
                continue
            safe = self._sanitize(path)
            if not safe:
                continue
            if not content.endswith("\n"):
                content += "\n"
            self.writer.write(safe, content)
            wrote += 1

        return wrote >= len(self.required_files)

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip()
        p = re.sub(r"/+", "/", p)
        if p.startswith("/") or ".." in p.split("/"):
            return None
        if not p.startswith("hardware/interfaces/automotive/vehicle/impl/"):
            return None
        return p

    def _write_fallback(self) -> None:
        cpp = """// FILE: hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp

#include <android-base/logging.h>
#include <android/binder_interface_utils.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>

#include <algorithm>
#include <cstdint>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>

using ::aidl::android::hardware::automotive::vehicle::BnIVehicle;
using ::aidl::android::hardware::automotive::vehicle::IVehicleCallback;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropValue;

namespace {

struct Key {
    int32_t propId;
    int32_t areaId;

    bool operator==(const Key& other) const { return propId == other.propId && areaId == other.areaId; }
};

struct KeyHash {
    size_t operator()(const Key& k) const {
        return (static_cast<size_t>(static_cast<uint32_t>(k.propId)) << 32) ^
               static_cast<size_t>(static_cast<uint32_t>(k.areaId));
    }
};

class VehicleHalServiceImpl final : public BnIVehicle {
public:
    ::ndk::ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return) override {
        if (_aidl_return == nullptr) {
            return ::ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
        }

        std::lock_guard<std::mutex> lk(mMutex);
        Key k{propId, areaId};
        auto it = mStore.find(k);
        if (it != mStore.end()) {
            *_aidl_return = it->second;
        } else {
            VehiclePropValue empty{};
            empty.prop = propId;
            empty.areaId = areaId;
            empty.timestamp = 0;
            *_aidl_return = empty;
        }
        return ::ndk::ScopedAStatus::ok();
    }

    ::ndk::ScopedAStatus set(const VehiclePropValue& value) override {
        std::vector<std::shared_ptr<IVehicleCallback>> callbacksCopy;
        {
            std::lock_guard<std::mutex> lk(mMutex);
            Key k{value.prop, value.areaId};
            mStore[k] = value;
            callbacksCopy = mCallbacks;
        }

        for (auto& cb : callbacksCopy) {
            if (cb) (void)cb->onPropertyEvent(value);
        }
        return ::ndk::ScopedAStatus::ok();
    }

    ::ndk::ScopedAStatus registerCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        std::lock_guard<std::mutex> lk(mMutex);
        if (callback) mCallbacks.push_back(callback);
        return ::ndk::ScopedAStatus::ok();
    }

    ::ndk::ScopedAStatus unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback) override {
        std::lock_guard<std::mutex> lk(mMutex);
        mCallbacks.erase(std::remove(mCallbacks.begin(), mCallbacks.end(), callback), mCallbacks.end());
        return ::ndk::ScopedAStatus::ok();
    }

private:
    std::mutex mMutex;
    std::unordered_map<Key, VehiclePropValue, KeyHash> mStore;
    std::vector<std::shared_ptr<IVehicleCallback>> mCallbacks;
};

}  // namespace

int main(int argc, char** argv) {
    android::base::InitLogging(argv, android::base::LogdLogger(android::base::SYSTEM));

    ABinderProcess_setThreadPoolMaxThreadCount(4);
    ABinderProcess_startThreadPool();

    auto service = ndk::SharedRefBase::make<VehicleHalServiceImpl>();

    const char* instance = "android.hardware.automotive.vehicle.IVehicle/default";
    binder_status_t status = AServiceManager_addService(service->asBinder().get(), instance);
    if (status != STATUS_OK) {
        LOG(ERROR) << "Failed to register IVehicle service instance: " << instance
                   << " status=" << status;
        return 1;
    }

    LOG(INFO) << "Registered IVehicle service: " << instance;
    ABinderProcess_joinThreadPool();
    return 0;
}
"""
        self.writer.write(self.impl_cpp, cpp.rstrip() + "\n")


def generate_vhal_service(spec, plan=None):
    return VHALServiceAgent().run(spec.to_llm_spec())
