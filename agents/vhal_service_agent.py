# FILE: agents/vhal_service_agent.py

import json
import re
from pathlib import Path
from typing import List, Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are a deterministic code generator.\n"
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"files\": []}\n"
        )

        self.impl_cpp = "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"

        self.required_files = [
            self.impl_cpp,
        ]

    def build_prompt(self, spec_text: str) -> str:
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
- Thread-safe store
- Notify callbacks on set()
- Must compile (include required headers; correct overrides; no TODOs)

SPEC CONTEXT (do not repeat):
{spec_text}

RETURN JSON NOW.
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt(spec_text)

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, 1)
        if self._write_json_files(out1):
            print(f"[DEBUG] {self.name}: LLM wrote service files", flush=True)
            return out1

        repair = (
            prompt
            + "\nREPAIR (MANDATORY):\n"
              "- Your previous output was INVALID.\n"
              "- Output ONLY JSON exactly matching the schema.\n"
              "- Do NOT include markdown or any explanation.\n"
              "- Ensure the file path is exactly required.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        if self._write_json_files(out2):
            print(f"[DEBUG] {self.name}: LLM wrote service files (after repair)", flush=True)
            return out2

        print(f"[WARN] {self.name}: LLM did not produce valid JSON. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic VHAL service generated."

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _write_json_files(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False

        if not t.startswith("{"):
            return False
        low = t.lower()
        if "```" in t or "\n###" in t or "com.example" in low or "androidmanifest" in low:
            return False
        if "here are" in low or "sure," in low or "examples" in low:
            return False

        try:
            data = json.loads(t)
        except Exception:
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
#include <mutex>
#include <unordered_map>
#include <vector>

#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>

using ::aidl::android::hardware::automotive::vehicle::BnIVehicle;
using ::aidl::android::hardware::automotive::vehicle::IVehicleCallback;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropValue;

namespace {

class VehicleHalServiceImpl final : public BnIVehicle {
public:
    ::ndk::ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return) override {
        (void)areaId;
        if (_aidl_return == nullptr) {
            return ::ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
        }

        std::lock_guard<std::mutex> lk(mMutex);
        auto it = mStore.find(propId);
        if (it != mStore.end()) {
            *_aidl_return = it->second;
        } else {
            *_aidl_return = VehiclePropValue{};
        }
        return ::ndk::ScopedAStatus::ok();
    }

    ::ndk::ScopedAStatus set(const VehiclePropValue& value) override {
        std::vector<std::shared_ptr<IVehicleCallback>> callbacksCopy;
        {
            std::lock_guard<std::mutex> lk(mMutex);
            const int32_t propId = 0;  // fallback AIDL has empty parcelable
            mStore[propId] = value;
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
    std::unordered_map<int32_t, VehiclePropValue> mStore;
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


def generate_vhal_service(spec):
    return VHALServiceAgent().run(spec.to_llm_spec())
