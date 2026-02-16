# agents/vhal_aidl_agent.py
from __future__ import annotations
import json
import re
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Union

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent (VSS-aware)"
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_AIDL_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Generate correct, production-grade AIDL files from the provided VSS spec.\n"
            "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
            "If you cannot produce perfect JSON, output exactly: {\"files\": []}"
        )

        self.base_dir = "hardware/interfaces/automotive/vehicle/aidl"
        self.pkg_dir = f"{self.base_dir}/android/hardware/automotive/vehicle"

        self.required_files = [
            f"{self.pkg_dir}/IVehicle.aidl",
            f"{self.pkg_dir}/IVehicleCallback.aidl",
            f"{self.pkg_dir}/VehiclePropValue.aidl",
            f"{self.pkg_dir}/VehiclePropertyVss.aidl",
        ]

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
        prop_lines = [f"- {p.get('name')} ({p.get('type')}, {p.get('access')})" for p in props]

        required_list = "\n".join(f"- {f}" for f in self.required_files)

        return f"""
Generate complete Vehicle HAL AIDL files including vendor-specific property enum.

MANDATORY OUTPUT FORMAT:
Return ONLY valid JSON:
{{
  "files": [
    {{"path": "...", "content": "full file content with \\n for newlines"}}
  ]
}}

CRITICAL RULES:
- Output ONLY the JSON object. No extra text, no fences, no comments.
- Use exact file paths listed below.
- Escape properly: newlines as \\n, quotes as \\\"

REQUIRED FILES (generate ALL of them):
{required_list}

AIDL REQUIREMENTS:
- IVehicle.aidl: standard interface (get, set, registerCallback, unregisterCallback)
- IVehicleCallback.aidl: onPropertyEvent(in VehiclePropValue)
- VehiclePropValue.aidl: parcelable with all standard fields
- VehiclePropertyVss.aidl: MUST contain @Backing(type="int") enum VehiclePropertyVss with ALL properties from spec

VSS PROPERTIES TO INCLUDE IN VehiclePropertyVss.aidl:
{'\n'.join(prop_lines)}

VehiclePropertyVss.aidl example structure:
@VintfStability
@Backing(type="int")
enum VehiclePropertyVss {{
    {props[0].get('name','PROP_0')} = 0xF0000000,
    {props[1].get('name','PROP_1')} = 0xF0000001,
    // ... continue sequentially
}}

Use the full VSS spec below to determine property names, types, access rights, etc.

FULL VSS SPEC:
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
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID OR INCOMPLETE.\n" \
                                "You MUST output valid JSON with ALL four required files and correct paths.\n" \
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

            content = content.rstrip() + "\n"
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

        vss_enum = """package android.hardware.automotive.vehicle;

@VintfStability
@Backing(type="int")
enum VehiclePropertyVss {
    VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED = 0xF0000000,
    VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENGAGED = 0xF0000001,
    // Add more placeholders if needed - real LLM generation will fill them
}
"""

        files = [
            (f"{self.pkg_dir}/IVehicle.aidl", iv),
            (f"{self.pkg_dir}/IVehicleCallback.aidl", cb),
            (f"{self.pkg_dir}/VehiclePropValue.aidl", vp),
            (f"{self.pkg_dir}/VehiclePropertyVss.aidl", vss_enum),
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