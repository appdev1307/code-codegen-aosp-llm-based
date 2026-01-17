# FILE: agents/vhal_aidl_build_agent.py

import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.llm_file_parser import parse_files_json


class VHALAidlBuildAgent:
    def __init__(self):
        self.name = "VHAL AIDL Build Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are an expert Android Soong build engineer.\n"
            "Return STRICT JSON only.\n"
            "No prose.\n"
        )

        self.bp_path = "hardware/interfaces/automotive/vehicle/aidl/Android.bp"
        self.aidl_srcs = [
            "android/hardware/automotive/vehicle/IVehicle.aidl",
            "android/hardware/automotive/vehicle/IVehicleCallback.aidl",
            "android/hardware/automotive/vehicle/VehiclePropValue.aidl",
        ]

    def build_prompt(self, spec_text: str) -> str:
        srcs_lines = "\n".join([f'        "{s}",' for s in self.aidl_srcs])

        return f"""
OUTPUT MUST BE STRICT JSON ONLY (NO MARKDOWN, NO PROSE).
Return exactly:
{{
  "files": [
    {{
      "path": "{self.bp_path}",
      "content": "..."
    }}
  ]
}}

Constraints for Android.bp:
- Must define aidl_interface
- name: "android.hardware.automotive.vehicle"
- vendor_available: true
- srcs MUST be exactly:
{srcs_lines}
- versions: ["1"]
- stability: "vintf"
- backend: enable ndk only; disable cpp/java

Context (spec text; do not echo):
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt(spec_text)

        # Attempt 1 (JSON)
        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0, response_format="json") or ""
        self._dump_raw(out1, 1)
        if self._write_json(out1):
            return out1

        # Attempt 2 (JSON repair, disable formatter in case model doesn't support it)
        repair = prompt + "\nREPAIR: Output STRICT JSON with files[].path and files[].content."
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0, response_format=None) or ""
        self._dump_raw(out2, 2)
        if self._write_json(out2):
            return out2

        print(f"[WARN] {self.name}: LLM output invalid. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] AIDL Android.bp generated."

    def _write_json(self, text: str) -> bool:
        try:
            files = parse_files_json(text)
        except Exception:
            return False

        wrote = 0
        for p, c in files:
            safe = self._sanitize(p)
            if not safe:
                continue
            self.writer.write(safe, c)
            wrote += 1
        return wrote > 0

    def _write_fallback(self) -> None:
        content = """\
aidl_interface {
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
        self.writer.write(self.bp_path, content)

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_AIDL_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = (rel_path or "").strip().replace("\\", "/")
        p = re.sub(r"/+", "/", p)
        if not p or p.startswith("/") or ".." in p.split("/"):
            return None
        if not p.startswith("hardware/"):
            return None
        return p


def generate_vhal_aidl_bp(spec) -> str:
    spec_text = spec.to_llm_spec() if hasattr(spec, "to_llm_spec") else str(spec)
    return VHALAidlBuildAgent().run(spec_text)
