import re
from pathlib import Path
from typing import Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceAgent:
    def __init__(self, output_dir: str = "output/vhal_service"):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = output_dir
        self.writer = SafeWriter(self.output_dir)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an AAOS Vehicle HAL C++ engineer.

Target:
- Android Automotive OS
- AIDL-based Vehicle HAL backend
- C++ NDK Binder
- Android 12+

Requirements:
- Implement BnIVehicle
- Implement get() and set()
- Implement property storage
- Implement callback notification
- Thread-safe
- Register service as:
  android.hardware.automotive.vehicle.IVehicle/default

Rules:
- No placeholders.
- No explanations.

IMPORTANT:
- ALL file paths MUST be RELATIVE
- DO NOT generate absolute paths (/system, /vendor, /etc, /)
- DO NOT use path traversal (..)
- Use AOSP-style relative paths only

Output format:
--- FILE: <relative path> ---
<file content>

Specification:
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)
        result = call_llm(prompt)
        if not result or not result.strip():
            raise RuntimeError("[LLM ERROR] Empty VHAL service output")

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        written = self._write_files(result)

        if written == 0:
            # Fail fast: if the LLM didn't follow the required file format,
            # don't silently do nothing.
            raise RuntimeError(
                "[FORMAT ERROR] No files found in LLM output. "
                "Expected blocks like: --- FILE: <relative path> ---"
            )

        print(f"[DEBUG] {self.name}: output -> {self.output_dir} (files={written})", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str) -> int:
        """
        Parse LLM output of the form:
          --- FILE: path ---
          <content>
          --- FILE: other/path ---
          <content>
        and write them under output_dir.
        """
        file_count = 0
        current_path: Optional[str] = None
        buf: list[str] = []

        for line in text.splitlines():
            m = re.match(r"^\s*---\s*FILE:\s*(.+?)\s*---\s*$", line)
            if m:
                if current_path is not None:
                    self._flush(current_path, buf)
                    file_count += 1
                current_path = m.group(1).strip()
                buf = []
            else:
                buf.append(line)

        if current_path is not None:
            self._flush(current_path, buf)
            file_count += 1

        return file_count

    def _flush(self, rel_path: str, buf: list[str]) -> None:
        # Keep your existing sanitizer (already correct)
        safe_rel = self._sanitize_rel_path(rel_path)

        # Write using shared SafeWriter (central enforcement)
        content = "\n".join(buf).rstrip() + "\n"
        self.writer.write(safe_rel, content)

    @staticmethod
    def _sanitize_rel_path(rel_path: str) -> str:
        """
        Prevent path traversal and normalize separators.
        - Disallow absolute paths
        - Disallow '..' segments
        - Strip empty or '.' segments
        """
        p = Path(rel_path.replace("\\", "/"))
        if p.is_absolute():
            raise ValueError(f"Unsafe FILE path (absolute): {rel_path}")

        parts = []
        for part in p.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError(f"Unsafe FILE path (path traversal): {rel_path}")
            parts.append(part)

        if not parts:
            raise ValueError(f"Unsafe FILE path (empty): {rel_path}")

        return str(Path(*parts))


def generate_vhal_service(spec) -> str:
    # spec is expected to implement to_llm_spec()
    return VHALServiceAgent().run(spec.to_llm_spec())
