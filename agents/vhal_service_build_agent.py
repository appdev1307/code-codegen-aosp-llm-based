# FILE: agents/vhal_service_build_agent.py

import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.llm_file_parser import parse_files_json


class VHALServiceBuildAgent:
    def __init__(self):
        self.name = "VHAL Service Build Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are an expert Android Soong + VINTF engineer.\n"
            "Return STRICT JSON only.\n"
            "No prose.\n"
        )

        self.service_name = "android.hardware.automotive.vehicle-service-default"
        self.bp_path = "hardware/interfaces/automotive/vehicle/impl/Android.bp"
        self.rc_path = "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service-default.rc"
        self.xml_path = "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service-default.xml"

    def build_prompt(self, spec_text: str) -> str:
        return f"""
OUTPUT MUST BE STRICT JSON ONLY (NO MARKDOWN, NO PROSE).
Return exactly:
{{
  "files": [
    {{"path":"{self.bp_path}","content":"..."}},
    {{"path":"{self.rc_path}","content":"..."}},
    {{"path":"{self.xml_path}","content":"..."}}
  ]
}}

Constraints:
Android.bp:
- cc_binary name: "{self.service_name}"
- vendor: true
- relative_install_path: "hw"
- srcs: ["VehicleHalService.cpp"]
- shared_libs include: libbase, liblog, libutils, libbinder_ndk, android.hardware.automotive.vehicle-V1-ndk
- cflags include: -Wall -Wextra -Werror
- init_rc: ["android.hardware.automotive.vehicle-service-default.rc"]
- vintf_fragments: ["android.hardware.automotive.vehicle-service-default.xml"]

RC:
- service vehicle_hal_default /vendor/bin/hw/{self.service_name}
- class hal
- user system
- group system
- oneshot

VINTF:
- format="aidl"
- name android.hardware.automotive.vehicle
- version 1
- fqname IVehicle/default

Context (spec text; do not echo):
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)
        prompt = self.build_prompt(spec_text)

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0, response_format="json") or ""
        self._dump_raw(out1, 1)
        if self._write_json(out1):
            return out1

        out2 = call_llm(prompt + "\nREPAIR: Strict JSON only.", system=self.system, stream=False, temperature=0.0, response_format=None) or ""
        self._dump_raw(out2, 2)
        if self._write_json(out2):
            return out2

        print(f"[WARN] {self.name}: LLM did not produce valid JSON. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] Service build glue generated."

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
        self.writer.write(self.bp_path, bp)
        self.writer.write(self.rc_path, rc)
        self.writer.write(self.xml_path, xml)

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

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
