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
            "Output ONLY valid JSON. No explanations, no markdown, no additional text."
        )

    def build_prompt(self, spec_text: str) -> str:
        # Rough heuristic — count lines that look like properties
        approx_count = spec_text.count("\n") // 4 + 1

        return f"""\
Analyze the VSS-based vehicle specification below and group the properties into logical modules.

RULES:
- Group by **functional domain** (examples: HVAC, ADAS, BODY, CABIN, POWERTRAIN, CHASSIS, INFOTAINMENT, LIGHTING, DRIVETRAIN, SAFETY, EXTERIOR, INTERIOR, DIAGNOSTICS)
- Use **clear, uppercase, short module names** without prefixes (HVAC, BODY, ADAS, INFOTAINMENT, ...)
- Aim for **balanced sizes**: ideally 8–40 signals per module when total < 200
- When total signals ≤ 100, prefer **4–10 modules** (avoid >15 modules and avoid many 1–3 signal groups)
- Only put signals that truly don't fit anywhere else into "OTHER"
- Do NOT create dozens of tiny modules

OUTPUT FORMAT — strict JSON only:

{{
  "modules": {{
    "HVAC": ["Vehicle.Cabin.HVAC.LeftTemperature", ...],
    "BODY": ["Vehicle.Body.Door.FrontLeft.IsOpen", ...],
    ...
    "OTHER": [...]
  }},
  "summary": {{
    "total_properties": <integer>,
    "module_count": <integer>,
    "largest_module": "<name of module with most signals>"
  }}
}}

FULL SPEC (properties only):
{spec_text}

Output **only** the JSON object now. Nothing else.
""".strip()

    def run(self, spec_text: str) -> dict:
        print("[MODULE PLANNER] Analyzing spec and grouping into modules...")

        prompt = self.build_prompt(spec_text)

        try:
            raw = call_llm(
                prompt=prompt,
                system=self.system_prompt,
                temperature=0.0,
                response_format="json"
                # max_tokens removed — your call_llm does not support it
            )
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

        data, err = parse_json_object(raw.strip())
        if not data or "modules" not in data or not isinstance(data["modules"], dict):
            print("[MODULE PLANNER] Invalid response structure. Raw LLM output:")
            print(raw[:800] + "..." if len(raw) > 800 else raw)
            raise ValueError(f"Module planner did not return valid modules dict: {err or 'missing/invalid modules key'}")

        plan = data["modules"]
        summary = data.get("summary", {})

        # Basic validation & debug
        total_in_plan = sum(len(signals) for signals in plan.values())
        module_names = list(plan.keys())

        print(f"[MODULE PLANNER] Found {len(plan)} modules: {', '.join(module_names)}")
        print(f"[MODULE PLANNER] Signals grouped: {total_in_plan}")

        if summary:
            print(f"[MODULE PLANNER] Summary: {summary.get('total_properties')} signals → "
                  f"{summary.get('module_count')} modules (largest: {summary.get('largest_module')})")

        # Save plan
        out_path = Path("output/MODULE_PLAN.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[MODULE PLANNER] Wrote {out_path}")

        return plan  # { "HVAC": [...], "BODY": [...], ... }


def plan_modules_from_spec(spec_text: str) -> dict:
    return ModulePlannerAgent().run(spec_text)