from pathlib import Path
import json
import asyncio
from tqdm import tqdm
from llm_client import call_llm
from tools.safe_writer import SafeWriter  # fixed missing import

def flatten_vss(vss_data, current_path=""):
    """Recursively flatten VSS tree to only leaf signals (properties)"""
    flat = {}
    for key, value in vss_data.items():
        full_path = f"{current_path}.{key}" if current_path else key
        if "datatype" in value and value.get("type") != "branch": # Leaf signal
            flat[full_path] = value
        elif isinstance(value, dict): # Branch — recurse
            flat.update(flatten_vss(value, full_path))
    return flat


class VSSLabellingAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.labelled_path = Path(output_root) / "VSS_LABELLED.json"

    def _build_batch_prompt(self, batch: list) -> str:
        lines = []
        for idx, (path, signal) in enumerate(batch, 1):
            lines.append(f"Signal {idx}:")
            lines.append(f"  Path: {path}")
            lines.append(f"  Datatype: {signal.get('datatype', 'unknown')}")
            lines.append(f"  Type: {signal.get('type', 'unknown')}")
            lines.append(f"  Description: {signal.get('description', 'none')}")
            lines.append("")

        prompt = f"""
You are an expert automotive signal analyst.
Label the following {len(batch)} VSS leaf signals.
Output ONLY a valid JSON array with EXACTLY {len(batch)} objects, in the same order as the signals listed.
Do NOT output a single object. Do NOT add explanations, fences (```), or any text outside the array.

Signals:
{'\n'.join(lines)}

Each object must be:
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

Response MUST be ONLY the array:
[{{"domain": "...", ...}}, ...]  // exactly {len(batch)} items, nothing else
"""
        return prompt

    async def _label_batch_async(self, batch: list, semaphore: asyncio.Semaphore):
        async with semaphore:
            prompt = self._build_batch_prompt(batch)
            labels_list = None
            for attempt in range(2):
                try:
                    raw = call_llm(prompt=prompt, temperature=0.0, response_format="json")
                    raw_clean = raw.strip()
                    # Aggressive cleaning
                    if raw_clean.startswith("```json"):
                        raw_clean = raw_clean.split("```json", 1)[1].strip()
                    if raw_clean.endswith("```"):
                        raw_clean = raw_clean.rsplit("```", 1)[0].strip()
                    parsed = json.loads(raw_clean)

                    # Handle single object case
                    if isinstance(parsed, dict):
                        parsed = [parsed]

                    if not isinstance(parsed, list):
                        raise ValueError("Not a list")

                    if len(parsed) != len(batch):
                        print(f"[INFO] Length mismatch ({len(parsed)} vs {len(batch)}) — replicating first label")
                        parsed += [parsed[0]] * (len(batch) - len(parsed))

                    labels_list = parsed
                    break
                except Exception as e:
                    print(f"[WARNING] Attempt {attempt+1}/2 failed: {e}")
                    if attempt == 1:
                        print("[WARNING] Retries failed — using defaults")
                        labels_list = [{
                            "domain": "OTHER",
                            "safety_level": "Low",
                            "ui_widget": "Text",
                            "ui_range_min": None,
                            "ui_range_max": None,
                            "ui_step": None,
                            "ui_unit": None,
                            "aosp_standard": False
                        }] * len(batch)

            results = []
            for (path, signal), labels in zip(batch, labels_list):
                enhanced = signal.copy()
                enhanced["labels"] = labels
                enhanced["normalized_id"] = path.upper().replace(".", "_")
                results.append((path, enhanced))
            return results

    def run_on_dict(self, signal_dict: dict):
        n = len(signal_dict)
        print(f"[LABELLING] Labelling {n} pre-selected signals (batched + async)...")

        batch_size = 4   # lowered for better LLM reliability
        max_concurrent = 5

        items = list(signal_dict.items())
        batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

        labelled_data = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_all():
            tasks = [self._label_batch_async(batch, semaphore) for batch in batches]
            return await asyncio.gather(*tasks, return_exceptions=True)

        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(run_all())

        pbar = tqdm(total=n, desc="Labelling signals", unit="signal", ncols=100)
        for batch_result in results:
            if isinstance(batch_result, Exception):
                print(f"[ERROR] Batch failed: {batch_result}")
                continue
            for path, enhanced in batch_result:
                labelled_data[enhanced["normalized_id"]] = enhanced
                pbar.update(1)

        pbar.close()
        print(f"[LABELLING] Done! {len(labelled_data)} labelled signals ready")
        return labelled_data

    # ... (rest of your file unchanged: _label_single_signal and run methods)