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

        # Option C: Stage-1 output is draft (NOT the final AOSP module)
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.report_path = Path(self.output_root) / "VHAL_AIDL_VALIDATE_REPORT.json"

        self.system = (
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"files\": []}\n"
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
- DO NOT generate app-layer AIDL (no com.example, no AndroidManifest, no Service).
- TARGET IS AOSP AAOS VEHICLE HAL AIDL:
  - Root: hardware/interfaces/automotive/vehicle/aidl/
  - package: android.hardware.automotive.vehicle

YOU MUST GENERATE EXACTLY THESE FILES:
- {self.required_files[0]}
- {self.required_files[1]}
- {self.required_files[2]}

AIDL REQUIREMENTS:
- IVehicle MUST declare exactly these methods:
  VehiclePropValue get(int propId, int areaId);
  void set(in VehiclePropValue value);
  void registerCallback(in IVehicleCallback callback);
  void unregisterCallback(in IVehicleCallback callback);

- IVehicleCallback MUST declare:
  void onPropertyEvent(in VehiclePropValue value);

- VehiclePropValue MUST be parcelable and MUST include at least:
  int prop;
  int areaId;
  long timestamp;
  int[] intValues;
  float[] floatValues;
  boolean[] boolValues;
  String stringValue;

PLAN CONTEXT (do not repeat):
{plan_text}

RETURN JSON NOW.
""".lstrip()

    def run(self, plan_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt(plan_text)

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, 1)
        ok1, report1 = self._validate_llm(out1)
        self.report_path.write_text(json.dumps(report1, indent=2), encoding="utf-8")

        if ok1 and self._write_json_files(out1):
            print(f"[DEBUG] {self.name}: LLM wrote AIDL files (draft)", flush=True)
            return out1

        missing = report1.get("missing_required_paths", [])
        repair = (
            prompt
            + "\nREPAIR (MANDATORY): Return ONLY JSON matching schema.\n"
              "You MUST include all required paths exactly.\n"
              f"MISSING REQUIRED PATHS: {missing}\n"
              "Do NOT add any extra keys. Do NOT wrap in code fences.\n"
        )

        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        ok2, report2 = self._validate_llm(out2)
        (Path(self.output_root) / "VHAL_AIDL_VALIDATE_REPORT_attempt2.json").write_text(
            json.dumps(report2, indent=2), encoding="utf-8"
        )

        if ok2 and self._write_json_files(out2):
            print(f"[DEBUG] {self.name}: LLM wrote AIDL files (draft, after repair)", flush=True)
            return out2

        print(f"[WARN] {self.name}: LLM output invalid. Using deterministic fallback (draft).", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic VHAL AIDL generated (draft)."

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
        (self.raw_dir / f"VHAL_AIDL_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

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
        if not p.startswith("hardware/interfaces/automotive/vehicle/aidl/"):
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
        self.writer.write(f"{self.pkg_dir}/IVehicle.aidl", iv + "\n")
        self.writer.write(f"{self.pkg_dir}/IVehicleCallback.aidl", cb + "\n")
        self.writer.write(f"{self.pkg_dir}/VehiclePropValue.aidl", vp + "\n")


def generate_vhal_aidl(plan_or_spec: Union[str, Dict[str, Any], Any]) -> str:
    """
    Accepts:
      - plan JSON string
      - plan dict
      - spec object (fallback) -> uses spec.to_llm_spec()
    """
    if isinstance(plan_or_spec, str):
        plan_text = plan_or_spec
    elif isinstance(plan_or_spec, dict):
        plan_text = json.dumps(plan_or_spec, indent=2)
    else:
        plan_text = plan_or_spec.to_llm_spec()

    return VHALAidlAgent().run(plan_text)

