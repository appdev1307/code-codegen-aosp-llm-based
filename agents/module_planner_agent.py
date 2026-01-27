# agents/module_planner_agent.py
import json
from pathlib import Path
from llm_client import call_llm
from tools.json_contract import parse_json_object

class ModulePlannerAgent:
    def __init__(self):
        self.system_prompt = (
            "You are an expert Vehicle Signal Specification (VSS) and Android Automotive OS architect.\n"
            "Your job is to group vehicle signals into logical, coherent modules suitable for modular Vehicle HAL generation.\n"
            "Output ONLY valid JSON. No explanations, no markdown."
        )

    def build_prompt(self, spec_text: str) -> str:
        return f"""
Analyze the full VSS-based vehicle specification below and group the properties into logical modules.

RULES:
- Group by functional domain (e.g., HVAC, ADAS, Body, Cabin, Powertrain, Chassis, Infotainment, etc.)
- Use meaningful, uppercase module names (e.g., HVAC, BODY, CABIN, ADAS)
- Put ambiguous or miscellaneous signals in "OTHER"
- Aim for 20–80 signals per module (ideal for HAL generation)
- Do NOT split too finely (avoid 100+ tiny modules)

OUTPUT FORMAT (STRICT JSON ONLY):
{{
  "modules": {{
    "MODULE_NAME": [
      "VSS_VEHICLE_ADAS_CRUISECONTROL_SPEEDSET",
      "VSS_VEHICLE_ADAS_ABS_ISENABLED",
      ...
    ],
    "OTHER": [...]
  }},
  "summary": {{
    "total_properties": 200,
    "module_count": 6,
    "largest_module": "BODY"
  }}
}}

FULL SPEC:
{spec_text}

OUTPUT ONLY THE JSON NOW:
""".strip()

    def run(self, spec_text: str) -> dict:
        print("[MODULE PLANNER] Analyzing full spec and grouping into modules...")

        prompt = self.build_prompt(spec_text)
        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json"
        )

        data, err = parse_json_object(raw.strip())
        if not data or "modules" not in data:
            raise ValueError(f"Module planner failed to return valid JSON: {err or 'No modules key'}")

        plan = data["modules"]
        summary = data.get("summary", {})

        print(f"[MODULE PLANNER] Found {len(plan)} modules: {', '.join(plan.keys())}")
        if summary:
            print(f"[MODULE PLANNER] Summary: {summary.get('total_properties')} signals → {summary.get('module_count')} modules")

        # Save plan
        out_path = Path("output/MODULE_PLAN.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[MODULE PLANNER] Wrote {out_path}")

        return plan  # { "HVAC": [...ids], "BODY": [...], ... }

def plan_modules_from_spec(spec_text: str) -> dict:
    return ModulePlannerAgent().run(spec_text)