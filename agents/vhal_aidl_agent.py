# FILE: agents/vhal_aidl_agent.py

import json
import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"

        # Stage-1 goes to draft
        self.output_root = "output/.llm_draft/latest"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

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

- VehiclePropValue MUST be parcelable.
  NOTE: include at least fields prop:int, areaId:int, timestamp:long.

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
            print(f"[DEBUG] {self.name}: LLM wrote AIDL files (draft)", flush=True)
            return out1

        # Repair: ask for strict JSON only, include only error hint, not full previous blob
        repair = (
            prompt
            + "\nREPAIR (MANDATORY): Return ONLY JSON matching schema.\n"
              "Common mistakes to avoid:\n"
              "- Do NOT wrap in ``` fences\n"
              "- Do NOT add any keys other than {files:[...]}\n"
              "- Ensure all required paths are included exactly\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        if self._write_json_files(out2):
            print(f"[DEBUG] {self.name}: LLM wrote AIDL files (draft, after repair)", flush=True)
            return out2

        print(f"[WARN] {self.name}: LLM output invalid. Using deterministic fallback (draft).", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic VHAL AIDL generated (draft)."

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
        # Minimal but non-empty parcelable so service can key by propId/areaId
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


def generate_vhal_aidl(spec, plan=None):
    return VHALAidlAgent().run(spec.to_llm_spec())
