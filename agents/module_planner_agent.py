# agents/module_planner_agent.py
import json
from pathlib import Path
from typing import Dict, List

from llm_client import call_llm
from tools.json_contract import parse_json_object


class ModulePlannerAgent:
    def __init__(self):
        self.system_prompt = (
            "You are an expert Vehicle Signal Specification (VSS) and Android Automotive OS architect.\n"
            "Your only job is to group vehicle signals into logical, coherent modules for modular Vehicle HAL generation.\n"
            "You do NOT assign numbers or decide order — you only decide which signals belong to which functional module.\n"
            "Output ONLY valid JSON. No explanations, no markdown, no comments, no additional text."
        )

    def build_prompt(self, spec_text: str) -> str:
        approx_count = spec_text.count("\n") // 4 + 1

        return f"""\
Analyze the following VSS-derived vehicle properties and group them into logical modules.

STRICT RULES:
- Group strictly by **functional domain** (examples: HVAC, ADAS, BODY, CABIN, POWERTRAIN, CHASSIS, INFOTAINMENT, LIGHTING, DRIVETRAIN, SAFETY, EXTERIOR, INTERIOR, DIAGNOSTICS, OBSTACLE_DETECTION, ...)
- Use **short, clear, uppercase module names** without any prefixes or underscores (ADAS, CHASSIS, POWERTRAIN, ...)
- Aim for balanced module sizes: ideally 8–40 signals per module (when total < 200)
- When total signals ≤ 100, prefer 4–10 modules total — avoid >15 modules and avoid many tiny (1–3 signal) modules
- Put only signals that truly do not fit anywhere else into "OTHER"
- NEVER create dozens of tiny modules
- IMPORTANT: Use the **EXACT property names** as they appear in the FULL SPEC below — do NOT change, shorten, normalize, reorder or rewrite them
- The order of signals inside each module DOES NOT MATTER and WILL NOT be used for numbering — grouping is the only purpose

OUTPUT FORMAT — strict JSON only:
{{
  "modules": {{
    "ADAS": [
      "VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED",
      "VEHICLE_CHILDREN_ADAS_CHILDREN_CRUISECONTROL_CHILDREN_SPEEDSET",
      ...
    ],
    "CHASSIS": [...],
    "OTHER": [...]
  }},
  "summary": {{
    "total_properties": <integer>,          // must equal number of unique names in all modules
    "module_count": <integer>,
    "largest_module": "<name of module with most signals>"
  }}
}}

FULL SPEC (properties only — use these exact names):
{spec_text}

Output **only** the JSON object. Nothing else.
""".strip()

    def run(self, spec_text: str) -> Dict[str, List[str]]:
        print("[MODULE PLANNER] Analyzing spec and grouping into modules...")

        prompt = self.build_prompt(spec_text)

        try:
            raw = call_llm(
                prompt=prompt,
                system=self.system_prompt,
                temperature=0.0,
                response_format="json"
            )
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

        data, err = parse_json_object(raw.strip())
        if not data or "modules" not in data or not isinstance(data["modules"], dict):
            print("[MODULE PLANNER] Invalid response structure. Raw LLM output:")
            print(raw[:800] + "..." if len(raw) > 800 else raw)
            raise ValueError(f"Module planner did not return valid modules dict: {err or 'missing/invalid modules key'}")

        raw_plan = data["modules"]
        summary = data.get("summary", {})

        # Post-process: sort module keys alphabetically for more deterministic downstream behavior
        # (order inside modules is explicitly NOT meaningful)
        sorted_modules = {k: v for k, v in sorted(raw_plan.items())}

        # Basic validation & debug
        total_in_plan = sum(len(signals) for signals in sorted_modules.values())
        module_names = list(sorted_modules.keys())

        print(f"[MODULE PLANNER] Found {len(sorted_modules)} modules: {', '.join(module_names)}")
        print(f"[MODULE PLANNER] Signals grouped: {total_in_plan}")

        if summary:
            print(f"[MODULE PLANNER] Summary: {summary.get('total_properties')} signals → "
                  f"{summary.get('module_count')} modules (largest: {summary.get('largest_module')})")

        if total_in_plan != summary.get("total_properties", 0):
            print("[MODULE PLANNER] Warning: total signals in plan ≠ summary.total_properties")

        # Save plan
        out_path = Path("output/MODULE_PLAN.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        saved_data = {"modules": sorted_modules, "summary": summary}
        out_path.write_text(json.dumps(saved_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[MODULE PLANNER] Wrote {out_path}")

        return sorted_modules  # { "ADAS": [names...], "CHASSIS": [...], ... }


def plan_modules_from_spec(spec_text: str) -> Dict[str, List[str]]:
    """
    Returns a mapping: module_name → list of exact property names (strings)
    The order of signals inside each list is NOT meaningful and should not be used for ID assignment.
    Use property names ("VEHICLE_CHILDREN_...") as stable keys instead.
    """
    return ModulePlannerAgent().run(spec_text)