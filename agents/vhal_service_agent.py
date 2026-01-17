# FILE: agents/vhal_service_agent.py

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_root = "output"  # unified root
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.system = (
            "You are a senior Android Automotive Vehicle HAL engineer.\n"
            "Follow instructions exactly.\n"
            "Do not ask questions.\n"
            "Output must start with '--- FILE:' and contain only file blocks.\n"
            "No explanations.\n"
        )

    def build_prompt(self, spec_text: str) -> str:
        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
START YOUR RESPONSE WITH: --- FILE:

You are an AAOS Vehicle HAL C++ engineer.

Target:
- Android Automotive OS 14
- AIDL-based Vehicle HAL backend
- C++ NDK Binder

Requirements:
- Implement BnIVehicle
- Implement get() and set()
- Property storage
- Callback notification
- Thread-safe
- Register service:
  android.hardware.automotive.vehicle.IVehicle/default

Hard constraints:
- No placeholders, no TODOs
- No prose, no comments, no markdown, no code fences

Paths:
- Paths MUST start with: hardware/
- Example:
  hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp

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
            raise RuntimeError("[INTERNAL ERROR] Empty prompt constructed for VHAL service agent")

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
              "- Your previous output was INVALID because it contained no '--- FILE:' blocks.\n"
              "- You MUST output ONLY multi-file blocks.\n"
              "- The first non-empty line MUST start with: --- FILE:\n"
              "- Provide a minimal but compilable AAOS 14 AIDL VHAL C++ service skeleton.\n"
              "- Include necessary includes, binder registration, storage, callbacks, and thread-safety.\n"
              "- No prose, no comments, no markdown.\n"
        )

        result2 = call_llm(repair_prompt, system=self.system) or ""
        self._dump_raw(result2, attempt=2)

        written2 = self._write_files(result2)
        if written2 == 0:
            raise RuntimeError(
                "[FORMAT ERROR] No VHAL service files written after retry. "
                "Expected --- FILE: blocks. "
                f"See {self.raw_dir / 'VHAL_SERVICE_RAW_attempt1.txt'} and "
                f"{self.raw_dir / 'VHAL_SERVICE_RAW_attempt2.txt'}"
            )

        print(f"[DEBUG] {self.name}: wrote {written2} files (after retry)", flush=True)
        return result2

    # -----------------------------
    # Raw dump
    # -----------------------------
    def _dump_raw(self, text: str, attempt: int) -> None:
        p = self.raw_dir / f"VHAL_SERVICE_RAW_attempt{attempt}.txt"
        p.write_text(text or "", encoding="utf-8")

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

        # Remove one outer fenced block if present
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


def generate_vhal_service(spec):
    return VHALServiceAgent().run(spec.to_llm_spec())
