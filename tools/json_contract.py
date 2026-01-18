# FILE: tools/json_contract.py

import json
from typing import Any, Dict, Optional, Tuple


def extract_first_json_object(text: str) -> Optional[str]:
    """
    Extract the first top-level JSON object from arbitrary text.
    Handles leading prose, code fences, and trailing junk.

    Strategy:
    - Find first '{'
    - Scan forward counting braces while respecting string literals
    - Return substring that forms a balanced JSON object
    """
    if not text:
        return None

    s = text.strip()

    # Quick remove common code fences without trying to be perfect
    s = s.replace("```json", "").replace("```", "").strip()

    start = s.find("{")
    if start < 0:
        return None

    in_str = False
    esc = False
    depth = 0

    for i in range(start, len(s)):
        ch = s[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        # not in string
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]

    return None


def parse_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Return (parsed_obj, error_message). Only accepts a JSON object at top level.
    """
    js = extract_first_json_object(text or "")
    if not js:
        return None, "No JSON object found in output."
    try:
        data = json.loads(js)
    except Exception as e:
        return None, f"JSON parse failed: {e}"
    if not isinstance(data, dict):
        return None, "Top-level JSON must be an object."
    return data, None
