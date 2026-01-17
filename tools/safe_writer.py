# FILE: tools/safe_writer.py

import os
from typing import Optional


class SafeWriter:
    """
    Enforces:
    - rel_path is relative (no absolute, no drive letters)
    - no traversal segments ("..")
    - normalized separators
    - all writes remain inside output_root
    """

    def __init__(self, output_root: str):
        self.output_root = os.path.abspath(output_root)
        os.makedirs(self.output_root, exist_ok=True)

    def write(self, rel_path: str, content: str) -> str:
        safe_rel = self._sanitize_rel_path(rel_path)

        abs_path = os.path.abspath(os.path.join(self.output_root, safe_rel))

        # Final containment check (defense in depth)
        if not (abs_path == self.output_root or abs_path.startswith(self.output_root + os.sep)):
            raise ValueError(f"[SAFE WRITER] Path escape detected: {abs_path}")

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

        return abs_path

    def _sanitize_rel_path(self, rel_path: str) -> str:
        if not isinstance(rel_path, str) or not rel_path.strip():
            raise ValueError("[SAFE WRITER] Empty path")

        p = rel_path.strip()

        # Normalize slashes early (LLMs often emit backslashes)
        p = p.replace("\\", "/")

        # Reject absolute paths and home shortcuts
        if p.startswith("/") or p.startswith("~/"):
            raise ValueError(f"[SAFE WRITER] Absolute path not allowed: {rel_path}")

        # Reject Windows drive letters (e.g., C:\)
        if len(p) >= 2 and p[1] == ":":
            raise ValueError(f"[SAFE WRITER] Drive path not allowed: {rel_path}")

        # Collapse repeated separators
        while "//" in p:
            p = p.replace("//", "/")

        # Reject traversal via path segments
        parts = [seg for seg in p.split("/") if seg not in ("", ".")]
        if any(seg == ".." for seg in parts):
            raise ValueError(f"[SAFE WRITER] Path traversal not allowed: {rel_path}")

        safe_rel = "/".join(parts)

        # Basic sanity: prevent writing to directory only
        if safe_rel.endswith("/"):
            raise ValueError(f"[SAFE WRITER] Path points to directory: {rel_path}")

        return safe_rel
