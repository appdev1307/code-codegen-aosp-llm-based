# FILE: agents/vhal_aidl_agent.py
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, Union
from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_AIDL_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Your task is to generate correct AIDL interface files based on the provided plan.\n"
            "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
            "If you cannot produce perfect JSON, output exactly: {\"files\": []}"
        )

        self.base_dir = "hardware/interfaces/automotive/vehicle/aidl"
        self.pkg_dir = f"{self.base_dir}/android/hardware/automotive/vehicle"
        self.required_files = [
            f"{self.pkg_dir}/IVehicle.aidl",
            f"{self.pkg_dir}/IVehicleCallback.aidl",
            f"{self.pkg_dir}/VehiclePropValue.aidl",
        ]

    def build_prompt(self, plan_text: str) -> str:
        required_list = "\n".join(f"- {f}" for f in self.required_files)
        return f"""
Generate the core AOSP Vehicle HAL AIDL files.

MANDATORY OUTPUT FORMAT:
Return ONLY valid JSON:
{{
  "files": [
    {{
      "path": "<exact path>",
      "content": "full AIDL content with \\n for newlines"
    }}
  ]
}}

CRITICAL RULES:
- Output ONLY the JSON object. No extra text, no fences, no comments.
- Use exact file paths (no variations).
- Escape properly: newlines as \\n, quotes as \\\"
- All files must have package android.hardware.automotive.vehicle;

REQUIRED FILES (generate exactly these three):
{required_list}

AIDL REQUIREMENTS:
- IVehicle.aidl: interface with get(), set(), registerCallback(), unregisterCallback()
- IVehicleCallback.aidl: interface with onPropertyEvent(in VehiclePropValue)
- VehiclePropValue.aidl: parcelable with prop, areaId, timestamp, intValues, floatValues, boolValues, stringValue

PLAN CONTEXT (use only if adding custom extensions):
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
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID OR INCOMPLETE.\n" \
                                "You MUST output valid JSON with ALL three required files and correct paths.\n" \
                                "Fix all issues. No explanations."

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
        print(f"[WARN] {self.name}: LLM failed both attempts → using deterministic fallback")
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

            content = content.rstrip() + "\n"  # Normalize ending
            self.writer.write(safe_path, content)
            written_paths.append(safe_path)

        missing = [p for p in self.required_files if p not in written_paths]
        report["paths"] = written_paths
        report["missing_required"] = missing

        self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return len(missing) == 0

    def _dump_raw(self, text: str, label: str) -> None:
        (self.raw_dir / f"VHAL_AIDL_RAW_{label}.txt").write_text(text or "[EMPTY]", encoding="utf-8")

    def _sanitize_path(self, rel_path: str) -> Optional[str]:
        if not rel_path:
            return None
        p = rel_path.replace("\\", "/").strip("/")
        p = re.sub(r"/+", "/", p)
        if ".." in p.split("/") or not p.startswith("hardware/interfaces/automotive/vehicle/aidl/"):
            return None
        return p

    def _write_fallback(self) -> None:
        iv = """package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehiclePropValue;
import android.hardware.automotive.vehicle.IVehicleCallback;

interface IVehicle {
    VehiclePropValue get(int propId, int areaId);
    void set(in VehiclePropValue value);
    void registerCallback(in IVehicleCallback callback);
    void unregisterCallback(in IVehicleCallback callback);
}
"""

        cb = """package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehiclePropValue;

interface IVehicleCallback {
    void onPropertyEvent(in VehiclePropValue value);
}
"""

        vp = """package android.hardware.automotive.vehicle;

parcelable VehiclePropValue {
    int prop;
    int areaId;
    long timestamp;
    int[] intValues;
    float[] floatValues;
    boolean[] boolValues;
    String stringValue;
}
"""

        files = [
            (f"{self.pkg_dir}/IVehicle.aidl", iv),
            (f"{self.pkg_dir}/IVehicleCallback.aidl", cb),
            (f"{self.pkg_dir}/VehiclePropValue.aidl", vp),
        ]

        for path, content in files:
            self.writer.write(path, content.rstrip() + "\n")


def generate_vhal_aidl(plan_or_spec: Union[str, Dict[str, Any], Any]) -> bool:
    if isinstance(plan_or_spec, str):
        plan_text = plan_or_spec
    elif isinstance(plan_or_spec, dict):
        plan_text = json.dumps(plan_or_spec, separators=(",", ":"))
    else:
        try:
            plan_text = plan_or_spec.to_llm_spec()
        except:
            plan_text = "{}"

    return VHALAidlAgent().run(plan_text)