# agents/plan_agent.py
import json
from typing import Any, Dict, List

from llm_client import call_llm
from schemas.hal_spec import HalSpec


class PlanAgent:
    """
    Phase 1 (LLM): Produce a small, strict JSON 'plan' that guides deterministic emitters.
    This avoids asking LLM to output AOSP/Soong/AIDL-compliant files directly.
    """

    def __init__(self):
        self.name = "HAL Plan Agent"

    def build_prompt(self, spec: HalSpec) -> str:
        spec_text = spec.to_llm_spec()
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
- Do not invent properties not in the spec.
- If unsure: callback_policy="notify_on_change", default_change_mode="ON_CHANGE", property default=null.

Input spec:
{spec_text}
""".strip()

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[DEBUG] {self.name}: start", flush=True)
        raw = call_llm(self.build_prompt(spec))
        plan = json.loads(raw)

        # Minimal safety normalization to avoid cascading failures
        plan.setdefault("callback_policy", "notify_on_change")
        plan.setdefault("default_change_mode", "ON_CHANGE")
        plan.setdefault("properties", [])
        plan["domain"] = plan.get("domain") or spec.domain
        plan["aosp_level"] = int(plan.get("aosp_level") or spec.aosp_level)
        plan["vendor"] = plan.get("vendor") or spec.vendor

        print(f"[DEBUG] {self.name}: done (properties in plan={len(plan['properties'])})", flush=True)
        return plan
