# FILE: agents/vhal_service_build_agent.py

import json
import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceBuildAgent:
    def __init__(self):
        self.name = "VHAL Service Build Agent"
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

        self.base = "hardware/interfaces/automotive/vehicle/impl"
        self.bp_path = f"{self.base}/Android.bp"
        self.rc_path = f"{self.base}/android.hardware.automotive.vehicle-service-default.rc"
        self.xml_path = f"{self.base}/android.hardware.automotive.vehicle-service-default.xml"

        self.required_files = [self.bp_path, self.rc_path, self.xml_path]

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
- TARGET IS AOSP Soong + init rc + VINTF fragment for AAOS VHAL default service.

YOU MUST GENERATE EXACTLY THESE FILES:
- {self.bp_path}
- {self.rc_path}
- {self.xml_path}

Android.bp REQUIREMENTS:
- cc_binary name: "android.hardware.automotive.vehicle-service-default"
- vendor: true
- relative_install_path: "hw"
- srcs: ["VehicleHalService.cpp"]
- shared_libs include at least:
  "libbase", "liblog", "libutils", "libbinder_ndk", "android.hardware.automotive.vehicle-V1-ndk"
- init_rc: ["android.hardware.automotive.vehicle-service-default.rc"]
- vintf_fragments: ["android.hardware.automotive.vehicle-service-default.xml"]
- cflags: ["-Wall", "-Wextra", "-Werror"]

RC REQUIREMENTS:
- service name: android.hardware.automotive.vehicle-service-default
- exec: /vendor/bin/hw/android.hardware.automotive.vehicle-service-default
- class: hal
- user: system
- group: system

VINTF XML REQUIREMENTS:
- <manifest version="1.0" type="device">
- hal name="android.hardware.automotive.vehicle"
- transport: hwbinder
- interface name="IVehicle"
- instance "default"

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
              "- Ensure all required files exist with exact paths.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        if self._write_json_files(out2):
            return out2

        print(f"[WARN] {self.name}: LLM did not produce valid JSON. Using deterministic fallback.", flush=True)
        self._write_fallback()
        return "[FALLBACK] Deterministic service build glue generated."

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_SERVICE_BP_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

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

        required = set(self.required_files)
        seen = set()

        wrote = 0
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = f.get("content")
            if path not in required or not isinstance(content, str) or not content.strip():
                continue
            safe = self._sanitize(path)
            if not safe:
                continue
            if not content.endswith("\n"):
                content += "\n"
            self.writer.write(safe, content)
            wrote += 1
            seen.add(path)

        return wrote == len(self.required_files) and seen == required

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip()
        p = re.sub(r"/+", "/", p)
        if p.startswith("/") or ".." in p.split("/"):
            return None
        if p not in self.required_files:
            return None
        return p

    def _write_fallback(self) -> None:
        bp = """cc_binary {
    name: "android.hardware.automotive.vehicle-service-default",
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
}
"""
        rc = """service android.hardware.automotive.vehicle-service-default /vendor/bin/hw/android.hardware.automotive.vehicle-service-default
    class hal
    user system
    group system
"""
        xml = """<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.automotive.vehicle</name>
        <transport>hwbinder</transport>
        <interface>
            <name>IVehicle</name>
            <instance>default</instance>
        </interface>
    </hal>
</manifest>
"""
        self.writer.write(self.bp_path, bp.rstrip() + "\n")
        self.writer.write(self.rc_path, rc.rstrip() + "\n")
        self.writer.write(self.xml_path, xml.rstrip() + "\n")


def generate_vhal_service_build_glue():
    return VHALServiceBuildAgent().run()
