# FILE: agents/vhal_aidl_build_agent.py

import json
import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlBuildAgent:
    def __init__(self):
        self.name = "VHAL AIDL Build Agent"
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

        self.bp_path = "hardware/interfaces/automotive/vehicle/aidl/Android.bp"

        self.required_files = [self.bp_path]

    def build_prompt(self) -> str:
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
- TARGET IS AOSP Soong (Android.bp) for AAOS VHAL AIDL interface.
- Path MUST be exactly:
  {self.bp_path}

REQUIREMENTS:
- Provide an aidl_interface module named:
  "android.hardware.automotive.vehicle"
- vendor_available: true
- versions: ["1"]
- stability: "vintf"
- backend: ndk enabled, cpp/java disabled
- srcs include exactly:
  android/hardware/automotive/vehicle/IVehicle.aidl
  android/hardware/automotive/vehicle/IVehicleCallback.aidl
  android/hardware/automotive/vehicle/VehiclePropValue.aidl

RETURN JSON NOW.
""".lstrip()

    def run(self) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt()

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, 1)
        if self._write_json_files(out1):
            return out1

        repair = (
            prompt
            + "\nREPAIR (MANDATORY):\n"
              "- Output ONLY JSON exactly matching schema.\n"
              "- Ensure path is exactly required.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        if self._write_json_files(out2):
            return out2

        print(f"[WARN] {self.name}: LLM output invalid. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic AIDL Android.bp generated."

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_AIDL_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _write_json_files(self, text: str) -> bool:
        t = (text or "").strip()
        if not t or not t.startswith("{"):
            return False

        low = t.lower()
        if "```" in t or "\n###" in t or "com.example" in low:
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

        wrote = 0
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = f.get("content")
            if path != self.bp_path or not isinstance(content, str) or not content.strip():
                continue
            safe = self._sanitize(path)
            if not safe:
                continue
            if not content.endswith("\n"):
                content += "\n"
            self.writer.write(safe, content)
            wrote += 1

        return wrote == 1

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip()
        p = re.sub(r"/+", "/", p)
        if p.startswith("/") or ".." in p.split("/"):
            return None
        if p != self.bp_path:
            return None
        return p

    def _write_fallback(self) -> None:
        bp = """aidl_interface {
    name: "android.hardware.automotive.vehicle",
    vendor_available: true,
    srcs: [
        "android/hardware/automotive/vehicle/IVehicle.aidl",
        "android/hardware/automotive/vehicle/IVehicleCallback.aidl",
        "android/hardware/automotive/vehicle/VehiclePropValue.aidl",
    ],
    versions: ["1"],
    stability: "vintf",
    backend: {
        ndk: { enabled: true },
        cpp: { enabled: false },
        java: { enabled: false },
    },
}
"""
        self.writer.write(self.bp_path, bp.rstrip() + "\n")


def generate_vhal_aidl_bp():
    return VHALAidlBuildAgent().run()
