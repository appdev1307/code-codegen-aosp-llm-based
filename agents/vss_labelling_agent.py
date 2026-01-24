# agents/vss_labelling_agent.py
from pathlib import Path
import json
from llm_client import call_llm
from tools.safe_writer import SafeWriter

class VSSLabellingAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.labelled_path = Path(output_root) / "VSS_LABELLED.json"

    def run(self, vss_json_path: str):
        print("[LABELLING] Starting LLM-assisted labelling of VSS dataset...")

        with open(vss_json_path, "r", encoding="utf-8") as f:
            vss_data = json.load(f)

        labelled_data = {}

        total = len(vss_data)
        for idx, (path, signal) in enumerate(vss_data.items(), 1):
            print(f"[LABELLING] Processing {idx}/{total}: {path}")

            prompt = f"""
You are an expert automotive signal analyst.

Label the following VSS signal with structured metadata for HAL and app generation.

Signal path: {path}
Datatype: {signal.get("datatype", "unknown")}
Type: {signal.get("type", "unknown")}
Description: {signal.get("description", "none")}

Output ONLY valid JSON with these fields:
{{
  "domain": "ADAS|BODY|HVAC|CABIN|POWERTRAIN|CHASSIS|INFOTAINMENT|OTHER",
  "safety_level": "Critical|High|Medium|Low",
  "ui_widget": "Switch|Slider|Text|Gauge|Button|None",
  "ui_range_min": number or null,
  "ui_range_max": number or null,
  "ui_step": number or null,
  "ui_unit": "km/h" or "°C" or "%" or null,
  "aosp_standard": true|false,
  "legacy_org_id": string or null
}}

Choose values that make sense for real vehicle apps and HAL.
"""

            raw = call_llm(
                prompt=prompt,
                temperature=0.0,
                response_format="json"
            )

            try:
                labels = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            except Exception as e:
                print(f"  → JSON parse failed: {e}, using defaults")
                labels = {
                    "domain": "OTHER",
                    "safety_level": "Low",
                    "ui_widget": "Text",
                    "ui_range_min": None,
                    "ui_range_max": None,
                    "ui_step": None,
                    "ui_unit": None,
                    "aosp_standard": False,
                    "legacy_org_id": None
                }

            # Merge original signal with labels
            enhanced_signal = signal.copy()
            enhanced_signal["labels"] = labels
            enhanced_signal["normalized_id"] = path.upper().replace(".", "_")

            labelled_data[enhanced_signal["normalized_id"]] = enhanced_signal

        # Save labelled dataset
        self.labelled_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[LABELLING] Complete! Labelled dataset saved to {self.labelled_path}")
        print(f"    → {len(labelled_data)} signals enriched with domain, UI hints, safety, etc.")

        return labelled_data