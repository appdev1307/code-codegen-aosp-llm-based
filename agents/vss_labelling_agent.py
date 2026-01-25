# agents/vss_labelling_agent.py - FIXED VERSION
from pathlib import Path
import json
from llm_client import call_llm
from tools.safe_writer import SafeWriter

def flatten_vss(vss_data, current_path=""):
    """Recursively flatten VSS tree to only leaf signals (properties)"""
    flat = {}
    for key, value in vss_data.items():
        full_path = f"{current_path}.{key}" if current_path else key
        if "datatype" in value and value.get("type") != "branch":  # Leaf signal
            flat[full_path] = value
        elif isinstance(value, dict):  # Branch — recurse
            flat.update(flatten_vss(value, full_path))
    return flat

class VSSLabellingAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.labelled_path = Path(output_root) / "VSS_LABELLED.json"

    def run(self, vss_json_path: str):
        print("[LABELLING] Starting LLM-assisted labelling of VSS dataset...")

        with open(vss_json_path, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)

        # Flatten to only leaf signals
        leaf_signals = flatten_vss(raw_vss)
        print(f"[LABELLING] Extracted {len(leaf_signals)} leaf signals from VSS tree")

        labelled_data = {}

        total = len(leaf_signals)
        for idx, (path, signal) in enumerate(leaf_signals.items(), 1):
            print(f"[LABELLING] Processing {idx}/{total}: {path}")

            prompt = f"""
You are an expert automotive signal analyst.

Label this VSS leaf signal:

Path: {path}
Datatype: {signal.get("datatype", "unknown")}
Type: {signal.get("type", "unknown")}
Description: {signal.get("description", "none")}

Output ONLY valid JSON:
{{
  "domain": "ADAS|BODY|HVAC|CABIN|POWERTRAIN|CHASSIS|INFOTAINMENT|OTHER",
  "safety_level": "Critical|High|Medium|Low",
  "ui_widget": "Switch|Slider|Text|Gauge|Button|None",
  "ui_range_min": number or null,
  "ui_range_max": number or null,
  "ui_step": number or null,
  "ui_unit": string or null,
  "aosp_standard": true|false
}}

Choose realistic values.
"""

            raw = call_llm(prompt=prompt, temperature=0.0, response_format="json")

            try:
                labels = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            except:
                labels = {
                    "domain": "OTHER",
                    "safety_level": "Low",
                    "ui_widget": "Text",
                    "ui_range_min": None,
                    "ui_range_max": None,
                    "ui_step": None,
                    "ui_unit": None,
                    "aosp_standard": False
                }

            enhanced = signal.copy()
            enhanced["labels"] = labels
            enhanced["normalized_id"] = path.upper().replace(".", "_")

            labelled_data[enhanced["normalized_id"]] = enhanced

        self.labelled_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False))
        print(f"[LABELLING] Complete! {len(labelled_data)} labelled leaf signals saved")

        return labelled_data