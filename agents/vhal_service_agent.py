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
    # Max properties per LLM call — avoids 1200s timeouts on 32B models
    CHUNK_SIZE = 12

    def __init__(self, output_root: str = "output/.llm_draft/latest"):
        self.name = "VHAL C++ Service Agent (VSS-aware + strong prompt)"
        self.output_root = output_root
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

3. Implement ALL these methods with EXACT names and signatures (names must match exactly):
   - getAllPropertyConfigs(std::vector<VehiclePropConfig>* _aidl_return)   ← EXACT NAME REQUIRED
   - getValues(const std::vector<GetValueRequest>& requests, std::vector<GetValueResult>* _aidl_return)  ← EXACT NAME REQUIRED
   - setValues(const std::vector<SetValueRequest>& requests)               ← EXACT NAME REQUIRED
   - get(int32_t propId, int32_t areaId, VehiclePropValue* _aidl_return)
   - set(const VehiclePropValue& value)
   - subscribe(const std::shared_ptr<IVehicleCallback>& callback, const std::vector<SubscribeOptions>& options)
   - unsubscribe(const std::shared_ptr<IVehicleCallback>& callback, const std::vector<int32_t>& propIds)
   - registerCallback(const std::shared_ptr<IVehicleCallback>& callback)
   - unregisterCallback(const std::shared_ptr<IVehicleCallback>& callback)
   Include VehiclePropValue type in all value handling.

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
        props = self._parse_properties(plan_text)

        if not props or len(props) <= self.CHUNK_SIZE:
            return self._run_single(plan_text, "attempt1")

        # Large spec: generate VssPropertyIds.h from all props (one call),
        # then generate VehicleHalService.cpp in chunks and merge the
        # getAllPropConfigs body across chunks.
        print(f"[DEBUG] {self.name}: {len(props)} properties -> "
              f"chunking into batches of {self.CHUNK_SIZE}")
        chunks = [props[i:i + self.CHUNK_SIZE]
                  for i in range(0, len(props), self.CHUNK_SIZE)]

        # Step A: generate the header (VssPropertyIds.h) from all props — it's small
        header_ok = self._generate_header(props)

        # Step B: generate CPP per chunk, extract getAllPropConfigs blocks, merge
        all_prop_config_blocks: list = []
        cpp_boilerplate_written = False

        for idx, chunk in enumerate(chunks):
            chunk_plan = self._make_chunk_plan(plan_text, chunk, idx, len(chunks))
            raw = call_llm(
                prompt=self.build_prompt(chunk_plan),
                system=self.system_prompt,
                temperature=0.25,
                top_p=0.95,
                response_format="json",
            )
            self._dump_raw(raw, f"chunk{idx + 1}")
            blocks = self._extract_prop_config_block(raw)
            all_prop_config_blocks.extend(blocks)
            print(f"[DEBUG] {self.name}: chunk {idx + 1}/{len(chunks)} "
                  f"-> {len(blocks)} prop config entries")
            if idx == 0:
                cpp_boilerplate_written = self._write_cpp_from_raw(raw)

        cpp_ok = self._merge_cpp_prop_configs(all_prop_config_blocks)

        if not cpp_boilerplate_written:
            print(f"[WARN] {self.name}: CPP boilerplate missing -> repair")
            self._run_single(plan_text, "repair_cpp")
        if not header_ok:
            print(f"[WARN] {self.name}: header missing -> fallback header")
            self._write_fallback_header(props)

        success = (cpp_boilerplate_written or cpp_ok) and header_ok
        print(f"[DEBUG] {self.name}: done (chunked {'success' if success else 'partial/fallback'})")
        return success

    def _run_single(self, plan_text: str, label: str) -> bool:
        prompt = self.build_prompt(plan_text)
        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.25,
            top_p=0.95,
            response_format="json",
        )
        self._dump_raw(raw, label)
        if self._try_write_from_output(raw):
            print(f"[DEBUG] {self.name}: done ({label} success)")
            return True
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
        self._dump_raw(raw2, label + "_repair")
        if self._try_write_from_output(raw2):
            print(f"[DEBUG] {self.name}: success ({label} repair)")
            return True
        print(f"[WARN] {self.name}: LLM failed -> fallback")
        self._write_fallback()
        return False

    def _make_chunk_plan(self, plan_text: str, chunk: list, idx: int, total: int) -> str:
        import yaml as _yaml
        try:
            if plan_text.strip().startswith("spec_version"):
                base = _yaml.safe_load(plan_text)
            else:
                import json as _json
                base = _json.loads(plan_text)
        except Exception:
            base = {}
        base = dict(base)
        base["properties"] = chunk
        base["_chunk_info"] = f"{idx + 1}/{total}"
        import json as _json
        return _json.dumps(base, separators=(",", ":"))

    def _generate_header(self, props: list) -> bool:
        """Generate VssPropertyIds.h from the full property list (small, fast)."""
        lines = ["#pragma once", "#include <cstdint>", ""]
        for i, p in enumerate(props):
            name = p.get("name", f"PROP_{i}") if isinstance(p, dict) else f"PROP_{i}"
            lines.append(f"constexpr int32_t {name} = 0xF{i:07X};")
        lines.append("")
        content = "\n".join(lines)
        self.writer.write(self.ids_header, content)
        return True

    def _write_fallback_header(self, props: list) -> None:
        self._generate_header(props)

    def _extract_prop_config_block(self, raw: str) -> list:
        """Extract VehiclePropConfig cfg blocks from C++ source in LLM JSON output."""
        try:
            data = json.loads(raw.strip())
            for item in data.get("files", []):
                path = (item.get("path") or "").strip()
                if "VehicleHalService.cpp" in path:
                    content = item.get("content", "")
                    blocks = []
                    # Extract each "VehiclePropConfig cfg;" block up to push_back
                    pattern = r'(VehiclePropConfig cfg[^;]*;.*?_aidl_return->push_back[^;]+;)'
                    for m in re.findall(pattern, content, re.DOTALL):
                        blocks.append(m.strip())
                    return blocks
        except Exception:
            pass
        return []

    def _write_cpp_from_raw(self, raw: str) -> bool:
        """Write VehicleHalService.cpp from first-chunk LLM output (may be partial)."""
        try:
            data = json.loads(raw.strip())
            for item in data.get("files", []):
                path = self._sanitize_path((item.get("path") or "").strip())
                content = item.get("content")
                if path == self.impl_cpp and isinstance(content, str):
                    self.writer.write(path, content.rstrip() + "\n")
                    return True
        except Exception:
            pass
        return False

    def _merge_cpp_prop_configs(self, blocks: list) -> bool:
        """Rewrite getAllPropConfigs in the saved CPP with merged blocks."""
        if not blocks:
            return False
        cpp_path = Path(self.output_root) / self.impl_cpp
        if not cpp_path.exists():
            return False
        try:
            original_cpp = cpp_path.read_text(encoding="utf-8")
            merged_body = "\n\n    ".join(blocks)
            # Replace the getAllPropConfigs body with merged blocks
            pattern = r'(getAllPropConfigs\([^)]*\)[^{]*\{)[^}]*(\s*return ndk::ScopedAStatus::ok\(\);\s*\})'
            replacement = f"\1\n    {merged_body}\n    \2"
            new_cpp = re.sub(pattern, replacement, original_cpp, flags=re.DOTALL)
            if new_cpp != original_cpp:
                cpp_path.write_text(new_cpp, encoding="utf-8")
                return True
        except Exception:
            pass
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
        cpp = """// Fallback minimal C++ VHAL implementation
#include <aidl/android/hardware/automotive/vehicle/BnIVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/IVehicleCallback.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropConfig.h>
#include "VssPropertyIds.h"

namespace aidl::android::hardware::automotive::vehicle {

class VehicleHalService : public BnIVehicle {
public:
    ndk::ScopedAStatus getAllPropertyConfigs(std::vector<VehiclePropConfig>* _aidl_return) override {
        if (!_aidl_return) return ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
        _aidl_return->clear();
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus getValues(
        const std::vector<GetValueRequest>& requests,
        std::vector<GetValueResult>* _aidl_return) override {
        if (!_aidl_return) return ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
        for (const auto& req : requests) {
            GetValueResult res;
            res.requestId = req.requestId;
            res.status = StatusCode::NOT_AVAILABLE;
            _aidl_return->push_back(std::move(res));
        }
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus setValues(
        const std::vector<SetValueRequest>& requests) override {
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus registerCallback(
        const std::shared_ptr<IVehicleCallback>& callback) override {
        return ndk::ScopedAStatus::ok();
    }

    ndk::ScopedAStatus unregisterCallback(
        const std::shared_ptr<IVehicleCallback>& callback) override {
        return ndk::ScopedAStatus::ok();
    }
};

}  // namespace aidl::android::hardware::automotive::vehicle
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

def generate_vhal_service(plan_or_spec: Union[str, Dict[str, Any], Any],
                          output_root: str = "output/.llm_draft/latest") -> bool:
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

    agent = VHALServiceAgent(output_root=output_root)
    return agent.run(plan_text)