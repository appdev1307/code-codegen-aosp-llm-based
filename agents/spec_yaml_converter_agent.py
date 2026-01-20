# FILE: agents/spec_yaml_converter_agent.py
from pathlib import Path
from typing import Optional
import yaml  # pip install pyyaml
from llm_client import call_llm


class SpecYamlConverterAgent:
    def __init__(self, output_root: str = "output", max_retries: int = 3):
        self.name = "Spec YAML Converter Agent"
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries

    def build_prompt(self, simple_spec_text: str) -> str:
        return f"""
You are an expert automotive specification engineer specializing in Android Automotive OS (AAOS) HAL specifications.

TASK:
Convert the provided "Simple Spec" text into a strict, valid YAML specification format (version 1.1).

ABSOLUTE RULES:
- Output ONLY valid YAML. No explanations, no markdown, no code fences, no apologies.
- First line must be: spec_version: 1.1
- Use exactly 2-space indentation.
- Must be parsable by PyYAML safe_load().
- Top-level keys MUST be exactly: spec_version, product, target, features, properties
- properties must be a list (can be empty)

DEFAULT VALUES (apply when not specified):
- product.vendor: "AOSP"
- product.android: "AAOS_14"
- target.module: infer from content (e.g., HVAC, ADAS, BODY, CABIN); default "HVAC"
- target.domains: [vehicle_hal, car_service, sepolicy]
- features: create one: name: generated_from_vss, description: "Automatically generated from VSS input"

For each property:
- name: required (string, snake_case or as given)
- type: INT | FLOAT | BOOLEAN | STRING
- access: READ | WRITE | READ_WRITE
- areas: list of strings; default ["GLOBAL"] if missing
- aosp.standard: true only if it's a known official AOSP property; otherwise false
- aosp.kind: system or vendor
- aosp.vendor_namespace: required only if standard=false
- sdv section: optional — omit unless clearly specified

Simple Spec Input:
{simple_spec_text.strip()}

OUTPUT ONLY THE YAML NOW:
""".strip()

    def _validate_yaml(self, yaml_text: str) -> Optional[str]:
        if not yaml_text or not yaml_text.strip():
            return "Empty output"

        stripped = yaml_text.strip()
        if stripped.lower().startswith(("i'm sorry", "i cannot", "as an ai", "here is", "```")):
            return "Model returned non-YAML text (apology, markdown, or explanation)"

        try:
            data = yaml.safe_load(stripped)
        except yaml.YAMLError as e:
            return f"Invalid YAML syntax: {e}"

        if not isinstance(data, dict):
            return "Top-level is not a mapping"

        required_keys = {"spec_version", "product", "target", "features", "properties"}
        missing = required_keys - data.keys()
        if missing:
            return f"Missing required keys: {sorted(missing)}"

        if data.get("spec_version") not in (1.1, "1.1"):
            return f"Invalid spec_version: {data.get('spec_version')} (must be 1.1)"

        if not isinstance(data.get("properties"), list):
            return "properties must be a list"

        if not isinstance(data.get("features"), list) or not data["features"]:
            return "features must be a non-empty list"

        return None  # Valid!

    def run(self, simple_spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start")

        base_prompt = self.build_prompt(simple_spec_text)

        for attempt in range(1, self.max_retries + 2):
            print(f"[DEBUG] {self.name}: attempt {attempt}/{self.max_retries + 1}")

            # Use JSON mode + strict temperature for maximum structure
            yaml_text = call_llm(
                prompt=base_prompt,
                system="You are a precise YAML generator. Output only valid YAML.",
                temperature=0.0,
                response_format="json",  # Helps enforce structure even for YAML
            ).strip()

            # Dump raw attempt
            attempt_path = self.output_root / f"SPEC_NORMALIZED_attempt_{attempt}.yaml"
            attempt_path.write_text(yaml_text, encoding="utf-8")

            err = self._validate_yaml(yaml_text)
            if err is None:
                final_path = self.output_root / "SPEC_NORMALIZED.yaml"
                final_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
                print(f"[DEBUG] {self.name}: SUCCESS → wrote {final_path}")
                print(f"[DEBUG] {self.name}: done")
                return yaml_text

            print(f"[WARN] {self.name}: attempt {attempt} failed → {err}")

            # Repair prompt for next attempt
            base_prompt += f"\n\nPREVIOUS ATTEMPT WAS INVALID YAML.\n" \
                           f"ERROR: {err}\n" \
                           "FIX ALL ISSUES NOW.\n" \
                           "Output ONLY valid YAML. No text before or after."

        # Final failure
        raise RuntimeError(
            f"[ERROR] {self.name} failed after {self.max_retries + 1} attempts.\n"
            f"Check dumped files in: {self.output_root}/SPEC_NORMALIZED_attempt_*.yaml"
        )