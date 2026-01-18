# FILE: tools/json_contract.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def parse_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Best-effort extraction of the first JSON object from a model response.
    Returns: (data, error_message)
    - If strict json.loads succeeds => ok
    - Else scan for first '{' and decode using JSONDecoder.raw_decode
    """
    t = (text or "").strip()
    if not t:
        return None, "empty_response"

    # Fast path
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data, None
        return None, "top_level_json_must_be_object"
    except Exception:
        pass

    # Recovery path: find first object
    dec = json.JSONDecoder()
    first = t.find("{")
    if first < 0:
        return None, "no_json_object_found"

    try:
        obj, end = dec.raw_decode(t[first:])
        if isinstance(obj, dict):
            return obj, None
        return None, "top_level_json_must_be_object"
    except Exception as e:
        return None, f"json_parse_failed: {e}"
