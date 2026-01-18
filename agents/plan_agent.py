# FILE: agents/plan_agent.py

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_client import call_llm
from schemas.hal_spec import HalSpec


VALID_CALLBACK_POLICY = {"notify_on_change", "notify_on_set"}
VALID_CHANGE_MODE = {"ON_CHANGE", "CONTINUOUS", "STATIC"}


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp_enum(v: Any, allowed: set, default: str) -> str:
    if isinstance(v, str) and v in allowed:
        return v
    return default


def _norm_prop_id(pid: Any) -> Optional[str]:
    if not isinstance(pid, str):
        return None
    s = pid.strip()
    if not s:
        return None
    # keep conservative; your ids are VSS_...
    return s


def _split_chunks(items: List[str], chunk_size: int) -> List[List[str]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


class PlanAgent:
    """
    Stage-0 Plan (LLM):
    - Produce a small strict JSON plan derived from the YAML spec.
    - Use chunking to handle large property lists reliably.
    - Output is deterministic-normalized to prevent cascading failures.

    Output schema:
    {
      "domain": "HVAC",
      "aosp_level": 14,
      "vendor": "AOSP",
      "callback_policy": "notify_on_change|notify_on_set",
      "default_change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
      "properties": [
        {"id": "VSS_...", "change_mode": "...", "default": null}
      ]
    }
    """

    def __init__(self):
        self.name = "HAL Plan Agent"

        # Store raw LLM outputs for debugging
        self.debug_dir = Path("output/.llm_draft/latest")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        # Header prompt is small; property prompts are chunked
        self.system_header = (
            "You are an Android Automotive Vehicle HAL architect.\n"
            "Return STRICT JSON only. No prose, no markdown, no code fences.\n"
            "If you cannot comply, return exactly: {\"ok\": false}\n"
        )
        self.system_props = (
            "You are an Android Automotive Vehicle HAL architect.\n"
            "Return STRICT JSON only. No prose, no markdown, no code fences.\n"
            "If you cannot comply, return exactly: {\"items\": []}\n"
        )

    def _extract_property_ids(self, spec: HalSpec) -> List[str]:
        ids: List[str] = []
        props = getattr(spec, "properties", None) or []
        for p in props:
            # p may be dataclass-like or dict-like
            pid = getattr(p, "id", None)
            if pid is None and isinstance(p, dict):
                pid = p.get("id")
            pid = _norm_prop_id(pid)
            if pid:
                ids.append(pid)
        # stable dedupe while preserving order
        seen = set()
        out = []
        for x in ids:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _build_header_prompt(self, spec: HalSpec) -> str:
        # Keep input small to reduce failure rate.
        # We do NOT include full property list here.
        domain = getattr(spec, "domain", "HVAC")
        aosp_level = getattr(spec, "aosp_level", 14)
        vendor = getattr(spec, "vendor", "AOSP")

        return f"""
Return ONLY one JSON object.

Schema:
{{
  "domain": "{domain}",
  "aosp_level": {int(aosp_level)},
  "vendor": "{vendor}",
  "callback_policy": "notify_on_change|notify_on_set",
  "default_change_mode": "ON_CHANGE|CONTINUOUS|STATIC"
}}

Rules:
- Enums must match exactly (case-sensitive).
- If unsure: callback_policy="notify_on_change", default_change_mode="ON_CHANGE".

Input (minimal):
- domain: {domain}
- aosp_level: {int(aosp_level)}
- vendor: {vendor}

RETURN JSON NOW.
""".strip()

    def _build_props_prompt(self, spec: HalSpec, header: Dict[str, Any], prop_ids: List[str]) -> str:
        # Provide minimal per-property context to the LLM (id + type/access/areas if available)
        # but do it compactly to avoid token blowups.

        # Build a compact lookup table: id -> (type, access, areas)
        lookup: Dict[str, Dict[str, Any]] = {}
        props = getattr(spec, "properties", None) or []
        for p in props:
            pid = getattr(p, "id", None)
            if pid is None and isinstance(p, dict):
                pid = p.get("id")
            pid = _norm_prop_id(pid)
            if not pid or pid not in prop_ids:
                continue

            ptype = getattr(p, "type", None) if not isinstance(p, dict) else p.get("type")
            access = getattr(p, "access", None) if not isinstance(p, dict) else p.get("access")
            areas = getattr(p, "areas", None) if not isinstance(p, dict) else p.get("areas")

            lookup[pid] = {
                "type": str(ptype) if ptype is not None else None,
                "access": str(access) if access is not None else None,
                "areas": list(areas) if isinstance(areas, list) else None,
            }

        # Render compact “bullet lines” (one line per property)
        lines = []
        for pid in prop_ids:
            meta = lookup.get(pid) or {}
            t = meta.get("type") or "UNKNOWN"
            a = meta.get("access") or "UNKNOWN"
            ar = meta.get("areas") or []
            ar_s = ",".join([str(x) for x in ar]) if ar else "UNKNOWN"
            lines.append(f"- {pid} | type={t} | access={a} | areas={ar_s}")
        props_text = "\n".join(lines)

        default_change = header.get("default_change_mode", "ON_CHANGE")
        callback_policy = header.get("callback_policy", "notify_on_change")

        return f"""
Return ONLY JSON.

Schema:
{{
  "items": [
    {{
      "id": "VSS_...",
      "change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
      "default": null
    }}
  ]
}}

Rules:
- You MUST return one item for EACH input id.
- Do NOT invent ids not provided.
- If unsure: change_mode="{default_change}", default=null.
- Keep defaults conservative:
  - BOOLEAN: default=false if you are confident; else null
  - INT/FLOAT: default=0 or 0.0 only if you are confident; else null
- callback_policy for service: "{callback_policy}" (informational)

Input ids (with minimal context):
{props_text}

RETURN JSON NOW.
""".strip()

    def _dump_raw(self, name: str, text: str) -> None:
        (self.debug_dir / name).write_text(text or "", encoding="utf-8")

    def _parse_header(self, raw: str, spec: HalSpec) -> Dict[str, Any]:
        domain = getattr(spec, "domain", "HVAC")
        aosp_level = getattr(spec, "aosp_level", 14)
        vendor = getattr(spec, "vendor", "AOSP")

        # defaults
        out = {
            "domain": domain,
            "aosp_level": int(aosp_level),
            "vendor": vendor,
            "callback_policy": "notify_on_change",
            "default_change_mode": "ON_CHANGE",
        }

        try:
            data = json.loads((raw or "").strip())
            if not isinstance(data, dict):
                return out
        except Exception:
            return out

        out["domain"] = data.get("domain") or out["domain"]
        out["aosp_level"] = _safe_int(data.get("aosp_level"), out["aosp_level"])
        out["vendor"] = data.get("vendor") or out["vendor"]
        out["callback_policy"] = _clamp_enum(data.get("callback_policy"), VALID_CALLBACK_POLICY, out["callback_policy"])
        out["default_change_mode"] = _clamp_enum(
            data.get("default_change_mode"), VALID_CHANGE_MODE, out["default_change_mode"]
        )
        return out

    def _parse_props_items(
        self, raw: str, requested_ids: List[str], default_change_mode: str
    ) -> List[Dict[str, Any]]:
        # Build output for each requested id; ensure 1:1 coverage.
        result_map: Dict[str, Dict[str, Any]] = {}
        for pid in requested_ids:
            result_map[pid] = {"id": pid, "change_mode": default_change_mode, "default": None}

        try:
            data = json.loads((raw or "").strip())
            if not isinstance(data, dict):
                return list(result_map.values())
            items = data.get("items")
            if not isinstance(items, list):
                return list(result_map.values())
        except Exception:
            return list(result_map.values())

        for it in items:
            if not isinstance(it, dict):
                continue
            pid = _norm_prop_id(it.get("id"))
            if not pid or pid not in result_map:
                continue

            cm = it.get("change_mode")
            cm = _clamp_enum(cm, VALID_CHANGE_MODE, default_change_mode)

            # default can be null / bool / int / float / string (keep as-is but avoid objects)
            dv = it.get("default", None)
            if isinstance(dv, (dict, list)):
                dv = None

            result_map[pid] = {"id": pid, "change_mode": cm, "default": dv}

        return [result_map[pid] for pid in requested_ids]

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[DEBUG] {self.name}: start", flush=True)

        all_ids = self._extract_property_ids(spec)

        # 0) Header (small prompt)
        header_raw = call_llm(self._build_header_prompt(spec), system=self.system_header, stream=False, temperature=0.0) or ""
        self._dump_raw("PLAN_HEADER_RAW.txt", header_raw)
        header = self._parse_header(header_raw, spec)

        # 1) Properties (chunked)
        # Use conservative chunk size; 25 is typically safe even for long ids.
        chunks = _split_chunks(all_ids, chunk_size=25)

        merged_items: List[Dict[str, Any]] = []
        for i, chunk in enumerate(chunks, start=1):
            prompt = self._build_props_prompt(spec, header, chunk)
            raw = call_llm(prompt, system=self.system_props, stream=False, temperature=0.0) or ""
            self._dump_raw(f"PLAN_PROPS_RAW_chunk{i:02d}.txt", raw)
            merged_items.extend(self._parse_props_items(raw, chunk, header["default_change_mode"]))

        plan: Dict[str, Any] = {
            "domain": header["domain"],
            "aosp_level": header["aosp_level"],
            "vendor": header["vendor"],
            "callback_policy": header["callback_policy"],
            "default_change_mode": header["default_change_mode"],
            "properties": merged_items,
        }

        # Final safety: if LLM totally failed, still return a non-empty plan with defaults
        if not plan["properties"] and all_ids:
            plan["properties"] = [{"id": pid, "change_mode": header["default_change_mode"], "default": None} for pid in all_ids]

        print(f"[DEBUG] {self.name}: done (properties in plan={len(plan['properties'])})", flush=True)
        return plan
