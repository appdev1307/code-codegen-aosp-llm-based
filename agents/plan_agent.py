# FILE: agents/plan_agent.py

import json
from typing import Any, Dict, List, Tuple

from llm_client import call_llm
from schemas.hal_spec import HalSpec
from tools.json_contract import parse_json_object


class PlanAgent:
    """
    Phase 1 (LLM): Produce a strict JSON plan.
    Must NEVER drop spec properties: we force-include all IDs from spec after merge.
    """

    CALLBACK_POLICIES = {"notify_on_change", "notify_on_set"}
    CHANGE_MODES = {"ON_CHANGE", "CONTINUOUS", "STATIC"}

    def __init__(self):
        self.name = "HAL Plan Agent"
        self.system = (
            "Return STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"properties\": []}\n"
        )

    def _compact_properties(self, spec: HalSpec) -> List[str]:
        lines = []
        for p in spec.properties:
            pid = getattr(p, "id", "")
            typ = getattr(p, "type", "")
            acc = getattr(p, "access", "")
            areas = getattr(p, "areas", None) or []
            areas_s = ",".join([str(a).upper() for a in areas]) if isinstance(areas, list) else str(areas)
            lines.append(f"{pid}|{typ}|{acc}|{areas_s}")
        return lines

    def _build_header_prompt(self, spec: HalSpec) -> str:
        return f"""
You are an Android Automotive Vehicle HAL architect.

Return ONLY a single JSON object. No markdown. No explanations.

Schema:
{{
  "domain": "HVAC",
  "aosp_level": 14,
  "vendor": "AOSP",
  "callback_policy": "notify_on_change|notify_on_set",
  "default_change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
  "properties": [
    {{
      "id": "VSS_...",
      "change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
      "default": null
    }}
  ]
}}

Rules:
- Enums must match exactly (case-sensitive).
- Do not invent properties not in the input list.
- If unsure: callback_policy="notify_on_change", default_change_mode="ON_CHANGE", default=null.

Context:
- domain={spec.domain}
- aosp_level={spec.aosp_level}
- vendor={spec.vendor}

You will be given a chunk of properties as:
ID|TYPE|ACCESS|AREAS

Return JSON only.
""".strip()

    def _build_chunk_prompt(self, header: str, chunk_lines: List[str]) -> str:
        return f"""{header}

PROPERTIES (CHUNK):
{chr(10).join(chunk_lines)}

RETURN JSON NOW.
""".strip()

    def _dedup_keep_order(self, ids: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in ids:
            if x and x not in seen:
                out.append(x)
                seen.add(x)
        return out

    def _validate_header_enums(self, merged: Dict[str, Any], data: Dict[str, Any]) -> None:
        cp = (data.get("callback_policy") or "").strip()
        dcm = (data.get("default_change_mode") or "").strip()

        if cp in self.CALLBACK_POLICIES:
            merged["callback_policy"] = cp
        # else: keep fallback

        if dcm in self.CHANGE_MODES:
            merged["default_change_mode"] = dcm
        # else: keep fallback

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[DEBUG] {self.name}: start", flush=True)

        raw_ids = [(getattr(p, "id", "") or "").strip() for p in spec.properties]
        spec_ids = self._dedup_keep_order([x for x in raw_ids if x])

        lines = self._compact_properties(spec)
        header = self._build_header_prompt(spec)

        chunk_size = 60
        merged: Dict[str, Any] = {
            "domain": spec.domain,
            "aosp_level": int(spec.aosp_level),
            "vendor": spec.vendor,
            "callback_policy": "notify_on_change",
            "default_change_mode": "ON_CHANGE",
            "properties": [],
            # Optional diagnostics (safe to keep in PLAN.json or drop later)
            "diagnostics": {
                "chunks": 0,
                "parse_errors": 0,
                "hallucinated_ids": [],
                "invalid_change_modes": 0,
            },
        }

        spec_set = set(spec_ids)

        for idx in range(0, len(lines), chunk_size):
            chunk = lines[idx : idx + chunk_size]
            prompt = self._build_chunk_prompt(header, chunk)

            raw = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
            data, err = parse_json_object(raw)

            merged["diagnostics"]["chunks"] += 1
            if err or not data:
                merged["diagnostics"]["parse_errors"] += 1
                continue

            if idx == 0:
                self._validate_header_enums(merged, data)

            props = data.get("properties") or []
            if not isinstance(props, list):
                continue

            # Filter model props to spec ids only, track hallucinations and invalid change_mode
            for p in props:
                if not isinstance(p, dict):
                    continue
                pid = (p.get("id") or "").strip()
                if not pid:
                    continue
                if pid not in spec_set:
                    merged["diagnostics"]["hallucinated_ids"].append(pid)
                    continue
                cm = (p.get("change_mode") or "").strip()
                if cm and cm not in self.CHANGE_MODES:
                    merged["diagnostics"]["invalid_change_modes"] += 1
                merged["properties"].append(p)

        # Index what model returned (first occurrence wins)
        model_map: Dict[str, Dict[str, Any]] = {}
        for p in merged["properties"]:
            pid = (p.get("id") or "").strip()
            if not pid or pid in model_map:
                continue
            model_map[pid] = p

        # FORCE: include every spec id exactly once
        normalized: List[Dict[str, Any]] = []
        for pid in spec_ids:
            p = model_map.get(pid) or {}
            cm = (p.get("change_mode") or "").strip()
            normalized.append(
                {
                    "id": pid,
                    "change_mode": cm if cm in self.CHANGE_MODES else merged["default_change_mode"],
                    "default": p.get("default", None),
                }
            )

        merged["properties"] = normalized

        print(f"[DEBUG] {self.name}: done (properties in plan={len(merged['properties'])})", flush=True)
        return merged
