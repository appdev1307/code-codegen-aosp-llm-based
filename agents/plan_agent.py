# FILE: agents/plan_agent.py
from __future__ import annotations
import asyncio
import json
from typing import Any, Dict, List, Tuple
from llm_client import call_llm
from schemas.hal_spec import HalSpec
from tools.json_contract import parse_json_object
from pathlib import Path


_SYSTEM_PROMPT = (
    "Return STRICT JSON only.\n"
    "No prose. No markdown. No code fences.\n"
    'If you cannot comply, output exactly: {"properties": []}\n'
)

# Tighter chunks → smaller prompts per call → faster per-call processing.
# More chunks also means more parallel tasks, which is the whole point.
_CHUNK_SIZE = 30


def _build_header(spec: HalSpec) -> str:
    return (
        "You are an Android Automotive Vehicle HAL architect.\n"
        "Return ONLY a single JSON object. No markdown. No explanations.\n"
        "Schema:\n"
        '{\n'
        '  "domain": "HVAC",\n'
        '  "aosp_level": 14,\n'
        '  "vendor": "AOSP",\n'
        '  "callback_policy": "notify_on_change|notify_on_set",\n'
        '  "default_change_mode": "ON_CHANGE|CONTINUOUS|STATIC",\n'
        '  "properties": [\n'
        '    {\n'
        '      "id": "VSS_...",\n'
        '      "change_mode": "ON_CHANGE|CONTINUOUS|STATIC",\n'
        '      "default": null\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "Rules:\n"
        "- Enums must match exactly (case-sensitive).\n"
        "- Do not invent properties not in the input list.\n"
        "- If unsure: callback_policy=\"notify_on_change\", "
        "default_change_mode=\"ON_CHANGE\", default=null.\n"
        f"Context:\n"
        f"- domain={spec.domain}\n"
        f"- aosp_level={spec.aosp_level}\n"
        f"- vendor={spec.vendor}\n"
        "You will be given a chunk of properties as:\n"
        "ID|TYPE|ACCESS|AREAS\n"
        "Return JSON only."
    )


def _compact_properties(spec: HalSpec) -> List[str]:
    lines: List[str] = []
    for p in spec.properties:
        pid = (getattr(p, "id", "") or "").strip()
        typ = (getattr(p, "type", "") or "").strip()
        acc = (getattr(p, "access", "") or "").strip()
        areas = getattr(p, "areas", None) or []
        areas_s = (
            ",".join(str(a).upper() for a in areas)
            if isinstance(areas, list)
            else str(areas)
        )
        if pid:
            lines.append(f"{pid}|{typ}|{acc}|{areas_s}")
    return lines


# ---------------------------------------------------------------------------
# Per-chunk async worker
# ---------------------------------------------------------------------------
async def _process_chunk(header: str, chunk_lines: List[str]) -> Dict[str, Any]:
    """
    Sends one chunk to the LLM and returns the parsed JSON.
    Returns an empty dict on any failure — the merge step handles missing data
    gracefully via the normalization pass.
    """
    prompt = (
        f"{header}\n"
        f"PROPERTIES (CHUNK):\n"
        + "\n".join(chunk_lines)
        + "\nRETURN JSON NOW."
    )

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(
            None,
            lambda: call_llm(prompt, system=_SYSTEM_PROMPT, stream=False, temperature=0.0) or "",
        )
    except Exception as e:
        print(f"[Plan Agent] chunk LLM call failed: {e}", flush=True)
        return {}

    data, err = parse_json_object(raw)
    if err or not data:
        return {}
    return data


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------
class PlanAgent:
    """
    Phase 0 (LLM): Produce a strict JSON plan.
    Key rules:
    - Chunked calls, fired in parallel.
    - Robust JSON parsing.
    - MUST include every spec property id exactly once.
    """

    def __init__(self, output_root: str = "output"):
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.name = "HAL Plan Agent"

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[{self.name}] start", flush=True)
        return asyncio.run(self._run_async(spec))

    async def _run_async(self, spec: HalSpec) -> Dict[str, Any]:
        # Ordered list of property ids — used later to guarantee completeness
        spec_ids: List[str] = [
            x for x in
            ((getattr(p, "id", "") or "").strip() for p in spec.properties)
            if x
        ]

        lines = _compact_properties(spec)
        header = _build_header(spec)

        # Split into chunks and fire ALL in parallel
        chunks = [
            lines[i: i + _CHUNK_SIZE]
            for i in range(0, len(lines), _CHUNK_SIZE)
        ]
        print(f"[{self.name}] {len(lines)} properties → {len(chunks)} chunks (parallel)", flush=True)

        chunk_results: List[Dict[str, Any]] = await asyncio.gather(
            *(_process_chunk(header, chunk) for chunk in chunks)
        )

        # -------------------------------------------------------------------
        # Merge: collect domain defaults from whichever chunk returned them,
        # then flatten all property lists into one map.
        # -------------------------------------------------------------------
        callback_policy = "notify_on_change"
        default_change_mode = "ON_CHANGE"
        model_map: Dict[str, Dict[str, Any]] = {}

        for data in chunk_results:
            # Domain defaults — take the first non-null value we see.
            # Every chunk is asked for these; ordering doesn't matter.
            if not model_map:  # only bother checking until we have properties
                callback_policy = data.get("callback_policy") or callback_policy
                default_change_mode = data.get("default_change_mode") or default_change_mode

            # Collect properties — first occurrence wins (handles LLM dupes)
            for p in (data.get("properties") or []):
                if not isinstance(p, dict):
                    continue
                pid = (p.get("id") or "").strip()
                if pid and pid not in model_map:
                    model_map[pid] = p

        # -------------------------------------------------------------------
        # Normalize: force every spec id in, exactly once, in original order.
        # Missing or malformed LLM output falls back to safe defaults.
        # -------------------------------------------------------------------
        normalized: List[Dict[str, Any]] = []
        for pid in spec_ids:
            mp = model_map.get(pid) or {}
            normalized.append({
                "id": pid,
                "change_mode": mp.get("change_mode") or default_change_mode,
                "default": mp.get("default", None),
            })

        merged: Dict[str, Any] = {
            "domain": spec.domain,
            "aosp_level": int(spec.aosp_level),
            "vendor": spec.vendor,
            "callback_policy": callback_policy,
            "default_change_mode": default_change_mode,
            "properties": normalized,
        }

        # Save
        plan_path = self.output_root / "PLAN.json"
        plan_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        print(
            f"[{self.name}] done — {len(normalized)} properties in plan → {plan_path}",
            flush=True,
        )

        return merged