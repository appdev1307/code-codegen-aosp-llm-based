# FILE: agents/vss_labelling_agent.py
# With tqdm progress bar + run_on_dict for fast testing

from pathlib import Path
import json
from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tqdm import tqdm  # Clean progress bar


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
        """Full labelling from file path (original method)"""
        print("[LABELLING] Starting LLM-assisted labelling of VSS dataset...")
        with open(vss_json_path, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)

        leaf_signals = flatten_vss(raw_vss)
        total = len(leaf_signals)
        print(f"[LABELLING] Found {total} leaf signals — labelling in progress...")

        labelled_data = {}

        for path, signal in tqdm(leaf_signals.items(),
                                 desc="Labelling VSS signals",
                                 unit="signal",
                                 ncols=100,
                                 bar_format="{l_bar}{bar} | {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
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
            except Exception:
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

        # Save result
        self.labelled_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False))
        print(f"\n[LABELLING] Complete! {len(labelled_data)} signals labelled and saved to {self.labelled_path}")
        return labelled_data

    def run_on_dict(self, signal_dict: dict):
        """Fast labelling on pre-limited dict (e.g., 50 signals)"""
        print(f"[LABELLING] Labelling {len(signal_dict)} pre-selected signals...")

        labelled_data = {}

        for path, signal in tqdm(signal_dict.items(),
                                 desc="Labelling signals",
                                 unit="signal",
                                 ncols=100,
                                 bar_format="{l_bar}{bar} | {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
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
            except Exception:
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

        print(f"[LABELLING] Done! {len(labelled_data)} labelled signals ready")
        return labelled_data