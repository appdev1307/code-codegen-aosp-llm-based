# FILE: agents/vhal_aidl_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are a senior Android Automotive OS engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output only multi-file blocks starting with '--- FILE:'.\n"
            "No explanations.\n"
        )

        # Canonical AOSP-style locations (matches your example)
        self.base = "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"

    def build_prompt(self, spec_text: str) -> str:
        # Few-shot example forces structure compliance for weaker models.
        example = f"""--- FILE: {self.base}/VehiclePropValue.aidl ---
package android.hardware.automotive.vehicle;
parcelable VehiclePropValue;
"""

        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
THE FIRST NON-EMPTY LINE MUST START WITH: --- FILE:

Glossary:
- VSS = Vehicle Signal Specification (signal tree), NOT "Vehicle Security System".

You are an Android Automotive OS architect.

Your task:
Generate AIDL definitions for Vehicle HAL based strictly on AOSP standards.

Rules:
- Package MUST be: android.hardware.automotive.vehicle
- AIDL only (Android 12+), NO HIDL
- IVehicle MUST declare EXACTLY:
    VehiclePropValue get(int propId, int areaId);
    void set(in VehiclePropValue value);
- IVehicleCallback MUST exist
- VehiclePropValue MUST be declared as parcelable
- Use correct AIDL syntax
- No placeholders
- No explanations
- No comments
- No markdown fences

Paths:
- Paths MUST start with: hardware/
- Example:
  {self.base}/IVehicle.aidl

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Example (format demonstration only; do not repeat verbatim):
{example}

Specification:
{spec_text}

Now output the AIDL files.
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)

        # Attempt 1
        result1 = call_llm(prompt, system=self.system) or ""
        self._dump_raw(result1, attempt=1)
        written1 = self._write_files(result1)
        if written1 > 0:
            print(f"[DEBUG] {self.name}: wrote {written1} files", flush=True)
            return result1

        # Attempt 2 (repair)
        repair_prompt = (
            prompt
            + "\n\nREPAIR INSTRUCTIONS (MANDATORY):\n"
              "- Your previous output was INVALID because it contained no '--- FILE:' blocks.\n"
              "- Output ONLY file blocks.\n"
              "- The first non-empty line MUST start with: --- FILE:\n"
              "- Include at minimum:\n"
              "  1) IVehicle.aidl\n"
              "  2) IVehicleCallback.aidl\n"
              "  3) VehiclePropValue.aidl\n"
              "- No prose.\n"
        )
        result2 = call_llm(repair_prompt, system=self.system) or ""
        self._dump_raw(result2, attempt=2)
        written2 = self._write_files(result2)
        if written2 > 0:
            print(f"[DEBUG] {self.name}: wrote {written2} files (after retry)", flush=True)
            return result2

        # Deterministic fallback (unblocks pipeline)
        print(f"[WARN] {self.name}: LLM did not produce file blocks. Using deterministic fallback.", flush=True)
        fallback_written = self._write_fallback_aidl()
        if fallback_written == 0:
            raise RuntimeError(
                "[FORMAT ERROR] No AIDL files written after retry, and fallback failed. "
                f"See {self.raw_dir / 'VHAL_AIDL_RAW_attempt1.txt'} and {self.raw_dir / 'VHAL_AIDL_RAW_attempt2.txt'}"
            )

        return "[FALLBACK] Deterministic AIDL files generated."

    # -----------------------------
    # Raw dump
    # -----------------------------
    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"VHAL_AIDL_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")

    # -----------------------------
    # Parsing + writing
    # -----------------------------
    def _write_files(self, text: str) -> int:
        if not text or not text.strip():
            return 0

        normalized = self._strip_outer_code_fences(text)
        blocks = self._parse_file_blocks(normalized)
        if not blocks:
            return 0

        count = 0
        for rel_path, content in blocks:
            safe_path = self._sanitize_rel_path(rel_path)
            if safe_path is None:
                continue
            self.writer.write(safe_path, content)
            count += 1
        return count

    def _strip_outer_code_fences(self, text: str) -> str:
        t = text.replace("\r\n", "\n").strip()
        if t.startswith("```"):
            t = re.sub(r"(?m)^```[^\n]*\n", "", t, count=1)
            t = re.sub(r"(?m)\n```$", "", t, count=1)
        return t.strip() + "\n"

    def _parse_file_blocks(self, text: str) -> List[Tuple[str, str]]:
        pattern = re.compile(
            r"(?ms)^\s*---\s*FILE:\s*(?P<path>[^-\n]+?)\s*---\s*\n(?P<body>.*?)(?=^\s*---\s*FILE:\s*|\Z)"
        )
        blocks: List[Tuple[str, str]] = []
        for m in pattern.finditer(text):
            rel_path = (m.group("path") or "").strip()
            body = (m.group("body") or "").rstrip() + "\n"
            if rel_path and body.strip():
                blocks.append((rel_path, body))
        return blocks

    def _sanitize_rel_path(self, rel_path: str) -> Optional[str]:
        p = (rel_path or "").strip().replace("\\", "/")
        p = re.sub(r"/+", "/", p)

        if not p:
            return None
        if p.startswith("/"):
            return None
        if ".." in p.split("/"):
            return None
        if not p.startswith("hardware/"):
            return None
        return p

    # -----------------------------
    # Deterministic fallback
    # -----------------------------
    def _write_fallback_aidl(self) -> int:
        """
        Minimal AIDL set satisfying your hard requirements.
        This is intentionally small but syntactically valid.
        """
        files = {
            f"{self.base}/VehiclePropValue.aidl": """\
package android.hardware.automotive.vehicle;
parcelable VehiclePropValue;
""",
            f"{self.base}/IVehicleCallback.aidl": """\
package android.hardware.automotive.vehicle;

interface IVehicleCallback {
    // Minimal callback surface (expand later as needed)
    void onPropertyEvent(in VehiclePropValue value);
    void onPropertySetError(int errorCode, int propId, int areaId);
}
""",
            f"{self.base}/IVehicle.aidl": """\
package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehiclePropValue;
import android.hardware.automotive.vehicle.IVehicleCallback;

interface IVehicle {
    VehiclePropValue get(int propId, int areaId);
    void set(in VehiclePropValue value);

    // Minimal subscription APIs (optional but common); safe to remove if you do not want them.
    void registerCallback(in IVehicleCallback callback);
    void unregisterCallback(in IVehicleCallback callback);
}
""",
        }

        count = 0
        for rel_path, content in files.items():
            safe_path = self._sanitize_rel_path(rel_path)
            if safe_path is None:
                continue
            self.writer.write(safe_path, content.strip() + "\n")
            count += 1
        return count


def generate_vhal_aidl(spec):
    return VHALAidlAgent().run(spec.to_llm_spec())
