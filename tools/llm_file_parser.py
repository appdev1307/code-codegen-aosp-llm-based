# FILE: tools/llm_file_parser.py

import json
import re
from typing import List, Optional, Tuple


def parse_files_json(text: str) -> List[Tuple[str, str]]:
    """
    Parse strict JSON:
    {
      "files": [
        {"path": "hardware/...", "content": "..."},
        ...
      ]
    }
    """
    t = (text or "").strip()
    data = json.loads(t)

    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Missing or empty 'files' array")

    out: List[Tuple[str, str]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        path = (f.get("path") or "").strip()
        content = f.get("content")
        if not path or not isinstance(content, str):
            continue
        if not content.endswith("\n"):
            content += "\n"
        out.append((path, content))

    if not out:
        raise ValueError("No valid files in JSON")
    return out


def strip_outer_code_fences(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if t.startswith("```"):
        t = re.sub(r"(?m)^```[^\n]*\n", "", t, count=1)
        t = re.sub(r"(?m)\n```$", "", t, count=1)
    return t.strip() + "\n"


def parse_file_blocks(text: str) -> List[Tuple[str, str]]:
    """
    Backward-compatible parser for:
      --- FILE: path ---
      <content>
    """
    normalized = strip_outer_code_fences(text)
    pattern = re.compile(
        r"(?ms)^\s*---\s*FILE:\s*(?P<path>[^-\n]+?)\s*---\s*\n(?P<body>.*?)(?=^\s*---\s*FILE:\s*|\Z)"
    )
    out: List[Tuple[str, str]] = []
    for m in pattern.finditer(normalized):
        p = (m.group("path") or "").strip()
        b = (m.group("body") or "").rstrip() + "\n"
        if p and b.strip():
            out.append((p, b))
    return out
