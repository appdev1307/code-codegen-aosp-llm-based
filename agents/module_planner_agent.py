# agents/module_planner_agent.py
import json
import yaml
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

from llm_client import call_llm
from tools.json_contract import parse_json_object


class ModulePlannerAgent:
    def __init__(self, use_fast_mode: bool = False):
        """
        Args:
            use_fast_mode: If True, use domain metadata directly (instant, no LLM).
                          If False (default), use LLM-based planning (slower but more flexible).
        """
        self.use_fast_mode = use_fast_mode
        self.system_prompt = (
            "You are an expert Vehicle Signal Specification (VSS) and Android Automotive OS architect.\n"
            "Your only job is to group vehicle signals into logical, coherent modules for modular Vehicle HAL generation.\n"
            "You do NOT assign numbers or decide order — you only decide which signals belong to which functional module.\n"
            "Output ONLY valid JSON. No explanations, no markdown, no comments, no additional text."
        )

    def plan_from_metadata(self, spec_text: str) -> Dict[str, List[str]]:
        """
        FAST MODE: Extract module groupings directly from 'domain' metadata.
        This is instant and deterministic - no LLM needed.
        """
        print("[MODULE PLANNER] Using FAST MODE (metadata-based grouping)...")
        
        try:
            data = yaml.safe_load(spec_text)
            properties = data.get('properties', [])
            
            # Group by domain from metadata
            modules = defaultdict(list)
            for p in properties:
                name = p.get('name', 'UNKNOWN')
                domain = p.get('meta', {}).get('domain', 'OTHER')
                
                # Normalize domain name
                domain = str(domain).strip().upper()
                if not domain or domain == 'NONE':
                    domain = 'OTHER'
                
                modules[domain].append(name)
            
            # Convert to regular dict and sort
            sorted_modules = {k: v for k, v in sorted(modules.items())}
            
            # Create summary
            total = sum(len(v) for v in sorted_modules.values())
            largest = max(sorted_modules.items(), key=lambda x: len(x[1]))[0] if sorted_modules else 'NONE'
            
            summary = {
                'total_properties': total,
                'module_count': len(sorted_modules),
                'largest_module': largest,
                'method': 'metadata_based'
            }
            
            return sorted_modules, summary
            
        except Exception as e:
            print(f"[MODULE PLANNER] Fast mode failed ({e}), falling back to LLM mode")
            return None, None

    def extract_minimal_spec(self, spec_text: str) -> str:
        """
        Extract only essential fields (name, domain, vss_path) from the full YAML spec.
        This drastically reduces token count for the LLM.
        """
        try:
            data = yaml.safe_load(spec_text)
            properties = data.get('properties', [])
            
            # Extract minimal info for each property
            minimal_props = []
            for p in properties:
                name = p.get('name', 'UNKNOWN')
                domain = p.get('meta', {}).get('domain', 'OTHER')
                vss_path = p.get('meta', {}).get('vss_path', '')
                
                minimal_props.append(f"- {name} (domain: {domain}, path: {vss_path})")
            
            return '\n'.join(minimal_props)
        
        except Exception as e:
            print(f"[MODULE PLANNER] Warning: Could not extract minimal spec ({e}), using full spec")
            return spec_text

    def build_prompt(self, spec_text: str) -> str:
        minimal_spec = self.extract_minimal_spec(spec_text)
        approx_count = minimal_spec.count("\n") + 1

        return f"""\
Analyze the following VSS-derived vehicle properties and group them into logical modules.

STRICT RULES:
- Group strictly by **functional domain** (examples: HVAC, ADAS, BODY, CABIN, POWERTRAIN, CHASSIS, INFOTAINMENT, LIGHTING, DRIVETRAIN, SAFETY, EXTERIOR, INTERIOR, DIAGNOSTICS, OBSTACLE_DETECTION, ...)
- Use **short, clear, uppercase module names** without any prefixes or underscores (ADAS, CHASSIS, POWERTRAIN, ...)
- Aim for balanced module sizes: ideally 8–40 signals per module (when total < 200)
- When total signals ≤ 100, prefer 4–10 modules total – avoid >15 modules and avoid many tiny (1–3 signal) modules
- Put only signals that truly do not fit anywhere else into "OTHER"
- NEVER create dozens of tiny modules
- IMPORTANT: Use the **EXACT property names** as they appear in the spec below – do NOT change, shorten, normalize, reorder or rewrite them
- The order of signals inside each module DOES NOT MATTER and WILL NOT be used for numbering – grouping is the only purpose

OUTPUT FORMAT – strict JSON only:
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
    "total_properties": <integer>,
    "module_count": <integer>,
    "largest_module": "<name of module with most signals>"
  }}
}}

PROPERTY LIST (approximately {approx_count} signals):
{minimal_spec}

Output **only** the JSON object. Nothing else.
""".strip()

    def plan_with_llm(self, spec_text: str) -> tuple[Dict[str, List[str]], Dict]:
        """
        LLM MODE: Use LLM to plan modules (slower but more flexible).
        """
        print("[MODULE PLANNER] Using LLM MODE (AI-based grouping)...")
        
        prompt = self.build_prompt(spec_text)
        
        original_size = len(spec_text)
        prompt_size = len(prompt)
        print(f"[MODULE PLANNER] Optimized prompt: {prompt_size:,} chars (original: {original_size:,} chars, saved {original_size - prompt_size:,})")

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
        summary['method'] = 'llm_based'

        # Sort modules alphabetically
        sorted_modules = {k: v for k, v in sorted(raw_plan.items())}
        
        return sorted_modules, summary

    def run(self, spec_text: str) -> Dict[str, List[str]]:
        print("[MODULE PLANNER] Analyzing spec and grouping into modules...")

        # Try fast mode first if enabled
        if self.use_fast_mode:
            sorted_modules, summary = self.plan_from_metadata(spec_text)
            if sorted_modules is None:
                # Fallback to LLM
                sorted_modules, summary = self.plan_with_llm(spec_text)
        else:
            sorted_modules, summary = self.plan_with_llm(spec_text)

        # Validation & debug
        total_in_plan = sum(len(signals) for signals in sorted_modules.values())
        module_names = list(sorted_modules.keys())

        print(f"[MODULE PLANNER] Found {len(sorted_modules)} modules: {', '.join(module_names)}")
        print(f"[MODULE PLANNER] Signals grouped: {total_in_plan}")

        if summary:
            print(f"[MODULE PLANNER] Summary: {summary.get('total_properties')} signals → "
                  f"{summary.get('module_count')} modules (largest: {summary.get('largest_module')}) "
                  f"[method: {summary.get('method', 'unknown')}]")

        if total_in_plan != summary.get("total_properties", 0):
            print("[MODULE PLANNER] Warning: total signals in plan ≠ summary.total_properties")

        # Save plan
        out_path = Path("output/MODULE_PLAN.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        saved_data = {"modules": sorted_modules, "summary": summary}
        out_path.write_text(json.dumps(saved_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[MODULE PLANNER] Wrote {out_path}")

        return sorted_modules


def plan_modules_from_spec(spec_text: str, use_fast_mode: bool = False) -> Dict[str, List[str]]:
    """
    Returns a mapping: module_name → list of exact property names (strings)
    
    Args:
        spec_text: YAML specification text
        use_fast_mode: If True, use metadata-based grouping (instant).
                       If False (default), use LLM-based planning (slower but flexible).
    
    The order of signals inside each list is NOT meaningful and should not be used for ID assignment.
    Use property names ("VEHICLE_CHILDREN_...") as stable keys instead.
    """
    return ModulePlannerAgent(use_fast_mode=use_fast_mode).run(spec_text)