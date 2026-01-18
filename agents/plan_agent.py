# FILE: agents/plan_agent.py
import json
from typing import Any, Dict, List

from llm_client import call_llm
from schemas.hal_spec import HalSpec
from tools.json_contract import parse_json_object


class PlanAgent:
    """
    Phase 1 (LLM): Produce a strict JSON plan.
    Must NEVER drop spec properties: we force-include all IDs from spec after merge.
    """

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

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[DEBUG] {self.name}: start", flush=True)

        spec_ids = [(getattr(p, "id", "") or "").strip() for p in spec.properties]
        spec_ids = [x for x in spec_ids if x]  # remove empties, preserve order

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
        }

        # Chunk calls
        for idx in range(0, len(lines), chunk_size):
            chunk = lines[idx : idx + chunk_size]
            prompt = self._build_chunk_prompt(header, chunk)

            raw = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
            data, err = parse_json_object(raw)

            if not err and data:
                if idx == 0:
                    merged["callback_policy"] = data.get("callback_policy") or merged["callback_policy"]
                    merged["default_change_mode"] = data.get("default_change_mode") or merged["default_change_mode"]

                props = data.get("properties") or []
                if isinstance(props, list):
                    merged["properties"].extend([p for p in props if isinstance(p, dict)])

        # Index what model returned
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
            normalized.append(
                {
                    "id": pid,
                    "change_mode": p.get("change_mode") or merged["default_change_mode"],
                    "default": p.get("default", None),
                }
            )

        merged["properties"] = normalized

        print(f"[DEBUG] {self.name}: done (properties in plan={len(merged['properties'])})", flush=True)
        return merged
