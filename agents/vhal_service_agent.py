# FILE: agents/vhal_service_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

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
            "You are a senior Android Automotive Vehicle HAL engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output only multi-file blocks starting with '--- FILE:'.\n"
            "No explanations.\n"
        )

        self.impl_cpp = "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"

    def build_prompt(self, spec_text: str) -> str:
        fewshot = f"""--- FILE: {self.impl_cpp} ---
#include <iostream>
int main() {{ std::cout << "ok"; }}
"""
        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
THE FIRST NON-EMPTY LINE MUST START WITH: --- FILE:

Glossary:
- VSS = Vehicle Signal Specification (signal tree), NOT "Vehicle Security System".

You are an AAOS Vehicle HAL C++ engineer.

Target:
- Android Automotive OS 14
- AIDL-based Vehicle HAL backend
- C++ NDK Binder

Requirements:
- Implement BnIVehicle
- Implement get(), set()
- Implement registerCallback(), unregisterCallback() if present in AIDL
- Property storage
- Callback notification
- Thread-safe
- Register service:
  android.hardware.automotive.vehicle.IVehicle/default

Hard constraints:
- No placeholders, no TODOs
- No prose, no comments, no markdown, no code fences

Paths:
- Paths MUST start with: hardware/
- Example:
  {self.impl_cpp}

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Example (format demonstration only; do not repeat verbatim):
{fewshot}

Specification:
{spec_text}

Now output the C++ service implementation files.
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)

        # Attempt 1
        result1 = call_llm(prompt, system=self.system) or ""
        self._dump_raw(result1, attempt=1)
        written1 = self._write_files(result1)
        if written1 > 0:
            print(f"[DEBUG] {self.name}: wrote {written1} files", flush=True)
            return result1

        # Attempt 2 (repair)
        repair_prompt = (
            prompt
            + "\n\nREPAIR INSTRUCTIONS (MANDATORY):\n"
              "- Your previous output was INVALID because it contained no '--- FILE:' blocks.\n"
              "- Output ONLY file blocks.\n"
              "- The first non-empty line MUST start with: --- FILE:\n"
              "- Include at minimum:\n"
              f"  1) {self.impl_cpp}\n"
              "- No prose.\n"
        )
        result2 = call_llm(repair_prompt, system=self.system) or ""
        self._dump_raw(result2, attempt=2)
        written2 = self._write_files(result2)
        if written2 > 0:
            print(f"[DEBUG] {self.name}: wrote {written2} files (after retry)", flush=True)
            return result2

        # Deterministic fallback
        print(f"[WARN] {self.name}: LLM did not produce file blocks. Using deterministic fallback.", flush=True)
        fallback_written = self._write_fallback_service()
        if fallback_written == 0:
            raise RuntimeError(
                "[FORMAT ERROR] No VHAL service files written after retry, and fallback failed. "
                f"See {self.raw_dir / 'VHAL_SERVICE_RAW_attempt1.txt'} and {self.raw_dir / 'VHAL_SERVICE_RAW_attempt2.txt'}"
            )

        return "[FALLBACK] Deterministic VHAL service skeleton generated."

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _write_files(self, text: str) -> int:
        if not text or not text.strip():
            return 0

        normalized = self._strip_outer_code_fences(text)
        blocks = self._parse_file_blocks(normalized)
        if not blocks:
            return 0

        count = 0
        for rel_path, content in blocks:
            safe_path = self._sanitize_rel_path(rel_path)
            if safe_path is None:
                continue
            self.writer.write(safe_path, content)
            count += 1
        return count

    def _strip_outer_code_fences(self, text: str) -> str:
        t = text.replace("\r\n", "\n").strip()
        if t.startswith("```"):
            t = re.sub(r"(?m)^```[^\n]*\n", "", t, count=1)
            t = re.sub(r"(?m)\n```$", "", t, count=1)
        return t.strip() + "\n"

    def _parse_file_blocks(self, text: str) -> List[Tuple[str, str]]:
        pattern = re.compile(
            r"(?ms)^\s*---\s*FILE:\s*(?P<path>[^-\n]+?)\s*---\s*\n(?P<body>.*?)(?=^\s*---\s*FILE:\s*|\Z)"
        )
        blocks: List[Tuple[str, str]] = []
        for m in pattern.finditer(text):
            rel_path = (m.group("path") or "").strip()
            body = (m.group("body") or "").rstrip() + "\n"
            if rel_path and body.strip():
                blocks.append((rel_path, body))
        return blocks

    def _sanitize_rel_path(self, rel_path: str) -> Optional[str]:
        p = (rel_path or "").strip().replace("\\", "/")
        p = re.sub(r"/+", "/", p)

        if not p:
            return None
        if p.startswith("/"):
            return None
        if ".." in p.split("/"):
            return None
        if not p.startswith("hardware/"):
            return None
        return p

    def _write_fallback_service(self) -> int:
        cpp = """\
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

            // VehiclePropValue is an empty parcelable in your fallback AIDL.
            // Store under propId=0 until you define fields.
            const int32_t propId = 0;
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
        return 1


def generate_vhal_service(spec):
    return VHALServiceAgent().run(spec.to_llm_spec())
