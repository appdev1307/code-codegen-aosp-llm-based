# FILE: agents/vhal_aidl_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlAgent:
    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_root = "output"  # unified root
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are a senior Android Automotive OS engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output must start with '--- FILE:' and contain only file blocks.\n"
            "No explanations.\n"
        )

    def build_prompt(self, spec_text: str) -> str:
        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
START YOUR RESPONSE WITH: --- FILE:

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

Paths:
- Paths MUST start with: hardware/
- Example:
  hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicle.aidl

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Specification:
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)
        if not isinstance(prompt, str) or not prompt.strip():
            raise RuntimeError("[INTERNAL ERROR] Empty prompt constructed for VHAL AIDL agent")

        # Attempt 1
        result = call_llm(prompt, system=self.system) or ""
        self._dump_raw(result, attempt=1)

        written = self._write_files(result)
        if written > 0:
            print(f"[DEBUG] {self.name}: wrote {written} files", flush=True)
            return result

        # Attempt 2 (repair)
        repair_prompt = (
            prompt
            + "\n\nREPAIR INSTRUCTIONS (MANDATORY):\n"
              "- Your previous output was INVALID.\n"
              "- You MUST output ONLY multi-file blocks.\n"
              "- The very first non-empty line MUST start with: --- FILE:\n"
              "- Include at least these files:\n"
              "  1) IVehicle.aidl\n"
              "  2) IVehicleCallback.aidl\n"
              "  3) VehiclePropValue.aidl (parcelable)\n"
              "- No prose, no comments, no markdown, no code fences.\n"
        )

        result2 = call_llm(repair_prompt, system=self.system) or ""
        self._dump_raw(result2, attempt=2)

        written2 = self._write_files(result2)
        if written2 == 0:
            raise RuntimeError(
                "[FORMAT ERROR] No AIDL files written after retry. "
                "Expected --- FILE: blocks. "
                f"See {self.raw_dir / 'VHAL_AIDL_RAW_attempt1.txt'} and "
                f"{self.raw_dir / 'VHAL_AIDL_RAW_attempt2.txt'}"
            )

        print(f"[DEBUG] {self.name}: wrote {written2} files (after retry)", flush=True)
        return result2

    # -----------------------------
    # Raw dump
    # -----------------------------
    def _dump_raw(self, text: str, attempt: int) -> None:
        p = self.raw_dir / f"VHAL_AIDL_RAW_attempt{attempt}.txt"
        p.write_text(text or "", encoding="utf-8")

    # -----------------------------
    # Parsing + writing
    # -----------------------------
    def _write_files(self, text: str) -> int:
        """
        Parse multi-file blocks and write them using SafeWriter.

        Accepts:
          --- FILE: <path> ---
          <content>

        Hardened against:
        - leading/trailing whitespace
        - markdown fences
        - path traversal
        """
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
                # Skip unsafe paths rather than writing them
                continue

            self.writer.write(safe_path, content)
            count += 1

        return count

    def _strip_outer_code_fences(self, text: str) -> str:
        """
        If the model wrapped everything in ``` fences, remove them.
        """
        t = text.replace("\r\n", "\n").strip()

        # Remove a single outer fenced block if present
        if t.startswith("```"):
            # remove first fence line
            t = re.sub(r"(?m)^```[^\n]*\n", "", t, count=1)
            # remove last fence line
            t = re.sub(r"(?m)\n```$", "", t, count=1)

        return t.strip() + "\n"

    def _parse_file_blocks(self, text: str) -> List[Tuple[str, str]]:
        """
        Robust parser for:
          --- FILE: path ---
          content...
        """
        # Require FILE headers at line start (after optional whitespace)
        pattern = re.compile(
            r"(?ms)^\s*---\s*FILE:\s*(?P<path>[^-\n]+?)\s*---\s*\n(?P<body>.*?)(?=^\s*---\s*FILE:\s*|\Z)"
        )

        blocks: List[Tuple[str, str]] = []
        for m in pattern.finditer(text):
            rel_path = (m.group("path") or "").strip()
            body = m.group("body") or ""
            body = body.rstrip() + "\n"

            if rel_path and body.strip():
                blocks.append((rel_path, body))

        return blocks

    def _sanitize_rel_path(self, rel_path: str) -> Optional[str]:
        """
        Enforce:
        - must start with hardware/
        - must not be absolute
        - must not contain .. traversal
        - normalize slashes
        """
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


def generate_vhal_aidl(spec):
    return VHALAidlAgent().run(spec.to_llm_spec())
