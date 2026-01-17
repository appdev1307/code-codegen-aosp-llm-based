# FILE: agents/vhal_aidl_build_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlBuildAgent:
    """
    Generates build glue for AIDL interface:
      hardware/interfaces/automotive/vehicle/aidl/Android.bp
    LLM-first, deterministic fallback.
    """

    def __init__(self):
        self.name = "VHAL AIDL Build Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are an expert Android Soong build engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output only multi-file blocks starting with '--- FILE:'.\n"
            "No explanations.\n"
        )

        self.bp_path = "hardware/interfaces/automotive/vehicle/aidl/Android.bp"
        self.aidl_srcs = [
            "android/hardware/automotive/vehicle/IVehicle.aidl",
            "android/hardware/automotive/vehicle/IVehicleCallback.aidl",
            "android/hardware/automotive/vehicle/VehiclePropValue.aidl",
        ]

    def build_prompt(self, spec_text: str) -> str:
        # Few-shot format demonstration
        example = f"""--- FILE: {self.bp_path} ---
aidl_interface {{
    name: "android.hardware.automotive.vehicle",
    vendor_available: true,
    srcs: [
        "android/hardware/automotive/vehicle/IVehicle.aidl",
    ],
    versions: ["1"],
    stability: "vintf",
    backend: {{
        ndk: {{ enabled: true }},
        cpp: {{ enabled: false }},
        java: {{ enabled: false }},
    }},
}}
"""
        srcs_lines = "\n".join([f'        "{s}",' for s in self.aidl_srcs])

        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
THE FIRST NON-EMPTY LINE MUST START WITH: --- FILE:

Goal:
Generate a build-correct Soong Android.bp for an AAOS AIDL VHAL interface.

Constraints:
- Use aidl_interface
- name MUST be: android.hardware.automotive.vehicle
- vendor_available: true
- stability: "vintf"
- versions: ["1"]
- backend: enable ONLY ndk; disable cpp/java
- srcs MUST include exactly these AIDL files:
{srcs_lines}

Output format:
--- FILE: <relative path> ---
<file content>

Example (format only, do not copy verbatim):
{example}

Input (context only):
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)

        # Attempt 1
        out1 = call_llm(prompt, system=self.system) or ""
        self._dump_raw(out1, 1)
        if self._write_files(out1) > 0:
            print(f"[DEBUG] {self.name}: done (LLM)", flush=True)
            return out1

        # Attempt 2
        repair = prompt + "\nREPAIR: Output ONLY '--- FILE:' blocks. No prose."
        out2 = call_llm(repair, system=self.system) or ""
        self._dump_raw(out2, 2)
        if self._write_files(out2) > 0:
            print(f"[DEBUG] {self.name}: done (LLM retry)", flush=True)
            return out2

        # Fallback
        print(f"[WARN] {self.name}: LLM did not produce file blocks. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] AIDL Android.bp generated."

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
        self.writer.write(self.bp_path, content.rstrip() + "\n")

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_AIDL_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    def _write_files(self, text: str) -> int:
        if not text or not text.strip():
            return 0
        blocks = self._parse_file_blocks(self._strip_outer_fences(text))
        if not blocks:
            return 0

        n = 0
        for path, body in blocks:
            safe = self._sanitize(path)
            if safe:
                self.writer.write(safe, body.rstrip() + "\n")
                n += 1
        return n

    def _strip_outer_fences(self, text: str) -> str:
        t = text.replace("\r\n", "\n").strip()
        if t.startswith("```"):
            t = re.sub(r"(?m)^```[^\n]*\n", "", t, count=1)
            t = re.sub(r"(?m)\n```$", "", t, count=1)
        return t + "\n"

    def _parse_file_blocks(self, text: str) -> List[Tuple[str, str]]:
        pat = re.compile(
            r"(?ms)^\s*---\s*FILE:\s*(?P<path>[^-\n]+?)\s*---\s*\n(?P<body>.*?)(?=^\s*---\s*FILE:\s*|\Z)"
        )
        out: List[Tuple[str, str]] = []
        for m in pat.finditer(text):
            p = (m.group("path") or "").strip()
            b = (m.group("body") or "").rstrip() + "\n"
            if p and b.strip():
                out.append((p, b))
        return out

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
