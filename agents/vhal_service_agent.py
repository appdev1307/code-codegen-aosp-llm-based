# agents/vhal_service_agent.py
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from llm_client import call_llm


FILE_HEADER_RE_LIST = [
    # --- FILE: path ---
    re.compile(r"^\s*---\s*FILE\s*:\s*(?P<path>.+?)\s*---\s*$", re.IGNORECASE),
    # --- FILE path ---
    re.compile(r"^\s*---\s*FILE\s+(?P<path>.+?)\s*---\s*$", re.IGNORECASE),
    # FILE: path
    re.compile(r"^\s*FILE\s*:\s*(?P<path>.+?)\s*$", re.IGNORECASE),
]


CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")
CODE_FENCE_END_RE = re.compile(r"^\s*```\s*$")


@dataclass
class ParsedFile:
    rel_path: str
    content: str


class VHALServiceAgent:
    def __init__(self, output_dir: str = "output/vhal_service"):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = output_dir

    # -----------------------------
    # Prompting
    # -----------------------------
    def build_prompt(self, spec_text: str) -> str:
        # Make the formatting rules extremely explicit.
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

ABSOLUTE OUTPUT RULES (MANDATORY):
- Output ONLY file blocks.
- Each file MUST be wrapped exactly like:

--- FILE: <relative path> ---
<file content>

- Do NOT use markdown. Do NOT use ``` fences.
- Do NOT add explanations, headings, or prose.
- Use forward slashes in paths.
- Provide all required includes and namespaces. No placeholders.

Specification:
{spec_text}
""".lstrip()

    def build_reformat_prompt(self, bad_output: str) -> str:
        # Second attempt: do NOT change code, only wrap into correct blocks.
        return f"""
Reformat the following content into STRICT file blocks.

RULES:
- Output ONLY blocks in this exact structure:

--- FILE: <relative path> ---
<file content>

- No markdown, no ``` fences, no commentary.
- Do NOT modify code content except adding missing file headers.
- If multiple files are present, split them into separate blocks.
- If it is clearly one file, choose a reasonable path under:
  hardware/interfaces/automotive/vehicle/impl/

CONTENT TO REFORMAT:
{bad_output}
""".lstrip()

    # -----------------------------
    # Parsing + writing
    # -----------------------------
    def _strip_code_fences(self, text: str) -> str:
        # If model mistakenly used ``` fences, remove them safely.
        lines = text.splitlines()
        out: List[str] = []
        in_fence = False
        for line in lines:
            if CODE_FENCE_RE.match(line) and not in_fence:
                in_fence = True
                continue
            if CODE_FENCE_END_RE.match(line) and in_fence:
                in_fence = False
                continue
            out.append(line)
        return "\n".join(out)

    def _match_header(self, line: str) -> Optional[str]:
        for rx in FILE_HEADER_RE_LIST:
            m = rx.match(line)
            if m:
                return m.group("path").strip()
        return None

    def parse_llm_files(self, llm_text: str) -> List[ParsedFile]:
        if not llm_text or not llm_text.strip():
            return []

        text = self._strip_code_fences(llm_text).strip()
        lines = text.splitlines()

        files: List[ParsedFile] = []
        current_path: Optional[str] = None
        current_buf: List[str] = []

        def flush():
            nonlocal current_path, current_buf
            if current_path is None:
                return
            content = "\n".join(current_buf).rstrip() + "\n"
            files.append(ParsedFile(rel_path=current_path, content=content))
            current_path = None
            current_buf = []

        for line in lines:
            maybe_path = self._match_header(line)
            if maybe_path is not None:
                # new file header encountered
                flush()
                current_path = maybe_path
                current_buf = []
            else:
                if current_path is not None:
                    current_buf.append(line)

        flush()

        # Filter out empty-path or empty-content blocks (but keep small files)
        cleaned: List[ParsedFile] = []
        for f in files:
            p = f.rel_path.strip().lstrip("./")
            if not p:
                continue
            cleaned.append(ParsedFile(rel_path=p, content=f.content))

        return cleaned

    def _safe_join_output(self, rel_path: str) -> Path:
        # Prevent path traversal
        rel_path = rel_path.replace("\\", "/").lstrip("/")
        out_root = Path(self.output_dir).resolve()
        full = (out_root / rel_path).resolve()
        if out_root not in full.parents and full != out_root:
            raise RuntimeError(f"[SECURITY] Refusing to write outside output_dir: {rel_path}")
        return full

    def write_files(self, parsed_files: List[ParsedFile]) -> List[Path]:
        os.makedirs(self.output_dir, exist_ok=True)
        written: List[Path] = []
        for f in parsed_files:
            full_path = self._safe_join_output(f.rel_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f.content, encoding="utf-8")
            written.append(full_path)
        return written

    def dump_raw(self, text: str, name: str = "vhal_service_raw.txt") -> Path:
        os.makedirs(self.output_dir, exist_ok=True)
        p = Path(self.output_dir) / name
        p.write_text(text or "", encoding="utf-8")
        return p

    # -----------------------------
    # Main entry
    # -----------------------------
    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec_text)
        result1 = call_llm(prompt) or ""
        self.dump_raw(result1, "vhal_service_llm_raw_attempt1.txt")

        files = self.parse_llm_files(result1)
        if not files:
            # Auto-retry: ask model to ONLY reformat into file blocks
            print(f"[WARN] {self.name}: no file blocks found, retrying with reformat prompt", flush=True)
            prompt2 = self.build_reformat_prompt(result1.strip() or "(empty)")
            result2 = call_llm(prompt2) or ""
            self.dump_raw(result2, "vhal_service_llm_raw_attempt2.txt")
            files = self.parse_llm_files(result2)

        if not files:
            raw_path = Path(self.output_dir) / "vhal_service_llm_raw_attempt2.txt"
            raise RuntimeError(
                "[FORMAT ERROR] No files found in LLM output.\n"
                "Expected blocks like: --- FILE: <relative path> ---\n"
                f"Raw outputs dumped to:\n"
                f"- {Path(self.output_dir) / 'vhal_service_llm_raw_attempt1.txt'}\n"
                f"- {raw_path}\n"
                "Tip: Open the raw file to see what the model returned."
            )

        written = self.write_files(files)
        print(f"[DEBUG] {self.name}: wrote {len(written)} files into {self.output_dir}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)

        return "\n".join(str(p) for p in written)


# Backward-compatible helper if your architect imports generate_vhal_service(...)
def generate_vhal_service(spec_text: str) -> str:
    return VHALServiceAgent().run(spec_text)
