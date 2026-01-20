# FILE: agents/vhal_aidl_agent.py
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Union
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
        return f"""
Generate the core AOSP Vehicle HAL AIDL files based on the plan below.

MANDATORY OUTPUT FORMAT:
Return ONLY a valid JSON object with this exact structure:
{{
  "files": [
    {{
      "path": "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicle.aidl",
      "content": "full AIDL file content as a single string (use \\n for newlines)"
    }},
    ...
  ]
}}

CRITICAL RULES:
- Output ONLY the JSON. Nothing else.
- NO markdown, NO ```json fences, NO comments.
- All file paths MUST be exactly as listed below.
- Content must be valid AIDL syntax.
- Use proper escaping: newlines as \\n, quotes as \\\"

REQUIRED FILES (you MUST generate all three exactly):
{chr(10).join(f"- {f}" for f in self.required_files)}

AIDL SPECIFIC REQUIREMENTS:
- package android.hardware.automotive.vehicle; must be present in all files
- IVehicle.aidl must include:
  - VehiclePropValue get(int propId, int areaId);
  - void set(in VehiclePropValue value);
  - void registerCallback(in IVehicleCallback callback);
  - void unregisterCallback(in IVehicleCallback callback);
- IVehicleCallback.aidl must include:
  - void onPropertyEvent(in VehiclePropValue value);
- VehiclePropValue.aidl must be parcelable and include at least:
  int prop; int areaId; long timestamp;
  int[] intValues; float[] floatValues; boolean[] boolValues; String stringValue;

PLAN (use this to add custom properties if needed):
{plan_text}

NOW OUTPUT ONLY THE JSON:
""".strip()

    def run(self, plan_text: str) -> bool:
        """Returns True if LLM successfully generated valid files, False if fallback used."""
        print(f"[DEBUG] {self.name}: start")

        prompt = self.build_prompt(plan_text)

        # First attempt with JSON mode enforced
        raw_output = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",  # Critical for reliability
        )
        self._dump_raw(raw_output, "attempt1")
        success = self._try_write_from_output(raw_output)

        if success:
            print(f"[DEBUG] {self.name}: done (LLM success on first try)")
            return True

        # Second attempt: repair prompt
        print(f"[DEBUG] {self.name}: first attempt failed, trying repair")
        repair_prompt = prompt + "\n\nPREVIOUS OUTPUT WAS INVALID.\n" \
            "FIX IT NOW. You MUST include ALL required files with correct paths.\n" \
            "Output ONLY valid JSON. No excuses."

        raw_output2 = call_llm(
            prompt=repair_prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",
        )
        self._dump_raw(raw_output2, "attempt2")
        success = self._try_write_from_output(raw_output2)

        if success:
            print(f"[DEBUG] {self.name}: done (LLM success after repair)")
            return True

        # Final fallback
        print(f"[WARN] {self.name}: LLM output invalid. Using deterministic fallback (draft).")
        self._write_fallback()
        return False

    def _try_write_from_output(self, text: str) -> bool:
        data, err = parse_json_object(text.strip())
        report = {
            "parse_error": err,
            "valid_json": data is not None,
            "files_found": 0,
            "paths": [],
            "missing_required": [],
        }

        if not data or "files" not in data or not isinstance(data["files"], list):
            report["missing_required"] = self.required_files[:]
            self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return False

        files = data["files"]
        report["files_found"] = len(files)
        found_paths = []

        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "").strip()
            content = item.get("content", "")
            if not path or not isinstance(content, str):
                continue

            safe_path = self._sanitize_path(path)
            if not safe_path or safe_path not in self.required_files:
                continue

            found_paths.append(safe_path)
            if not content.endswith("\n"):
                content += "\n"
            self.writer.write(safe_path, content)

        missing = [p for p in self.required_files if p not in found_paths]
        report["paths"] = found_paths
        report["missing_required"] = missing

        self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return len(missing) == 0

    def _dump_raw(self, text: str, label: str) -> None:
        (self.raw_dir / f"VHAL_AIDL_RAW_{label}.txt").write_text(text or "[EMPTY]", encoding="utf-8")

    def _sanitize_path(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip("/")
        p = re.sub(r"/+", "/", p)
        if ".." in p or not p.startswith("hardware/interfaces/automotive/vehicle/aidl/"):
            return None
        return p

    def _write_fallback(self) -> None:
        """Minimal but correct AIDL stubs — safe and buildable"""
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

        for path, content in [
            (f"{self.pkg_dir}/IVehicle.aidl", iv),
            (f"{self.pkg_dir}/IVehicleCallback.aidl", cb),
            (f"{self.pkg_dir}/VehiclePropValue.aidl", vp),
        ]:
            self.writer.write(path, content + "\n")


def generate_vhal_aidl(plan_or_spec: Union[str, Dict[str, Any], Any]) -> bool:
    """
    Generates VHAL AIDL files.
    Returns True if LLM succeeded, False if fallback was used.
    """
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