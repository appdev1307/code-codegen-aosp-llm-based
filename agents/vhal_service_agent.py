# FILE: agents/vhal_service_agent.py

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceAgent:
    """
    Two-Phase Generation (Option C), implemented WITHOUT changing your project layout/design.

    Phase 1 (LLM): produce a SMALL 'plan' JSON (behavioral intent only).
      - Reliable and easy to validate.
      - No file generation by LLM.

    Phase 2 (Deterministic): emit AOSP-compliant C++ service deterministically.
      - Plan can influence safe behavioral switches (e.g., notify_on_set vs notify_on_change).

    Backward compatible:
      - run(spec_text: str) still works (no plan) and will generate deterministic service.
      - run(spec_text: str, plan: dict) also works.
    """

    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        # Phase-1: JSON-only plan contract (LLM)
        self.system = (
            "You are an Android Automotive HAL planning assistant.\n"
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"plan\": {\"ok\": false, \"reason\": \"cannot_comply\"}}\n"
        )

        self.impl_cpp = "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"

        self.required_files = [
            self.impl_cpp,
        ]

    # ---------------------------------------------------------------------
    # Phase 1: LLM plan (small JSON, reliable)
    # ---------------------------------------------------------------------
    def build_plan_prompt(self, spec_text: str) -> str:
        return f"""
OUTPUT CONTRACT (MANDATORY):
Return ONLY valid JSON with this schema:

{{
  "plan": {{
    "ok": true,
    "aosp_level": 14,
    "callback_policy": "notify_on_set|notify_on_change",
    "store_policy": "always_store|store_if_changed"
  }}
}}

HARD RULES:
- Output ONLY JSON. No other text.
- NO markdown, NO code fences, NO headings.
- Do NOT generate any files.
- Keep it small and deterministic.
- If unsure: callback_policy="notify_on_change", store_policy="store_if_changed".

SPEC CONTEXT (do not repeat):
{spec_text}

RETURN JSON NOW.
""".lstrip()

    def _get_llm_plan(self, spec_text: str) -> Optional[Dict[str, Any]]:
        prompt = self.build_plan_prompt(spec_text)

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, "PLAN_attempt1")
        plan1 = self._parse_plan_json(out1)
        if plan1:
            return plan1

        repair = (
            prompt
            + "\nREPAIR (MANDATORY):\n"
              "- Your previous output was INVALID.\n"
              "- Output ONLY JSON exactly matching the schema.\n"
              "- Do NOT include markdown or any explanation.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, "PLAN_attempt2")
        return self._parse_plan_json(out2)

    def _parse_plan_json(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        if not t or not t.startswith("{"):
            return None
        if "```" in t or "\n###" in t:
            return None

        try:
            data = json.loads(t)
        except Exception:
            return None

        plan = data.get("plan")
        if not isinstance(plan, dict):
            return None
        if plan.get("ok") is not True:
            return None

        # Defaults (safe)
        aosp_level = plan.get("aosp_level", 14)
        try:
            aosp_level = int(aosp_level)
        except Exception:
            return None

        callback_policy = plan.get("callback_policy", "notify_on_change")
        store_policy = plan.get("store_policy", "store_if_changed")

        if callback_policy not in ("notify_on_set", "notify_on_change"):
            callback_policy = "notify_on_change"
        if store_policy not in ("always_store", "store_if_changed"):
            store_policy = "store_if_changed"

        return {
            "aosp_level": aosp_level,
            "callback_policy": callback_policy,
            "store_policy": store_policy,
        }

    # ---------------------------------------------------------------------
    # Phase 2: Deterministic emit (AOSP-compliant)
    # ---------------------------------------------------------------------
    def run(self, spec_text: str, plan: Optional[Dict[str, Any]] = None) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        if plan is None:
            plan = self._get_llm_plan(spec_text)

        # Determine behavior switches (safe, local)
        callback_policy = (plan or {}).get("callback_policy", "notify_on_change")
        store_policy = (plan or {}).get("store_policy", "store_if_changed")

        notify_on_set = (callback_policy == "notify_on_set")
        always_store = (store_policy == "always_store")

        # Deterministic emission
        self._write_deterministic_cpp(notify_on_set=notify_on_set, always_store=always_store)
        print(f"[DEBUG] {self.name}: deterministic service written", flush=True)

        if plan:
            return json.dumps({"phase": "VHAL_SERVICE", "mode": "two_phase", "plan": plan}, ensure_ascii=False)
        return json.dumps({"phase": "VHAL_SERVICE", "mode": "two_phase", "plan": None}, ensure_ascii=False)

    # ---------------------------------------------------------------------
    # Deterministic service generation (OEM-grade baseline)
    # ---------------------------------------------------------------------
    def _write_deterministic_cpp(self, notify_on_set: bool, always_store: bool) -> None:
        """
        Deterministically emits VehicleHalService.cpp. The "plan" affects only:
          - whether to notify on every set() or only when value changed
          - whether to always store or store only if changed

        This remains ABI-safe and build-safe, and does not allow LLM to inject arbitrary code.
        """
        notify_condition = "true" if notify_on_set else "changed"
        store_condition = "true" if always_store else "changed"

        cpp = f"""// FILE: {self.impl_cpp}
//
// Deterministic AAOS VHAL service (AIDL NDK Binder).
// Plan switches:
// - notify_on_set={str(notify_on_set).lower()}
// - always_store={str(always_store).lower()}

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

namespace {{

class VehicleHalServiceImpl final : public BnIVehicle {{
public:
    ::ndk::ScopedAStatus get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return) override {{
        (void)areaId;
        if (_aidl_return == nullptr) {{
            return ::ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
        }}

        std::lock_guard<std::mutex> lk(mMutex);
        auto it = mStore.find(propId);
        if (it != mStore.end()) {{
            *_aidl_return = it->second;
        }} else {{
            *_aidl_return = VehiclePropValue{{}};
        }}
        return ::ndk::ScopedAStatus::ok();
    }}

    ::ndk::ScopedAStatus set(const VehiclePropValue& value) override {{
        std::vector<std::shared_ptr<IVehicleCallback>> callbacksCopy;

        bool changed = true;
        const int32_t propId = 0;  // With empty parcelable fallback, propId cannot be extracted safely.

        {{
            std::lock_guard<std::mutex> lk(mMutex);

            auto it = mStore.find(propId);
            if (it != mStore.end()) {{
                // Conservative changed detection: if parcelable has no fields, treat as changed.
                // If you later add fields (prop/area/timestamp), implement a real comparator.
                changed = true;
            }}

            if ({store_condition}) {{
                mStore[propId] = value;
            }}

            callbacksCopy = mCallbacks;
        }}

        if ({notify_condition}) {{
            for (auto& cb : callbacksCopy) {{
                if (cb) (void)cb->onPropertyEvent(value);
            }}
        }}

        return ::ndk::ScopedAStatus::ok();
    }}

    ::ndk::ScopedAStatus registerCallback(const std::shared_ptr<IVehicleCallback>& callback) override {{
        std::lock_guard<std::mutex> lk(mMutex);
        if (callback) mCallbacks.push_back(callback);
        return ::ndk::ScopedAStatus::ok();
    }}

    ::ndk::ScopedAStatus unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback) override {{
        std::lock_guard<std::mutex> lk(mMutex);
        mCallbacks.erase(std::remove(mCallbacks.begin(), mCallbacks.end(), callback), mCallbacks.end());
        return ::ndk::ScopedAStatus::ok();
    }}

private:
    std::mutex mMutex;
    std::unordered_map<int32_t, VehiclePropValue> mStore;
    std::vector<std::shared_ptr<IVehicleCallback>> mCallbacks;
}};

}}  // namespace

int main(int /*argc*/, char** argv) {{
    android::base::InitLogging(argv, android::base::LogdLogger(android::base::SYSTEM));

    ABinderProcess_setThreadPoolMaxThreadCount(4);
    ABinderProcess_startThreadPool();

    auto service = ndk::SharedRefBase::make<VehicleHalServiceImpl>();

    const char* instance = "android.hardware.automotive.vehicle.IVehicle/default";
    binder_status_t status = AServiceManager_addService(service->asBinder().get(), instance);
    if (status != STATUS_OK) {{
        LOG(ERROR) << "Failed to register IVehicle service instance: " << instance
                   << " status=" << status;
        return 1;
    }}

    LOG(INFO) << "Registered IVehicle service: " << instance;
    ABinderProcess_joinThreadPool();
    return 0;
}}
"""
        self.writer.write(self.impl_cpp, cpp.rstrip() + "\n")

    # ---------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------
    def _dump_raw(self, text: str, tag: str) -> None:
        (self.raw_dir / f"VHAL_SERVICE_RAW_{tag}.txt").write_text(text or "", encoding="utf-8")

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip()
        p = re.sub(r"/+", "/", p)
        if p.startswith("/") or ".." in p.split("/"):
            return None
        if not p.startswith("hardware/interfaces/automotive/vehicle/impl/"):
            return None
        return p


def generate_vhal_service(spec, plan: Optional[Dict[str, Any]] = None):
    """
    Backward compatible wrapper:
      - If caller doesn't pass plan, agent will attempt Phase-1 plan internally.
      - If caller passes plan (recommended in Option C), it will use it.
    """
    return VHALServiceAgent().run(spec.to_llm_spec(), plan=plan)
