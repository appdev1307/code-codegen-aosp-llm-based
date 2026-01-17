# FILE: agents/vhal_service_build_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceBuildAgent:
    """
    Generates build glue for default VHAL service:
      - impl/Android.bp
      - impl/*.rc
      - impl/*.xml (VINTF fragment)
    LLM-first, deterministic fallback.
    """

    def __init__(self):
        self.name = "VHAL Service Build Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are an expert Android Soong build + VINTF engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output only multi-file blocks starting with '--- FILE:'.\n"
            "No explanations.\n"
        )

        self.bp_path = "hardware/interfaces/automotive/vehicle/impl/Android.bp"
        self.rc_path = "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service-default.rc"
        self.xml_path = "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service-default.xml"

        self.service_name = "android.hardware.automotive.vehicle-service-default"

    def build_prompt(self, spec_text: str) -> str:
        example = f"""--- FILE: {self.bp_path} ---
cc_binary {{
    name: "{self.service_name}",
    vendor: true,
    relative_install_path: "hw",
    srcs: ["VehicleHalService.cpp"],
}}
"""
        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
THE FIRST NON-EMPTY LINE MUST START WITH: --- FILE:

Goal:
Generate build-correct Soong + init + VINTF fragments for an AAOS 14 vendor AIDL VHAL service.

Constraints (MUST):
- cc_binary name MUST be: {self.service_name}
- vendor: true
- relative_install_path: "hw"
- srcs MUST include: VehicleHalService.cpp
- shared_libs MUST include:
  - libbase, liblog, libutils, libbinder_ndk
  - android.hardware.automotive.vehicle-V1-ndk
- init_rc MUST include: android.hardware.automotive.vehicle-service-default.rc
- vintf_fragments MUST include: android.hardware.automotive.vehicle-service-default.xml

Also generate:
1) init rc file at: {self.rc_path}
   - service executable: /vendor/bin/hw/{self.service_name}
   - class hal, user system, group system, oneshot

2) VINTF fragment at: {self.xml_path}
   - AIDL HAL name: android.hardware.automotive.vehicle
   - version: 1
   - fqname: IVehicle/default

Output format:
--- FILE: <relative path> ---
<file content>

Example (format only):
{example}

Input (context only):
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)

        out1 = call_llm(prompt, system=self.system) or ""
        self._dump_raw(out1, 1)
        if self._write_files(out1) > 0:
            print(f"[DEBUG] {self.name}: done (LLM)", flush=True)
            return out1

        out2 = call_llm(prompt + "\nREPAIR: Output ONLY '--- FILE:' blocks. No prose.", system=self.system) or ""
        self._dump_raw(out2, 2)
        if self._write_files(out2) > 0:
            print(f"[DEBUG] {self.name}: done (LLM retry)", flush=True)
            return out2

        print(f"[WARN] {self.name}: LLM did not produce file blocks. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] Service build glue generated."

    def _write_fallback(self) -> None:
        bp = f"""\
cc_binary {{
    name: "{self.service_name}",
    vendor: true,
    relative_install_path: "hw",
    srcs: ["VehicleHalService.cpp"],
    shared_libs: [
        "libbase",
        "liblog",
        "libutils",
        "libbinder_ndk",
        "android.hardware.automotive.vehicle-V1-ndk",
    ],
    cflags: ["-Wall", "-Wextra", "-Werror"],
    init_rc: ["android.hardware.automotive.vehicle-service-default.rc"],
    vintf_fragments: ["android.hardware.automotive.vehicle-service-default.xml"],
}}
"""
        rc = f"""\
service vehicle_hal_default /vendor/bin/hw/{self.service_name}
    class hal
    user system
    group system
    oneshot
"""
        xml = """\
<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.automotive.vehicle</name>
        <version>1</version>
        <fqname>IVehicle/default</fqname>
    </hal>
</manifest>
"""
        self.writer.write(self.bp_path, bp.rstrip() + "\n")
        self.writer.write(self.rc_path, rc.rstrip() + "\n")
        self.writer.write(self.xml_path, xml.rstrip() + "\n")

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

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


def generate_vhal_service_build_glue(spec) -> str:
    spec_text = spec.to_llm_spec() if hasattr(spec, "to_llm_spec") else str(spec)
    return VHALServiceBuildAgent().run(spec_text)
