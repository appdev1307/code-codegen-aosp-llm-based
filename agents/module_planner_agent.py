# agents/module_planner_agent.py - FIXED property extraction

import yaml
import json
from llm_client import call_llm

def extract_properties_from_yaml(yaml_text: str):
    """Extract all property names from YAML spec — reliable"""
    spec = yaml.safe_load(yaml_text)
    props = spec.get("properties", [])
    property_names = []
    for p in props:
        name = p.get("name") or p.get("Property ID")
        if name:
            property_names.append(str(name))
    return property_names

def plan_modules_from_spec(yaml_spec: str):
    print("[MODULE PLANNER] Analyzing full spec and grouping into modules...")

    property_names = extract_properties_from_yaml(yaml_spec)
    print(f"[MODULE PLANNER] Extracted {len(property_names)} properties from spec")

    if not property_names:
        print("[MODULE PLANNER] No properties found — aborting")
        return {}

    # Build prompt with ALL properties
    prop_list = "\n".join([f"- {name}" for name in property_names])

    prompt = f"""
You are an expert automotive software architect.

Group the following Vehicle HAL properties into meaningful modules (domains).

Properties ({len(property_names)} total):
{prop_list}

Rules:
- Common domains: ADAS, HVAC, BODY, CABIN, POWERTRAIN, CHASSIS, INFOTAINMENT, OBSTACLE_DETECTION, OTHER
- Group related properties together
- One module per domain
- If unsure, use OTHER

Output ONLY valid JSON:
{{
  "modules": {{
    "DOMAIN_NAME": ["PROPERTY_NAME_1", "PROPERTY_NAME_2", ...],
    ...
  }},
  "summary": {{
    "total_properties": {len(property_names)},
    "module_count": number_of_modules,
    "largest_module": "DOMAIN_WITH_MOST_PROPERTIES"
  }}
}}
"""

    raw = call_llm(prompt=prompt, temperature=0.0, response_format="json")

    try:
        plan = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception as e:
        print(f"[MODULE PLANNER] JSON parse failed: {e}")
        plan = {"modules": {"OTHER": property_names}, "summary": {"total_properties": len(property_names), "module_count": 1, "largest_module": "OTHER"}}

    # Save plan
    import pathlib
    pathlib.Path("output").mkdir(exist_ok=True)
    with open("output/MODULE_PLAN.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    print(f"[MODULE PLANNER] Found {len(plan['modules'])} modules")
    print(f"[MODULE PLANNER] Summary: {plan['summary']['total_properties']} signals → {plan['summary']['module_count']} modules")
    print("[MODULE PLANNER] Wrote output/MODULE_PLAN.json")

    return plan["modules"]